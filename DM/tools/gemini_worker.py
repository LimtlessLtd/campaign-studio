"""Submit one structured proposal to the signed-in Gemini CLI in a cancellable child process.

Gemini CLI is a coding agent, not a plain model call, so this worker holds it to the contract every draft
provider keeps (`AGENTS.md`): the prompt goes in, one JSON object matching the schema comes out, and the
model has no filesystem or shell tools. Gemini has no switch that removes every tool, so three layers apply:

1. it starts headless in plan (read-only) approval mode, with a policy file of this worker's own that
   denies every built-in and MCP tool (`--policy` replaces the user's policy folder, so no user rule can
   allow one), no extensions, no MCP servers, and an empty folder as its workspace (trusted for this run
   only). Campaign text cannot pull files in: every `@` in the prompt is escaped, and the prompt starts
   with a fixed line, so it is never read as a slash command;
2. it uses the Google sign-in only (a Google AI Pro or other Gemini subscription): the API-key, Vertex AI
   and gateway variables are removed from its environment, the Google login is selected for it, and a
   Gemini whose settings choose another sign-in is refused, so no API key is read or billed;
3. it reads Gemini's `stream-json` events while the model answers and fails the draft at the first event
   that is not the model starting, answering or reporting (a `tool_use`, or anything unknown), stopping
   Gemini before the tool can run; a result that still reports a tool call is refused.

Gemini has no schema switch, so the strict schema is appended to the prompt; the server validates the
draft against it before review, as for every provider. The worker prints the same envelope as the other
workers: the draft as `structured_output` and the tokens Gemini reported as `usage` (a subscription states
no price).
"""

import json
import os
import re
import shutil
import sys
import tempfile
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # app modules
import cli_draft
import draft_schemas

# Gemini reads at most 8 MB from standard input and cuts the rest, which would cut the schema off.
MAX_PROMPT = 8 * 1024 * 1024
TIMEOUT = 30 * 60  # seconds; a draft that takes longer is stopped rather than holding the lane
SCRATCH = 'campaign-studio-gemini-'
# Events Gemini may stream while it answers. Anything else (`tool_use`, `tool_result`, a future kind) is
# the model reaching for a tool, which a draft never has.
ALLOWED_EVENTS = {'init', 'message', 'error', 'result'}
# What would make Gemini bill an API key, a cloud project or a gateway instead of the Google sign-in.
KEY_ENV = (
    'GEMINI_API_KEY',
    'GOOGLE_API_KEY',
    'GOOGLE_GENAI_USE_VERTEXAI',
    'GOOGLE_APPLICATION_CREDENTIALS',
    'GOOGLE_GEMINI_BASE_URL',
    'GEMINI_CLI_USE_COMPUTE_ADC',
    'CLOUD_SHELL',
)
GOOGLE_LOGIN = 'oauth-personal'  # Gemini's name for "Sign in with Google"
SIGN_IN = 'run `gemini` in a terminal once and choose Sign in with Google'
NO_MCP = 'campaign-studio-no-mcp-servers'  # allowing only a server that does not exist allows none
HEADER = 'Campaign Studio structured draft. Answer with JSON only.\n\n'
ASK = 'Reply with only the one JSON object described above.'
POLICY = """# Campaign Studio drafts have no tools: every built-in and MCP tool is denied.
[[rule]]
toolName = "*"
decision = "deny"
priority = 999
denyMessage = "Campaign Studio drafts have no tools. Reply with the JSON object only."

[[rule]]
toolName = "*"
mcpName = "*"
decision = "deny"
priority = 999
denyMessage = "Campaign Studio drafts have no tools. Reply with the JSON object only."
"""
SELECTED = re.compile(r'"(?:selectedType|enforcedType)"\s*:\s*"([^"]*)"')
UNESCAPED_AT = re.compile(r'(?<!\\)@')


