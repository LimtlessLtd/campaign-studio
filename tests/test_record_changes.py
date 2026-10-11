"""A preview of a change to an existing record is the change apply makes, shortened for reading."""

import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import record_changes
import request_workflow
import shapes

# What the request layer calls on a workflow module (see the comment on `request_workflow.WORKFLOWS`).
WORKFLOW_INTERFACE = (
    'SCHEMA',
    'REVIEWABLE',
    'options',
    'prompt_pack',
    'validate',
    'base_hash',
    'reviewable_keys',
    'diff',
    'apply',
)


class RecordChangeTests(unittest.TestCase):
    def test_every_request_workflow_previews_what_it_applies(self):
        # A new kind is one row in WORKFLOWS, so a module that lacks a name would only fail when a GM
        # reached that step (the preview, for instance).
        for kind, module in request_workflow.WORKFLOWS.items():
            for name in WORKFLOW_INTERFACE:
                with self.subTest(kind=kind, name=name):
                    self.assertTrue(hasattr(module, name))

    def thread(self, **fields):
        return shapes.THREAD.new(id='t', title='The bell', **fields)

    def test_a_replaced_value_shows_the_old_and_the_new(self):
        before = self.thread(status='open')
        row = {'id': 't', 'status': 'resolved', 'update': ''}
        after = record_changes.update_thread(deepcopy(before), row, 's2', 'req')
        self.assertEqual(
            record_changes.changed_fields(before, after, record_changes.THREAD_FIELDS),
            [{'label': 'Status', 'before': 'open', 'after': 'resolved'}],
        )

    def test_added_text_shows_only_what_is_added_after_the_end_of_the_old_text(self):
        before = self.thread(detail='x' * 500 + ' It fell.')
        row = {'id': 't', 'status': 'open', 'update': ' It was found. '}
        after = record_changes.update_thread(deepcopy(before), row, 's2', 'req')
        [shown] = record_changes.changed_fields(before, after, record_changes.THREAD_FIELDS)
        self.assertEqual(shown['label'], 'Detail')
        self.assertEqual(shown['added'], 'It was found.')
        self.assertTrue(shown['before'].startswith('…') and shown['before'].endswith(' It fell.'))
        self.assertEqual(len(shown['before']), record_changes.TAIL + 1)
        self.assertEqual(after['detail'], before['detail'] + '\n\nIt was found.')

    def test_text_added_to_an_empty_field_has_nothing_before_it(self):
        before = self.thread()
        row = {'id': 't', 'status': 'open', 'update': 'First news.'}
        after = record_changes.update_thread(deepcopy(before), row, 's2', 'req')
        self.assertEqual(
            record_changes.changed_fields(before, after, record_changes.THREAD_FIELDS),
            [{'label': 'Detail', 'before': '', 'added': 'First news.'}],
        )

    def test_a_change_that_leaves_the_shown_fields_alone_shows_nothing(self):
        before = self.thread(status='open')
        row = {'id': 't', 'status': 'open', 'update': ''}
        after = record_changes.update_thread(deepcopy(before), row, 's2', 'req')
        self.assertEqual(
            record_changes.changed_fields(before, after, record_changes.THREAD_FIELDS), []
        )
        # The session is still noted on the thread: only the preview leaves it out.
        self.assertEqual(after['sessions'], ['s2'])

    def test_a_blank_codex_field_in_a_change_keeps_what_the_entry_has(self):
        before = shapes.CODEX_ENTRY.new(
            id='e', type='npc', name='E', status='alive', group='Watch', notes='Stern.'
        )
        row = {'id': 'e', 'status': ' ', 'group': 'Freed', 'notes': ''}
        after = record_changes.update_entry(deepcopy(before), row, 's2')
        self.assertEqual(
            record_changes.changed_fields(before, after, record_changes.ENTRY_FIELDS),
            [{'label': 'Group', 'before': 'Watch', 'after': 'Freed'}],
        )
        self.assertEqual((after['status'], after['notes']), ('alive', 'Stern.'))


if __name__ == '__main__':
    unittest.main()
