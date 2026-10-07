"""Structured draft provider contracts without a paid API call."""

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
sys.path.insert(0, str(ROOT / 'DM' / 'tools'))

import ai_provider
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

        for kind in ('request', 'content', 'layout', 'revision'):
            with self.subTest(kind=kind):
                check_strict(openai_worker.draft_schema(kind))

        layout = openai_worker.draft_schema('layout')
        self.assertEqual(layout, openai_worker.draft_schema('revision'))
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
