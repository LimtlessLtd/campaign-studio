"""The Codex draft provider against a fake `codex` command: no Codex account, network or model is used.

The fake answers `login status`, `features list` and `exec` the way the real CLI does (events as JSON
lines), records how it was started, and can be told to misbehave. The tests check the promises the worker
makes: the draft contract (no tools, one JSON object), the subscription login only, and that a failure or
a tool request fails the draft instead of reaching the campaign.
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

import codex_worker
import draft_schemas
import usage
import workflow

DRAFT = {'summary': 'Synthetic draft'}

FAKE = r"""
import json, os, sys, time

args = sys.argv[1:]
mode = os.environ.get('FAKE_MODE', 'ok')
home = os.environ['FAKE_HOME']


def say(event):
    print(json.dumps(event) if isinstance(event, dict) else event, flush=True)


if args[:2] == ['login', 'status']:
    print(os.environ.get('FAKE_LOGIN', 'Logged in using ChatGPT'))
    sys.exit(int(os.environ.get('FAKE_LOGIN_EXIT', '0')))
if args[:2] == ['features', 'list']:
    if os.environ.get('FAKE_FEATURES') == 'unreadable':
        print('Features have moved: see `codex doctor`')
        sys.exit(0)
    for name in ('shell_tool', 'unified_exec', 'multi_agent', 'apps', 'goals'):
        print(f'{name:<30} stable             true')
    sys.exit(0)

assert args[0] == 'exec', args
prompt = sys.stdin.read()
cwd = args[args.index('--cd') + 1]
schema = args[args.index('--output-schema') + 1]
with open(os.path.join(home, 'run.json'), 'w') as file:
    json.dump(
        dict(
            args=args,
            prompt=prompt,
            cwd=cwd,
            cwd_files=os.listdir(cwd),
            schema=json.load(open(schema)),
            scratch=os.path.dirname(schema),
            keys=[k for k in ('OPENAI_API_KEY', 'CODEX_API_KEY') if k in os.environ],
        ),
        file,
    )

say({'type': 'thread.started', 'thread_id': 't1'})
say({'type': 'turn.started'})
if mode == 'tool':
    say({'type': 'item.started', 'item': {'id': 'i1', 'type': 'command_execution', 'command': 'dir'}})
    time.sleep(2)  # a tool that was allowed to run would leave this behind
    open(os.path.join(home, 'tool-ran'), 'w').close()
    sys.exit(0)
if mode == 'fail':
    say({'type': 'error', 'message': 'Reconnecting... 1/5'})
    say({'type': 'turn.failed', 'error': {'message': "You've hit your usage limit. Try later."}})
    sys.exit(1)
if mode == 'junk':
    say('not json at all')
    sys.exit(3)
if mode == 'hang':
    time.sleep(60)
if mode == 'silent':
    sys.exit(0)
