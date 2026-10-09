"""Submit one structured proposal to the signed-in Codex CLI in a cancellable child process.

Codex is a coding agent, not a plain model call, so this worker holds it to the contract every draft
provider keeps (`AGENTS.md`): the prompt goes in, one JSON object matching the schema comes out, and the
model has no filesystem or shell tools. `codex exec` has no switch that removes every tool, so three
layers apply:

1. it starts with the tool-bearing features off, a read-only sandbox, no user config (so no MCP servers,
   hooks or plugins), no saved session, and an empty folder as its working directory;
2. it uses the ChatGPT subscription login only: API-key environment variables are removed and a Codex
   signed in with an API key is refused, so no API key is read or billed;
3. it reads Codex's event stream while the model answers and fails the draft at the first event that is not
   the model thinking or answering, stopping Codex before it can act on what it asked for.

The worker prints the same envelope as the OpenAI worker: the draft as `structured_output` and the tokens
Codex reported as `usage` (a subscription states no price). The server validates the draft before review.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # app modules
import draft_schemas
import storage
from job_service import SubprocessProcess

MAX_PROMPT = 8 * 1024 * 1024
MAX_ANSWER = 8 * 1024 * 1024
TIMEOUT = 30 * 60  # seconds; a draft that takes longer is stopped rather than holding the lane
STALE_AFTER = 24 * 3600  # scratch folders of draft runs that were cancelled are removed after a day
SCRATCH = 'campaign-studio-codex-'
# Items Codex may stream while it answers. Anything else (a command, a file change, an MCP or web call, a
# sub-agent) is the model reaching for a tool, which a draft never has.
ALLOWED_ITEMS = {'agent_message', 'reasoning', 'todo_list', 'error'}
# Features that give the model a tool or a reach beyond the prompt. Only names this Codex lists are passed,
# because it refuses an unknown feature.
OFF_FEATURES = (
    'shell_tool',
    'unified_exec',
    'view_image',
    'image_generation',
    'multi_agent',
    'multi_agent_v2',
    'apps',
    'plugins',
    'remote_plugin',
    'skill_search',
    'tool_suggest',
    'browser_use',
    'browser_use_external',
    'computer_use',
    'in_app_browser',
    'hooks',
)
# Config overrides (TOML, or the raw string when it is not TOML). An unknown key is ignored by Codex.
CONFIG = (
    'web_search=disabled',
    'tools.view_image=false',
    'agents.enabled=false',
    'approval_policy=never',
)
# What would make Codex bill an API key instead of the subscription.
KEY_ENV = ('OPENAI_API_KEY', 'CODEX_API_KEY')
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


class CodexError(ValueError):
    """A draft that Codex could not or must not produce. The message is shown to the GM."""


def child_env():
    env = {key: value for key, value in os.environ.items() if key not in KEY_ENV}
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def run_quietly(codex, args, env, timeout=30):
    """(exit code, output) of a short Codex command such as `login status`."""
    try:
        done = subprocess.run(
            list(codex) + args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CodexError(f'Codex did not answer `{" ".join(args)}`: {error}') from error
    return done.returncode, (done.stdout + done.stderr).strip()


def require_subscription_login(codex, env):
    """Refuse to draft unless Codex is signed in with a ChatGPT account (the subscription)."""
    code, text = run_quietly(codex, ['login', 'status'], env)
    if code != 0 or 'api key' in text.lower():
        raise CodexError(
            'Sign in to Codex with your ChatGPT account (run `codex login`); Studio does not use '
            'an API key. Codex said: ' + (text[:200] or 'nothing')
        )


def features_to_disable(codex, env):
    """The OFF_FEATURES this Codex knows. Its feature table lists a name first on each line."""
    code, text = run_quietly(codex, ['features', 'list'], env)
    known = {line.split()[0] for line in text.splitlines() if line.split()} if code == 0 else set()
    if 'shell_tool' not in known:
        # The list is unreadable (it has changed shape, or failed): turn every feature off and let Codex
        # itself refuse any name it does not know, rather than quietly turning none off.
        return list(OFF_FEATURES)
    return [feature for feature in OFF_FEATURES if feature in known]


def command_line(codex, schema_path, workdir, model, features):
    """The `codex exec` command: events as JSON lines, the prompt on standard input."""
    command = list(codex) + [
        'exec',
        '--json',
        '--ephemeral',
        '--ignore-user-config',
        '--ignore-rules',
        '--skip-git-repo-check',
        '--sandbox',
        'read-only',
        '--cd',
        workdir,
        '--output-schema',
        schema_path,
    ]
    for feature in features:
        command += ['--disable', feature]
    for override in CONFIG:
        command += ['-c', override]
    if model:
        command.append('--model=' + model)
    return command


def sweep_stale(root=None, now=None):
    """Remove scratch folders that a cancelled draft could not clean up (it is stopped without warning)."""
    root = root or tempfile.gettempdir()
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if (
                name.startswith(SCRATCH)
                and (now or time.time()) - os.path.getmtime(path) > STALE_AFTER
            ):
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass  # another worker removed it first


class Answer:
    """What the event stream says: the model's final message, the tokens used and why it failed."""

    def __init__(self):
        self.text = ''
        self.failure = ''
        # Lines that were not events, kept for the error when Codex exits badly.
        self.tail = deque(maxlen=8)
        self.used = {'input_tokens': 0, 'output_tokens': 0, 'cache_read_input_tokens': 0}

    def add(self, line):
        """Take one line of Codex output. A line that is an event is read; any other is kept as a clue."""
        try:
            event = json.loads(line)
        except ValueError:
            self.tail.append(line.strip()[:300])
            return
        if not isinstance(event, dict):
            return
        kind = str(event.get('type') or '')
        if kind.startswith('item.'):
            item = event.get('item') if isinstance(event.get('item'), dict) else {}
            item_type = item.get('type') or item.get('item_type')  # older Codex names it item_type
            if item_type not in ALLOWED_ITEMS:
                raise CodexError(
                    f'Codex tried to use a tool ({str(item_type)[:40]}). A draft has no tools, so it '
                    'was stopped before the tool could act.'
                )
            if kind == 'item.completed' and item_type == 'agent_message':
                self.text = str(item.get('text') or '')
        elif kind == 'turn.completed':
            reported = event.get('usage') if isinstance(event.get('usage'), dict) else {}
            for ours, theirs in (
                ('input_tokens', 'input_tokens'),
                ('output_tokens', 'output_tokens'),
                ('cache_read_input_tokens', 'cached_input_tokens'),
            ):
                value = reported.get(theirs)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    self.used[ours] += value
        elif kind == 'turn.failed':
            error = event.get('error')
            self.failure = str(error.get('message') if isinstance(error, dict) else error or '')
        elif kind == 'error':
            self.tail.append(str(event.get('message') or '')[:300])

    def draft(self):
        if self.failure:
            raise CodexError(self.failure[:500])
        if not self.text:
            raise CodexError(
                'Codex returned no answer.'
                + (' It said: ' + ' | '.join(self.tail) if self.tail else '')
            )
        if len(self.text) > MAX_ANSWER:
            raise CodexError('Codex returned an answer larger than 8 MB.')
        try:
            draft = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', self.text.strip()))
        except ValueError as error:
            raise CodexError('Codex did not return JSON.') from error
        if not isinstance(draft, dict):
            raise CodexError('Codex returned a proposal that is not an object.')
        return draft


