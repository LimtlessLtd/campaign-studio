"""One row of a proposal is written again alone: its key, its schema and its prompt."""

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import draft_schemas
import item_review
import redraft
import request_workflow
import workflow

KINDS = ('entries', 'threads')


def draft():
    return {
        'summary': 'A summary.',
        'entries': [{'id': 'a', 'name': 'Ann'}, {'id': 'b', 'name': 'Bo'}],
        'threads': [{'id': 'a', 'title': 'Same ID, other kind'}],
        'goals': ['One goal'],
    }


class ItemReviewTests(unittest.TestCase):
    def test_a_row_is_found_by_its_kind_and_id(self):
        self.assertEqual(item_review.find(draft(), 'entries:b', KINDS), {'id': 'b', 'name': 'Bo'})
        self.assertEqual(
            item_review.find(draft(), 'threads:a', KINDS)['title'], 'Same ID, other kind'
        )

    def test_a_key_that_names_no_row_is_refused(self):
        for key in ('entries:z', 'goals:One goal', 'notes:a', 'entries', ''):
            with self.subTest(key=key), self.assertRaises(ValueError):
                item_review.find(draft(), key, KINDS)

    def test_a_replaced_row_keeps_its_id_and_nothing_else_changes(self):
        before = draft()
        after = item_review.replaced(before, 'entries:a', {'id': 'renamed', 'name': 'Anna'}, KINDS)
        expected = deepcopy(before)
        expected['entries'][0] = {'id': 'a', 'name': 'Anna'}
        self.assertEqual(after, expected)
        self.assertEqual(before, draft())  # the staged draft itself is not edited


class EnvelopeTests(unittest.TestCase):
    def test_the_answer_is_one_row_under_the_schema_of_its_kind(self):
        schema = request_workflow.SCHEMA
        answer = redraft.envelope(schema, 'threads')
        self.assertEqual(answer, workflow.obj({'row': schema['properties']['threads']['items']}))
        row = {'id': 'x', 'title': 'T', 'detail': 'D', 'status': 'open'}
        self.assertEqual(redraft.new_row(schema, 'threads', {'row': row}), row)

    def test_only_an_array_of_rows_with_ids_can_be_written_again(self):
        schema = request_workflow.SCHEMA
        for kind in ('goals', 'checklist', 'notes', 'summary', 'focus', 'nothing'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                redraft.envelope(schema, kind)

    def test_an_answer_that_is_not_exactly_one_valid_row_is_refused(self):
        schema = request_workflow.SCHEMA
        row = {'id': 'x', 'title': 'T', 'detail': 'D', 'status': 'open'}
        for answer in (
            {},
            {'row': {**row, 'status': 'someday'}},
            {'row': {'id': 'x'}},
            {'row': row, 'extra': 1},
            [row],
            'text',
        ):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                redraft.new_row(schema, 'threads', answer)

    def test_a_draft_kind_can_name_the_row_a_provider_writes(self):
        for proposal, module in request_workflow.WORKFLOWS.items():
            for kind in module.REVIEWABLE:
                with self.subTest(proposal=proposal, kind=kind):
                    named = redraft.provider_kind(proposal, kind)
                    self.assertEqual(
                        draft_schemas.for_kind(named), redraft.envelope(module.SCHEMA, kind)
                    )
        self.assertEqual(
            draft_schemas.for_kind('request:entries'),
            redraft.envelope(request_workflow.SCHEMA, 'entries'),
        )
        # A whole map content draft's rows can be written again as well.
        self.assertIn('row', draft_schemas.for_kind('content:npcs')['properties'])

    def test_a_draft_kind_that_names_no_writable_row_is_refused(self):
        for kind in ('request:goals', 'request:nothing', 'nothing:entries', 'layout:operations'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                draft_schemas.for_kind(kind)


class PromptTests(unittest.TestCase):
    def test_the_prompt_carries_the_proposal_the_row_and_the_note_as_data(self):
        text = redraft.prompt(
            'PACK PROMPT', draft(), 'entries:a', {'id': 'a', 'name': 'Ann'}, 'Make her "older".'
        )
        self.assertTrue(text.startswith('PACK PROMPT'))
        self.assertIn('REDRAFT ONE ROW', text)
        self.assertIn('PROPOSAL: ' + json.dumps(draft()), text)
        self.assertIn('ROW TO REDRAFT (entries:a): {"id": "a", "name": "Ann"}', text)
        self.assertIn('GM NOTE: "Make her \\"older\\"."', text)

    def test_a_missing_note_asks_for_a_better_row(self):
        text = redraft.prompt('P', draft(), 'entries:a', {'id': 'a'}, '')
        self.assertIn('Write it again, better.', text)

    def test_a_long_note_is_cut_to_a_bounded_length(self):
        self.assertEqual(redraft.clean_note('  ' + 'x' * 5000), 'x' * redraft.MAX_NOTE)
        self.assertEqual(redraft.clean_note(None), '')


if __name__ == '__main__':
    unittest.main()
