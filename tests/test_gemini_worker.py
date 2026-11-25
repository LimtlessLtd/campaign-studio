"""The Gemini draft provider against a fake `gemini` command: no Google account, network or model is used.

The fake answers the headless call the way Gemini CLI 0.71 does (`stream-json` events as JSON lines),
records how it was started, and can be told to misbehave. The tests check the promises the worker makes:
the draft contract (no tools, one JSON object), the Google sign-in only, and that a failure or a tool
request fails the draft instead of reaching the campaign.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
sys.path.insert(0, str(ROOT / 'DM' / 'tools'))

import draft_schemas
import gemini_worker
import usage
import workflow

DRAFT = {'summary': 'Synthetic draft'}

FAKE = r"""
import json, os, sys, time

args = sys.argv[1:]
mode = os.environ.get('FAKE_MODE', 'ok')
home = os.environ['FAKE_HOME']
prompt = sys.stdin.read()
policy = next(a.split('=', 1)[1] for a in args if a.startswith('--policy='))
with open(os.path.join(home, 'run.json'), 'w') as file:
    json.dump(
        dict(
            args=args,
            prompt=prompt,
            cwd=os.getcwd(),
            cwd_files=os.listdir(os.getcwd()),
            policy=open(policy).read(),
            scratch=os.path.dirname(policy),
            keys=[k for k in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GOOGLE_GENAI_USE_VERTEXAI') if k in os.environ],
            login=os.environ.get('GOOGLE_GENAI_USE_GCA'),
        ),
        file,
    )


def say(event):
    print(json.dumps(event) if isinstance(event, dict) else event, flush=True)


def result(status='success', tool_calls=0, **extra):
    stats = {'total_tokens': 150, 'input_tokens': 120, 'output_tokens': 30, 'cached': 100,
             'input': 20, 'duration_ms': 900, 'tool_calls': tool_calls, 'models': {}}
    say(dict(type='result', timestamp='t', status=status, stats=stats, **extra))


if mode == 'noauth':
    print('Error authenticating: Authentication consent could not be obtained.', file=sys.stderr)
    sys.exit(41)
say({'type': 'init', 'timestamp': 't', 'session_id': 's1', 'model': 'gemini-2.5-pro'})
say({'type': 'message', 'timestamp': 't', 'role': 'user', 'content': prompt[:40]})
if mode == 'tool':
    say({'type': 'tool_use', 'timestamp': 't', 'tool_name': 'run_shell_command', 'tool_id': 'x1',
         'parameters': {'command': 'dir'}})
    time.sleep(2)  # a tool that was allowed to run would leave this behind
    open(os.path.join(home, 'tool-ran'), 'w').close()
    sys.exit(0)
if mode == 'fail':
    say({'type': 'error', 'timestamp': 't', 'severity': 'error', 'message': 'RESOURCE_EXHAUSTED'})
    result('error', error={'type': 'Error', 'message': 'You have exhausted your capacity on this model.'})
    sys.exit(1)
if mode == 'junk':
    say('not json at all')
    sys.exit(3)
if mode == 'hang':
    time.sleep(60)
if mode == 'silent':
    sys.exit(0)
say({'type': 'error', 'timestamp': 't', 'severity': 'warning', 'message': 'Retrying after 429'})
text = {
    'text': 'Here you go',
    'array': '[1, 2]',
    'fenced': '```json\n{"summary": "Synthetic draft"}\n```',
    'prose': 'Here is the proposal:\n{"summary": "Synthetic draft"}\nI hope it helps.',
}.get(mode, json.dumps({'summary': 'Synthetic draft'}))
half = len(text) // 2
for part in (text[:half], text[half:]):
    say({'type': 'message', 'timestamp': 't', 'role': 'assistant', 'content': part, 'delta': True})
if mode == 'unfinished':
    sys.exit(0)
result(tool_calls=1 if mode == 'tool_stats' else 0)
"""


def make_fake(folder):
    """A `gemini` the worker can start on this platform: (the command as a list, the path to give a child)."""
    script = Path(folder) / 'fake_gemini.py'
    script.write_text(FAKE, encoding='utf-8')
    if os.name == 'nt':
        wrapper = Path(folder) / 'gemini.cmd'
        wrapper.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding='utf-8')
    else:
        wrapper = Path(folder) / 'gemini'
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding='utf-8'
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return [str(wrapper)]


class FakeGemini(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.gemini = make_fake(self.home)
        (self.home / 'user').mkdir()
        environment = patch.dict(
            os.environ,
            {
                'FAKE_HOME': str(self.home),
                'FAKE_MODE': 'ok',
                # Gemini's settings come from here, never from the machine running the tests.
                'GEMINI_CLI_HOME': str(self.home / 'user'),
                'GEMINI_CLI_SYSTEM_SETTINGS_PATH': str(self.home / 'system-settings.json'),
                'GEMINI_API_KEY': 'must-not-reach-gemini',
                'GOOGLE_API_KEY': 'nor-this',
                'GOOGLE_GENAI_USE_VERTEXAI': 'true',
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    def draft(self, prompt='The synthetic prompt', **mode):
        os.environ.update({key.upper(): value for key, value in mode.items()})
        return gemini_worker.generate('request', self.gemini, '', prompt)

    def run_record(self):
        return json.loads((self.home / 'run.json').read_text(encoding='utf-8'))

    def user_settings(self, text):
        folder = self.home / 'user' / '.gemini'
        folder.mkdir(exist_ok=True)
        (folder / 'settings.json').write_text(text, encoding='utf-8')


class DraftTests(FakeGemini):
    def test_a_draft_is_the_models_joined_answer_with_the_tokens_gemini_reported(self):
        draft, used = self.draft()
        self.assertEqual(draft, DRAFT)
        self.assertEqual(
            used, {'input_tokens': 120, 'output_tokens': 30, 'cache_read_input_tokens': 100}
        )
        prompt = self.run_record()['prompt']
        self.assertTrue(prompt.startswith(gemini_worker.HEADER + 'The synthetic prompt'))

    def test_gemini_starts_read_only_with_every_tool_denied_and_no_api_keys(self):
        self.draft()
        record = self.run_record()
        args = record['args']
        for flag in (
            '--output-format=stream-json',
            '--approval-mode=plan',
            '--extensions=none',
            '--allowed-mcp-server-names=' + gemini_worker.NO_MCP,
            '--skip-trust',
            '--prompt=' + gemini_worker.ASK,
        ):
            self.assertIn(flag, args)
        self.assertFalse(any(arg.startswith('--model') for arg in args))
        self.assertFalse(any(arg in ('--yolo', '-y') or 'auto_edit' in arg for arg in args))
        # The policy denies every built-in and MCP tool.
        policy = record['policy']
        self.assertEqual(policy.count('toolName = "*"'), 2)
        self.assertEqual(policy.count('decision = "deny"'), 2)
        self.assertIn('mcpName = "*"', policy)
        self.assertNotIn('allow', policy.replace('denyMessage', ''))
        self.assertEqual(record['keys'], [])  # present in this process, absent for Gemini
        self.assertEqual(record['login'], 'true')  # the Google sign-in is selected for it
        self.assertEqual(record['cwd_files'], [])  # it starts in an empty folder
        self.assertFalse(Path(record['scratch']).exists())  # and nothing is left behind

    def test_the_prompt_carries_the_strict_schema_gemini_has_no_switch_for(self):
        self.draft()
        prompt = self.run_record()['prompt']
        schema = json.loads(prompt.rsplit('\n', 1)[1])
        self.assertEqual(schema, draft_schemas.for_kind('request'))

    def test_campaign_text_cannot_pull_in_files_or_run_a_slash_command(self):
        self.draft(prompt='/quit\nSee @notes.txt and @C:/secret, but keep \\@escaped.')
        prompt = self.run_record()['prompt']
        self.assertTrue(prompt.startswith(gemini_worker.HEADER))  # never starts with a slash
        self.assertIn('See \\@notes.txt and \\@C:/secret, but keep \\@escaped.', prompt)
        self.assertNotIn('\\\\@', prompt)

    def test_a_chosen_model_is_passed_as_one_argument_that_cannot_become_a_flag(self):
        gemini_worker.generate('request', self.gemini, '--yolo', 'p')
        args = self.run_record()['args']
        self.assertIn('--model=--yolo', args)
        self.assertNotIn('--yolo', args)

    def test_a_fenced_or_wrapped_answer_is_read_and_other_answers_are_refused(self):
        self.assertEqual(self.draft(fake_mode='fenced')[0], DRAFT)
        self.assertEqual(self.draft(fake_mode='prose')[0], DRAFT)
        for mode, message in (
            ('text', 'did not return JSON'),
            ('array', 'not an object'),
            ('silent', 'stopped before it finished'),
            ('unfinished', 'stopped before it finished'),
        ):
            with (
                self.subTest(mode=mode),
                self.assertRaisesRegex(gemini_worker.GeminiError, message),
            ):
                self.draft(fake_mode=mode)

    def test_a_failed_result_says_why_and_a_warning_does_not_fail_a_draft(self):
        with self.assertRaisesRegex(gemini_worker.GeminiError, 'exhausted your capacity'):
            self.draft(fake_mode='fail')
        self.assertEqual(self.draft(fake_mode='ok')[0], DRAFT)  # it printed a retry warning first

    def test_a_gemini_that_is_not_signed_in_says_how_to_sign_in(self):
        with self.assertRaisesRegex(gemini_worker.GeminiError, 'Sign in with Google'):
            self.draft(fake_mode='noauth')

    def test_a_gemini_that_never_finishes_is_stopped(self):
        with patch.object(gemini_worker, 'TIMEOUT', 1):
            with self.assertRaisesRegex(gemini_worker.GeminiError, 'took too long'):
                self.draft(fake_mode='hang')

    def test_an_exit_with_only_noise_reports_the_status_and_what_gemini_said(self):
        with self.assertRaisesRegex(gemini_worker.GeminiError, r'status 3.*not json at all'):
            self.draft(fake_mode='junk')

    def test_only_the_google_sign_in_is_used(self):
        for settings in (
            '{"security": {"auth": {"selectedType": "gemini-api-key"}}}',
            '// a comment\n{"security": {"auth": {"selectedType": "vertex-ai"}}}',
        ):
            self.user_settings(settings)
            with (
                self.subTest(settings=settings),
                self.assertRaisesRegex(gemini_worker.GeminiError, 'Google account'),
            ):
                self.draft()
            self.assertFalse((self.home / 'run.json').exists())  # Gemini never ran the draft
        (self.home / 'system-settings.json').write_text(
            '{"security": {"auth": {"enforcedType": "gemini-api-key"}}}', encoding='utf-8'
        )
        self.user_settings('{"security": {"auth": {"selectedType": "oauth-personal"}}}')
        with self.assertRaisesRegex(gemini_worker.GeminiError, 'gemini-api-key'):
            self.draft()
        (self.home / 'system-settings.json').unlink()
        self.assertEqual(self.draft()[0], DRAFT)  # a Google sign-in drafts

    def test_a_prompt_over_the_limit_or_a_kind_without_a_schema_is_refused_before_gemini_starts(
        self,
    ):
        with self.assertRaisesRegex(gemini_worker.GeminiError, 'too large'):
            gemini_worker.generate('request', self.gemini, '', 'x' * gemini_worker.MAX_PROMPT)
        with self.assertRaisesRegex(gemini_worker.GeminiError, 'too large'):
            # Gemini's limit is in bytes, so a prompt of fewer characters can still be over it.
            gemini_worker.generate(
                'request', self.gemini, '', 'é' * (gemini_worker.MAX_PROMPT // 2)
            )
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            gemini_worker.generate('mystery', self.gemini, '', 'p')
        self.assertFalse((self.home / 'run.json').exists())


class ToolGuardTests(FakeGemini):
    def test_a_tool_request_stops_gemini_before_the_tool_can_act(self):
        started = time.monotonic()
        with self.assertRaisesRegex(gemini_worker.GeminiError, r'tool \(run_shell_command\)'):
            self.draft(fake_mode='tool')
        self.assertLess(time.monotonic() - started, 30)
        time.sleep(2.5)  # long enough for a Gemini that was not stopped to have acted
        self.assertFalse((self.home / 'tool-ran').exists())

    def test_a_result_that_reports_a_tool_call_is_refused(self):
        with self.assertRaisesRegex(gemini_worker.GeminiError, 'reported a tool call'):
            self.draft(fake_mode='tool_stats')

    def test_anything_but_starting_answering_and_reporting_is_a_tool(self):
        for event in (
            {'type': 'tool_use', 'tool_name': 'read_file', 'parameters': {}},
            {'type': 'tool_result', 'tool_id': 'x', 'status': 'success'},
            {'type': 'subagent_started'},  # a kind a later Gemini might add
            {},
        ):
            with self.subTest(event=event), self.assertRaises(gemini_worker.GeminiError):
                gemini_worker.Answer().add(json.dumps(event))

    def test_starting_answering_and_reporting_are_allowed(self):
        answer = gemini_worker.Answer()
        for event in (
            {'type': 'init', 'session_id': 's', 'model': 'm'},
            {'type': 'message', 'role': 'user', 'content': '{"summary": "not the answer"}'},
            {'type': 'message', 'role': 'assistant', 'content': '{"summary": ', 'delta': True},
            {'type': 'error', 'severity': 'warning', 'message': 'Retrying'},
            {'type': 'message', 'role': 'assistant', 'content': '"x"}', 'delta': True},
            {'type': 'result', 'status': 'success', 'stats': {'input_tokens': True}},
        ):
            answer.add(json.dumps(event))
        answer.add(json.dumps([1]))
        answer.add('   ')
        self.assertEqual(answer.draft(), {'summary': 'x'})
        self.assertEqual(answer.used['input_tokens'], 0)  # a boolean is not a count


class ScratchTests(unittest.TestCase):
    def test_scratch_folders_of_cancelled_drafts_are_swept_when_a_day_old(self):
        with tempfile.TemporaryDirectory() as root:
            old, new, codex = (
                Path(root) / name
                for name in (
                    gemini_worker.SCRATCH + 'old',
                    gemini_worker.SCRATCH + 'new',
                    'campaign-studio-codex-old',  # another worker's: it sweeps its own
                )
            )
            for folder in (old, new, codex):
                folder.mkdir()
            week = time.time() - 7 * 24 * 3600
            for folder in (old, codex):
                os.utime(folder, (week, week))
            gemini_worker.sweep_stale(root)
            self.assertEqual(
                sorted(p.name for p in Path(root).iterdir()), sorted([codex.name, new.name])
            )


class WorkerProcessTests(FakeGemini):
    """The worker as the job lane starts it: a child process whose output is the draft envelope."""

    def run_worker(self, *extra, **mode):
        env = dict(os.environ, **{key.upper(): value for key, value in mode.items()})
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / 'DM' / 'tools' / 'gemini_worker.py'),
                'request',
                self.gemini[0],
                *extra,
            ],
            input='The synthetic prompt',
            capture_output=True,
            text=True,
            encoding='utf-8',
            env=env,
            timeout=120,
        )

    def test_the_envelope_is_read_like_claude_codes_and_records_usage_without_a_price(self):
        done = self.run_worker('gemini-2.5-pro')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(workflow.parse_output(done.stdout), DRAFT)
        found = usage.from_output(done.stdout)
        self.assertEqual((found['input_tokens'], found['cache_read_input_tokens']), (120, 100))
        self.assertIsNone(found['cost_usd'])  # a subscription states no price
        self.assertIn('--model=gemini-2.5-pro', self.run_record()['args'])

    def test_a_failure_exits_with_the_reason_for_the_job_log(self):
        done = self.run_worker(fake_mode='fail')
        self.assertEqual(done.returncode, 1)
        self.assertIn('Gemini drafting failed', done.stderr)
        self.assertIn('exhausted your capacity', done.stderr)
        self.assertEqual(done.stdout, '')


if __name__ == '__main__':
    unittest.main()