def feed(process, prompt):
    """Send the prompt on a thread, so a large one cannot block Codex's own output."""

    def write():
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except OSError:
            pass  # Codex stopped reading: its exit says why

    threading.Thread(target=write, daemon=True).start()


def run(command, prompt, env, answer):
    """Run Codex and feed `answer` its output. Stops Codex on a forbidden event or when it takes too long."""
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=env,
        creationflags=NO_WINDOW,
    ) as process:
        expired = threading.Event()

        def give_up():
            expired.set()
            SubprocessProcess(process).terminate()

        timer = threading.Timer(TIMEOUT, give_up)
        timer.start()
        try:
            feed(process, prompt)
            for line in process.stdout:
                answer.add(line)
        except CodexError:
            SubprocessProcess(process).terminate()
            raise
        finally:
            timer.cancel()
        code = process.wait()
    if expired.is_set():
        raise CodexError('Codex took too long and was stopped.')
    if code != 0 and not answer.failure:
        clue = ' | '.join(answer.tail)
        raise CodexError(
            f'Codex exited with status {code}.' + (' It said: ' + clue if clue else '')
        )


def generate(kind, codex, model, prompt):
    """Draft one proposal of `kind`. `codex` is the command that starts Codex, as a list. Returns (draft, usage)."""
    if len(prompt) > MAX_PROMPT:
        raise CodexError('The structured draft prompt is too large.')
    schema = draft_schemas.for_kind(kind)
    env = child_env()
    require_subscription_login(codex, env)
    features = features_to_disable(codex, env)
    sweep_stale()
    scratch = tempfile.mkdtemp(prefix=SCRATCH)
    try:
        workdir = os.path.join(scratch, 'empty')
        os.mkdir(workdir)  # Codex starts here, so the only files it could see are none
        schema_path = os.path.join(scratch, 'schema.json')
        storage.atomic_json(schema_path, schema)
        answer = Answer()
        run(command_line(codex, schema_path, workdir, model, features), prompt, env, answer)
        return answer.draft(), answer.used
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main():
    if len(sys.argv) not in (3, 4):
        raise CodexError('Expected draft kind, the codex command and an optional model.')
    kind, codex = sys.argv[1:3]
    model = sys.argv[3] if len(sys.argv) == 4 else ''
    prompt = sys.stdin.read(MAX_PROMPT + 1)
    draft, used = generate(kind, [codex], model, prompt)
    print(json.dumps({'structured_output': draft, 'usage': used}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        sys.exit('Codex drafting failed: ' + str(error))