class GeminiError(cli_draft.DraftError):
    """A draft that Gemini could not or must not produce. The message is shown to the GM."""


def child_env():
    env = {key: value for key, value in os.environ.items() if key not in KEY_ENV}
    env['GOOGLE_GENAI_USE_GCA'] = 'true'  # the Google sign-in, when the settings name none
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def settings_files(env):
    """Gemini's user and system settings files, where a saved sign-in choice lives."""
    home = env.get('GEMINI_CLI_HOME') or os.path.expanduser('~')
    if env.get('GEMINI_CLI_SYSTEM_SETTINGS_PATH'):
        system = env['GEMINI_CLI_SYSTEM_SETTINGS_PATH']
    elif os.name == 'nt':
        system = os.path.join(
            env.get('ProgramData', r'C:\ProgramData'), 'gemini-cli', 'settings.json'
        )
    elif sys.platform == 'darwin':
        system = '/Library/Application Support/GeminiCli/settings.json'
    else:
        system = '/etc/gemini-cli/settings.json'
    return [os.path.join(home, '.gemini', 'settings.json'), system]


def require_google_login(env):
    """Refuse to draft when Gemini's settings choose a sign-in other than the Google account.

    The settings may hold comments, so the sign-in choice is found by its key rather than parsed as JSON.
    """
    for path in settings_files(env):
        try:
            with open(path, encoding='utf-8', errors='replace') as file:
                chosen = SELECTED.findall(file.read(1024 * 1024))
        except OSError:
            continue  # no settings there
        other = [value for value in chosen if value != GOOGLE_LOGIN]
        if other:
            raise GeminiError(
                f'Gemini is set to sign in with "{other[0][:40]}". Studio drafts only with your Google '
                f'account, never an API key: {SIGN_IN} (or `/auth` inside Gemini).'
            )


def full_prompt(prompt, schema):
    """What Gemini reads on standard input: the prompt, then the schema it has no switch for."""
    text = (
        HEADER
        + prompt
        + '\n\nReturn exactly one JSON object that matches this JSON schema (every property is '
        'required; add no others):\n' + json.dumps(schema, ensure_ascii=False)
    )
    return UNESCAPED_AT.sub(r'\\@', text)


def command_line(gemini, policy_path, model):
    """The headless `gemini` command: events as JSON lines, the prompt on standard input."""
    command = list(gemini) + [
        '--prompt=' + ASK,
        '--output-format=stream-json',
        '--approval-mode=plan',
        '--policy=' + policy_path,
        '--extensions=none',
        '--allowed-mcp-server-names=' + NO_MCP,
        '--skip-trust',
    ]
    if model:
        command.append('--model=' + model)
    return command


def sweep_stale(root=None, now=None):
    """Remove Gemini scratch folders that a cancelled draft could not clean up."""
    cli_draft.sweep_stale(SCRATCH, root, now)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


