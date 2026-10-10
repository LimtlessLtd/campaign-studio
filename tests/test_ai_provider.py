"""Structured draft provider contracts without a paid API call."""

import io
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
sys.path.insert(0, str(ROOT / 'DM' / 'tools'))

import ai_provider
import draft_schemas
import openai_worker
import request_workflow
import workflow


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return io.BytesIO(json.dumps(self.response).encode())


class AIProviderTests(unittest.TestCase):
    def test_openai_schemas_require_every_object_field(self):
        def check_strict(schema):
            if 'anyOf' in schema:
                for option in schema['anyOf']:
                    check_strict(option)
            if schema.get('type') == 'object':
                self.assertEqual(set(schema['properties']), set(schema['required']))
                self.assertIs(schema['additionalProperties'], False)
                for property_schema in schema['properties'].values():
                    check_strict(property_schema)
            if schema.get('type') == 'array':
                check_strict(schema['items'])

        for kind in (
            'request',
            *request_workflow.WORKFLOWS,
            'content',
            'classify',
            'thread-ledger',
            'arc-options',
            'layout',
            'revision',
        ):
            with self.subTest(kind=kind):
                check_strict(draft_schemas.for_kind(kind))

        layout = draft_schemas.for_kind('layout')
        self.assertEqual(layout, draft_schemas.for_kind('revision'))
        operation = layout['properties']['operations']['items']
        original = workflow.LAYOUT_SCHEMA['properties']['operations']['items']
        examples = (
            {
                'type': 'rect',
                'row': 0,
                'col': 0,
                'width': 2,
                'height': 2,
                'fill': '.',
                'border': '',
            },
            {'type': 'path', 'points': [[0, 0], [0, 1]], 'width': 1, 'char': ':'},
            {'type': 'stamp', 'row': 0, 'col': 0, 'rows': ['.']},
            {
                'type': 'scatter',
                'row': 0,
                'col': 0,
                'width': 2,
                'height': 2,
                'count': 1,
                'char': '&',
                'replace': ' ',
            },
        )
        self.assertEqual(len(operation['anyOf']), len(examples))
        for variant, example in zip(operation['anyOf'], examples):
            self.assertEqual(variant['properties']['type']['enum'], [example['type']])
            self.assertEqual(set(variant['required']), set(example))
            workflow.validate_schema(example, original)

    def test_settings_choose_provider_without_saving_a_secret(self):
        clean = ai_provider.clean_settings(
            {
                'provider': 'openai',
                'model': 'gpt-4o-mini',
                'key_env': 'STUDIO_TEST_KEY',
                'api_key': 'do-not-save',
            }
        )
        self.assertEqual(
            clean,
            {'provider': 'openai', 'model': 'gpt-4o-mini', 'key_env': 'STUDIO_TEST_KEY'},
        )
        with patch.dict(os.environ, {'STUDIO_TEST_KEY': 'secret-value'}):
            command = ai_provider.OpenAIResponses().command(
                clean, 'request', request_workflow.SCHEMA
            )
            self.assertTrue(ai_provider.OpenAIResponses().available(clean))
        self.assertIn('openai_worker.py', command[2])
        self.assertEqual(command[3:], ['request', 'gpt-4o-mini', 'STUDIO_TEST_KEY'])
        self.assertNotIn('secret-value', ' '.join(command))
        for invalid in ({'provider': 'unknown'}, {'provider': 'openai'}, {'key_env': 'bad-name'}):
            with self.assertRaises(ValueError):
                ai_provider.clean_settings(invalid)

    def test_codex_is_a_subscription_provider_started_through_its_worker(self):
        clean = ai_provider.clean_settings({'provider': 'codex'})  # no model, no key name needed
        self.assertEqual(clean['provider'], 'codex')
        with patch.object(ai_provider.shutil, 'which', return_value='/usr/bin/codex'):
            codex = ai_provider.PROVIDERS['codex']
            self.assertTrue(codex.available(clean))
            command = codex.command(clean, 'arc-options', {})
            self.assertEqual(
                ai_provider.status(clean) | {'key_available': False},
                {
                    'provider': 'codex',
                    'label': 'Codex',
                    'available': True,
                    'key_available': False,
                },
            )
            with_model = codex.command(dict(clean, model='gpt-5'), 'request', {})
        self.assertIn('codex_worker.py', command[2])
        self.assertEqual(command[3:], ['arc-options', '/usr/bin/codex'])
        self.assertEqual(with_model[3:], ['request', '/usr/bin/codex', 'gpt-5'])
        with patch.object(ai_provider.shutil, 'which', return_value=None):
            self.assertFalse(ai_provider.PROVIDERS['codex'].available(clean))
            with self.assertRaisesRegex(ValueError, 'not installed'):
                ai_provider.PROVIDERS['codex'].command(clean, 'request', {})

    def test_every_draft_kind_the_server_queues_has_an_openai_schema(self):
        # A kind the worker does not know fails its job under the OpenAI provider (it did for the
        # thread ledger), so adding a kind to the server needs its schema here too.
        source = (ROOT / 'DM' / 'campaign_core.py').read_text(encoding='utf-8')
        kinds = set(re.findall(r"ai_provider\.command\(\s*'([a-z-]+)'", source))
        self.assertTrue({'classify', 'thread-ledger', 'arc-options'} <= kinds)
        # A request is queued under the kind its own workflow names (see the next test).
        self.assertIn('ai_provider.command(request_workflow.draft_kind(item)', source)
        for kind in kinds | {'request', *request_workflow.WORKFLOWS}:
            with self.subTest(kind=kind):
                self.assertIn('properties', draft_schemas.for_kind(kind))

    def test_each_request_kind_is_drafted_under_its_own_strict_schema(self):
        # A session pitch was queued as a plain `request`, so the OpenAI and Codex providers held its draft
        # to the general request schema and every Session Forge proposal failed validation.
        for kind, module in request_workflow.WORKFLOWS.items():
            with self.subTest(kind=kind):
                draft = request_workflow.draft_kind({'kind': kind})
                self.assertEqual(draft, kind)
                self.assertEqual(draft_schemas.for_kind(draft), module.SCHEMA)
        for kind in ('npc', 'encounter', 'plot', 'expand', 'other'):
            with self.subTest(kind=kind):
                draft = request_workflow.draft_kind({'kind': kind})
                self.assertEqual(draft_schemas.for_kind(draft), request_workflow.SCHEMA)

    def test_openai_worker_sends_a_tool_free_schema_request_and_parses_its_draft(self):
        draft = {'summary': 'Synthetic draft'}
        opener = FakeOpener(
            {
                'status': 'completed',
                'usage': {
                    'input_tokens': 12,
                    'output_tokens': 7,
                    'input_tokens_details': {'cached_tokens': 4},
                },
                'output': [
                    {
                        'type': 'message',
                        'content': [
                            {'type': 'output_text', 'text': '{"summary": '},
                            {'type': 'output_text', 'text': '"Synthetic draft"}'},
                        ],
                    }
                ],
            }
        )
        with patch.dict(os.environ, {'STUDIO_TEST_KEY': 'secret-value'}):
            result, used = openai_worker.generate(
                'request', 'gpt-4o-mini', 'STUDIO_TEST_KEY', 'Synthetic prompt', opener
            )
        self.assertEqual(result, draft)
        self.assertEqual(used['input_tokens'], 12)
        self.assertEqual(used['cache_read_input_tokens'], 4)
        self.assertEqual(opener.request.full_url, openai_worker.RESPONSES_URL)
        self.assertEqual(opener.request.get_header('Authorization'), 'Bearer secret-value')
        body = json.loads(opener.request.data)
        self.assertEqual(body['input'], 'Synthetic prompt')
        self.assertEqual(body['model'], 'gpt-4o-mini')
        self.assertEqual(body['tools'], [])
        self.assertFalse(body['store'])
        self.assertEqual(body['text']['format']['schema'], request_workflow.SCHEMA)
        self.assertNotIn('secret-value', opener.request.data.decode())

    def test_openai_worker_rejects_refused_and_incomplete_responses(self):
        for response in (
            {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}},
            {
                'status': 'completed',
                'output': [
                    {'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'Cannot help.'}]}
                ],
            },
            {'status': 'completed', 'output': []},
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                openai_worker.parse_response(response)


if __name__ == '__main__':
    unittest.main()