say({'type': 'item.completed', 'item': {'id': 'i0', 'type': 'reasoning', 'text': 'thinking'}})
say({'type': 'error', 'message': 'Reconnecting... 1/5'})  # transient: the turn still completes
text = {'text': 'Here you go', 'array': '[1, 2]', 'fenced': '```json\n{"summary": "Synthetic draft"}\n```'}.get(
    mode, json.dumps({'summary': 'Synthetic draft'})
)
say({'type': 'item.completed', 'item': {'id': 'i1', 'type': 'agent_message', 'text': text}})
say({'type': 'turn.completed', 'usage': {'input_tokens': 120, 'cached_input_tokens': 100, 'output_tokens': 30}})
"""


def make_fake(folder):
    """A `codex` the worker can start on this platform: (the command as a list, the path to give a child)."""
    script = Path(folder) / 'fake_codex.py'
    script.write_text(FAKE, encoding='utf-8')
    if os.name == 'nt':
        wrapper = Path(folder) / 'codex.cmd'
        wrapper.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding='utf-8')
    else:
        wrapper = Path(folder) / 'codex'
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding='utf-8'
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return [str(wrapper)]


class FakeCodex(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.codex = make_fake(self.home)
        environment = patch.dict(
            os.environ,
            {
                'FAKE_HOME': str(self.home),
                'FAKE_MODE': 'ok',
                'OPENAI_API_KEY': 'must-not-reach-codex',
                'CODEX_API_KEY': 'nor-this',
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    def draft(self, **mode):
        os.environ.update({key.upper(): value for key, value in mode.items()})
        return codex_worker.generate('request', self.codex, '', 'The synthetic prompt')

    def run_record(self):
        return json.loads((self.home / 'run.json').read_text(encoding='utf-8'))


class DraftTests(FakeCodex):
    def test_a_draft_is_the_models_final_json_with_the_tokens_codex_reported(self):
        draft, used = self.draft()
        self.assertEqual(draft, DRAFT)
        self.assertEqual(
            used, {'input_tokens': 120, 'output_tokens': 30, 'cache_read_input_tokens': 100}
        )
        self.assertEqual(self.run_record()['prompt'], 'The synthetic prompt')

    def test_codex_starts_without_tools_config_sessions_or_api_keys(self):
        self.draft()
        record = self.run_record()
        args = record['args']
        for flag in ('--json', '--ephemeral', '--ignore-user-config', '--ignore-rules'):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
        disabled = [args[i + 1] for i, arg in enumerate(args) if arg == '--disable']
        self.assertEqual(disabled, ['shell_tool', 'unified_exec', 'multi_agent', 'apps'])
        self.assertIn('web_search=disabled', args)
        self.assertEqual(record['keys'], [])  # present in this process, absent for Codex
        self.assertFalse(any(arg.startswith('--model') for arg in args))
        # It starts in an empty folder and is held to the same schema every strict provider gets.
        self.assertEqual((record['cwd'], record['cwd_files']), (args[args.index('--cd') + 1], []))
        self.assertEqual(record['schema'], draft_schemas.for_kind('request'))
        self.assertFalse(Path(record['scratch']).exists())  # and nothing is left behind

    def test_an_unreadable_feature_list_turns_every_feature_off_rather_than_none(self):
        self.draft(fake_features='unreadable')
        args = self.run_record()['args']
        disabled = [args[i + 1] for i, arg in enumerate(args) if arg == '--disable']
        self.assertEqual(disabled, list(codex_worker.OFF_FEATURES))

    def test_a_chosen_model_is_passed_as_one_argument_that_cannot_become_a_flag(self):
        codex_worker.generate('request', self.codex, '--sandbox=danger-full-access', 'p')
        args = self.run_record()['args']
        self.assertIn('--model=--sandbox=danger-full-access', args)
        self.assertEqual(args.count('--sandbox'), 1)

    def test_a_fenced_answer_is_read_and_other_answers_are_refused(self):
        self.assertEqual(self.draft(fake_mode='fenced')[0], DRAFT)
        for mode, message in (
            ('text', 'did not return JSON'),
            ('array', 'not an object'),
            ('silent', 'no answer'),
        ):
            with self.subTest(mode=mode), self.assertRaisesRegex(codex_worker.CodexError, message):
                self.draft(fake_mode=mode)

    def test_a_failed_turn_says_why_and_a_transient_error_does_not_fail_a_turn(self):
        with self.assertRaisesRegex(codex_worker.CodexError, 'usage limit'):
            self.draft(fake_mode='fail')
        self.assertEqual(self.draft(fake_mode='ok')[0], DRAFT)  # it printed a reconnect error first

    def test_a_codex_that_never_finishes_is_stopped(self):
        with patch.object(codex_worker, 'TIMEOUT', 1):
            with self.assertRaisesRegex(codex_worker.CodexError, 'took too long'):
                self.draft(fake_mode='hang')

    def test_an_exit_with_only_noise_reports_the_status_and_what_codex_said(self):
        with self.assertRaisesRegex(codex_worker.CodexError, r'status 3.*not json at all'):
            self.draft(fake_mode='junk')

    def test_only_the_chatgpt_subscription_login_is_used(self):
        for text, code in (('Logged in using an API key', '0'), ('Not logged in', '1')):
            with (
                self.subTest(text=text),
                self.assertRaisesRegex(codex_worker.CodexError, 'ChatGPT'),
            ):
                self.draft(fake_login=text, fake_login_exit=code)
            self.assertFalse((self.home / 'run.json').exists())  # Codex never ran the draft

    def test_a_prompt_over_the_limit_or_a_kind_without_a_schema_is_refused_before_codex_starts(
        self,
    ):
        with self.assertRaisesRegex(codex_worker.CodexError, 'too large'):
            codex_worker.generate('request', self.codex, '', 'x' * (codex_worker.MAX_PROMPT + 1))
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            codex_worker.generate('mystery', self.codex, '', 'p')
        self.assertFalse((self.home / 'run.json').exists())


class ToolGuardTests(FakeCodex):
    def test_a_tool_request_stops_codex_before_the_tool_can_act(self):
        started = time.monotonic()
        with self.assertRaisesRegex(codex_worker.CodexError, r'tool \(command_execution\)'):
            self.draft(fake_mode='tool')
        self.assertLess(time.monotonic() - started, 30)
        time.sleep(2.5)  # long enough for a Codex that was not stopped to have acted
        self.assertFalse((self.home / 'tool-ran').exists())

    def test_anything_but_thinking_and_answering_is_a_tool(self):
        for item in (
            {'type': 'command_execution', 'command': 'dir'},
            {'type': 'file_change', 'changes': []},
            {'type': 'mcp_tool_call', 'server': 's', 'tool': 't'},
            {'type': 'web_search', 'query': 'q'},
            {'type': 'collab_tool_call', 'tool': 'spawn_agent'},
            {'item_type': 'command_execution'},  # older Codex names the field item_type
            {},
        ):
            for kind in ('item.started', 'item.updated', 'item.completed'):
                with self.subTest(item=item, kind=kind), self.assertRaises(codex_worker.CodexError):
                    codex_worker.Answer().add(json.dumps({'type': kind, 'item': item}))

    def test_thinking_planning_and_answering_are_allowed(self):
        answer = codex_worker.Answer()
        for item in (
            {'type': 'reasoning', 'text': 'hm'},
            {'type': 'todo_list', 'items': []},
            {'item_type': 'agent_message', 'text': '{"summary": "x"}'},  # older Codex
        ):
            answer.add(json.dumps({'type': 'item.completed', 'item': item}))
        answer.add(json.dumps({'type': 'turn.started'}))
        answer.add(json.dumps([1]))
        self.assertEqual(answer.draft(), {'summary': 'x'})


class ScratchTests(unittest.TestCase):
    def test_scratch_folders_of_cancelled_drafts_are_swept_when_a_day_old(self):
        with tempfile.TemporaryDirectory() as root:
            old, new, other = (
                Path(root) / name
                for name in (
                    codex_worker.SCRATCH + 'old',
                    codex_worker.SCRATCH + 'new',
                    'someone-elses',
                )
            )
            for folder in (old, new, other):
                folder.mkdir()
            week = time.time() - 7 * 24 * 3600
            for folder in (old, other):
                os.utime(folder, (week, week))
            codex_worker.sweep_stale(root)
            self.assertEqual(
                sorted(p.name for p in Path(root).iterdir()), sorted(['someone-elses', new.name])
            )


class WorkerProcessTests(FakeCodex):
    """The worker as the job lane starts it: a child process whose output is the draft envelope."""

    def run_worker(self, **mode):
        env = dict(os.environ, **{key.upper(): value for key, value in mode.items()})
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / 'DM' / 'tools' / 'codex_worker.py'),
                'request',
                self.codex[0],
            ],
            input='The synthetic prompt',
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    def test_the_envelope_is_read_like_claude_codes_and_records_usage_without_a_price(self):
        done = self.run_worker()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(workflow.parse_output(done.stdout), DRAFT)
        found = usage.from_output(done.stdout)
        self.assertEqual((found['input_tokens'], found['cache_read_input_tokens']), (120, 100))
        self.assertIsNone(found['cost_usd'])  # a subscription states no price

    def test_a_failure_exits_with_the_reason_for_the_job_log(self):
        done = self.run_worker(fake_mode='fail')
        self.assertEqual(done.returncode, 1)
        self.assertIn('Codex drafting failed', done.stderr)
        self.assertIn('usage limit', done.stderr)
        self.assertEqual(done.stdout, '')


if __name__ == '__main__':
    unittest.main()
