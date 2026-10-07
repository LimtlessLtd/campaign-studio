import json
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import usage


CLAUDE_RESULT = json.dumps(
    {
        'type': 'result',
        'result': '{}',
        'total_cost_usd': 0.0421,
        'duration_ms': 8400,
        'usage': {
            'input_tokens': 120,
            'output_tokens': 340,
            'cache_read_input_tokens': 900,
            'cache_creation_input_tokens': 15,
        },
    }
)


class UsageTests(unittest.TestCase):
    def test_reads_the_claude_result_envelope(self):
        found = usage.from_output(CLAUDE_RESULT)
        self.assertEqual(found['input_tokens'], 120)
        self.assertEqual(found['output_tokens'], 340)
        self.assertEqual(found['cache_read_input_tokens'], 900)
        self.assertEqual(found['cost_usd'], 0.0421)
        self.assertEqual(found['seconds'], 8.4)

    def test_openai_output_has_tokens_but_no_price(self):
        raw = json.dumps(
            {'structured_output': {}, 'usage': {'input_tokens': 10, 'output_tokens': 4}}
        )
        job = {'kind': 'request-draft', 'started': 100.0, 'ended': 103.5}
        usage.record(job, raw)
        self.assertEqual(job['usage']['input_tokens'], 10)
        self.assertIsNone(job['usage']['cost_usd'])
        self.assertEqual(job['usage']['seconds'], 3.5)

    def test_output_without_usage_or_a_draft_job_records_nothing(self):
        for raw in ('not json', '[]', json.dumps({'result': '{}'}), ''):
            self.assertIsNone(usage.from_output(raw))
        job = {'kind': 'generate'}
        usage.record(job, CLAUDE_RESULT)
        self.assertNotIn('usage', job)

    def test_bad_numbers_are_ignored_not_trusted(self):
        raw = json.dumps(
            {'usage': {'input_tokens': -5, 'output_tokens': 'many'}, 'total_cost_usd': -1}
        )
        found = usage.from_output(raw)
        self.assertEqual(
            (found['input_tokens'], found['output_tokens'], found['cost_usd']), (0, 0, None)
        )

    def test_totals_group_by_month_and_count_unpriced_jobs(self):
        april = time.mktime((2026, 4, 10, 12, 0, 0, 0, 0, -1))
        may = time.mktime((2026, 5, 2, 12, 0, 0, 0, 0, -1))
        priced = usage.from_output(CLAUDE_RESULT)
        free = dict(priced, cost_usd=None, seconds=2)
        jobs = [
            {'created': april, 'usage': priced},
            {'created': may, 'usage': priced},
            {'created': may, 'usage': free},
            {'created': may},
        ]
        months = usage.totals(jobs)['months']
        self.assertEqual([m['month'] for m in months], ['2026-05', '2026-04'])
        self.assertEqual(months[0]['jobs'], 2)
        self.assertEqual(months[0]['output_tokens'], 680)
        self.assertEqual(months[0]['cost_usd'], 0.0421)
        self.assertEqual(months[0]['unpriced'], 1)
        self.assertEqual(months[0]['seconds'], 10.4)


if __name__ == '__main__':
    unittest.main()
