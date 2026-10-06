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


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return io.BytesIO(json.dumps(self.response).encode())


class AIProviderTests(unittest.TestCase):
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
            result = openai_worker.generate(
                'request', 'gpt-4o-mini', 'STUDIO_TEST_KEY', 'Synthetic prompt', opener
            )
        self.assertEqual(result, draft)
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