class Answer:
    """What the event stream says: the model's answer, the tokens used and why it failed."""

    def __init__(self):
        self.text = ''
        self.failure = ''
        self.finished = False
        # Warnings, errors and lines that were not events, kept for the error when Gemini fails.
        self.tail = deque(maxlen=8)
        self.used = {'input_tokens': 0, 'output_tokens': 0, 'cache_read_input_tokens': 0}

    def add(self, line):
        """Take one line of Gemini output. A line that is an event is read; any other is kept as a clue."""
        try:
            event = json.loads(line)
        except ValueError:
            if line.strip():
                self.tail.append(line.strip()[:300])
            return
        if not isinstance(event, dict):
            return
        kind = event.get('type')
        if kind not in ALLOWED_EVENTS:
            name = event.get('tool_name') or kind
            raise GeminiError(
                f'Gemini tried to use a tool ({str(name)[:40]}). A draft has no tools, so it was '
                'stopped before the tool could act.'
            )
        if kind == 'message' and event.get('role') == 'assistant':
            content = str(event.get('content') or '')
            self.text = self.text + content if event.get('delta') else content
        elif kind == 'error':
            self.tail.append(str(event.get('message') or '')[:300])
        elif kind == 'result':
            self.finished = True
            stats = event.get('stats') if isinstance(event.get('stats'), dict) else {}
            if _count(stats.get('tool_calls')):
                raise GeminiError('Gemini reported a tool call. A draft has no tools.')
            for ours, theirs in (
                ('input_tokens', 'input_tokens'),
                ('output_tokens', 'output_tokens'),
                ('cache_read_input_tokens', 'cached'),
            ):
                self.used[ours] += _count(stats.get(theirs))
            if event.get('status') != 'success':
                error = event.get('error')
                message = error.get('message') if isinstance(error, dict) else error
                self.failure = str(message or ' | '.join(self.tail) or 'Gemini reported an error.')

    def draft(self):
        if self.failure:
            raise GeminiError(signed_in_hint(self.failure[:500]))
        if not self.text:
            raise GeminiError(
                'Gemini returned no answer.'
                + (' It said: ' + ' | '.join(self.tail) if self.tail else '')
            )
        return cli_draft.json_draft(self.text, 'Gemini', GeminiError)


def signed_in_hint(message):
    """The message, with how to sign in when Gemini says it could not."""
    if re.search(r'auth|sign[ -]?in|log[ -]?in|credential|consent', message, re.I):
        return f'{message} To draft with Gemini, {SIGN_IN}.'
    return message


def run(command, prompt, env, answer, workdir):
    """Run Gemini in `workdir` and feed `answer` its output; stops it on a tool request or a timeout."""
    code = cli_draft.run(
        command,
        prompt,
        env,
        answer.add,
        name='Gemini',
        timeout=TIMEOUT,
        error=GeminiError,
        cwd=workdir,
    )
    if code == 41:  # Gemini's exit status for a failed sign-in
        raise GeminiError(
            'Gemini is not signed in. Studio drafts only with your Google account: ' + SIGN_IN + '.'
        )
    if code != 0 and not answer.failure:
        clue = ' | '.join(answer.tail)
        raise GeminiError(
            signed_in_hint(
                f'Gemini exited with status {code}.' + (' It said: ' + clue if clue else '')
            )
        )


def generate(kind, gemini, model, prompt):
    """Draft one proposal of `kind`. `gemini` is the command that starts Gemini, as a list. Returns (draft, usage)."""
    text = full_prompt(prompt, draft_schemas.for_kind(kind))
    if len(text.encode('utf-8')) > MAX_PROMPT:
        raise GeminiError('The structured draft prompt is too large.')
    env = child_env()
    require_google_login(env)
    sweep_stale()
    scratch = tempfile.mkdtemp(prefix=SCRATCH)
    try:
        workdir = os.path.join(scratch, 'empty')
        os.mkdir(workdir)  # Gemini's workspace, so the only files it could see are none
        policy_path = os.path.join(scratch, 'no-tools.toml')
        with open(policy_path, 'w', encoding='utf-8') as file:
            file.write(POLICY)
        answer = Answer()
        run(command_line(gemini, policy_path, model), text, env, answer, workdir)
        if not answer.finished and not answer.failure:
            raise GeminiError(
                'Gemini stopped before it finished its answer.'
                + (' It said: ' + ' | '.join(answer.tail) if answer.tail else '')
            )
        return answer.draft(), answer.used
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main():
    if len(sys.argv) not in (3, 4):
        raise GeminiError('Expected draft kind, the gemini command and an optional model.')
    kind, gemini = sys.argv[1:3]
    model = sys.argv[3] if len(sys.argv) == 4 else ''
    prompt = sys.stdin.read(MAX_PROMPT + 1)
    draft, used = generate(kind, [gemini], model, prompt)
    print(json.dumps({'structured_output': draft, 'usage': used}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        sys.exit('Gemini drafting failed: ' + str(error))
