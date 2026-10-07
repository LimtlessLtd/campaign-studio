"""Large-campaign prompt selection stays bounded without losing task links."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'DM'))

import context as prompt_context


class ContextTests(unittest.TestCase):
    def test_two_thousand_entries_fit_and_keep_links_without_provenance(self):
        entries = [
            {
                'id': f'entry-{i:04}',
                'type': 'npc',
                'name': f'Person {i:04}',
                'public': 'One sentence. ' + 'Long lore. ' * 200,
                'secrets': 'Hidden canon. ' * 200,
                'foundry': {'imported': {'notes': 'Duplicate private source'}},
                'image': 'C:/private/portrait.png',
            }
            for i in range(2_000)
        ]
        linked = 'entry-1999'
        args = (
            'Draft an NPC.\nREFERENCE DATA:\n',
            {
                'campaign': {'name': 'Fixture', 'world': {'path': 'C:/private/world'}},
                'request': {'text': 'Person 0012'},
            },
            {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
            entries,
            [{'id': 'thread-1', 'title': 'The clue', 'status': 'open', 'detail': 'Find it'}],
            'Person 0012',
        )
        prompt, preview = prompt_context.build(
            *args, linked_ids=[linked], pins=['entry-0001'], budget_chars=16_000
        )
        again, _ = prompt_context.build(
            *args, linked_ids=[linked], pins=['entry-0001'], budget_chars=16_000
        )
        data = json.loads(prompt.split('REFERENCE DATA:\n', 1)[1])
        self.assertEqual(prompt, again)
        self.assertLessEqual(preview['prompt_chars'] + preview['schema_chars'] + 2_048, 16_000)
        self.assertIn(linked, {entry['id'] for entry in data['codex']})
        self.assertIn('entry-0001', {entry['id'] for entry in data['codex']})
        self.assertIn('entry-0012', {entry['id'] for entry in data['codex']})
        self.assertNotIn('foundry', prompt)
        self.assertNotIn('C:/private', prompt)
        self.assertGreater(preview['omitted_entries'], 0)
        self.assertEqual(preview['sections'][0]['count'], 2)

    def test_linked_identity_survives_long_notes_and_invalid_pins_fail(self):
        entries = [
            {'id': f'e-{i}', 'name': f'Entry {i}', 'type': 'npc', 'notes': 'x' * 20_000}
            for i in range(12)
        ]
        prompt, _ = prompt_context.build(
            'REFERENCE DATA:\n',
            {},
            {},
            entries,
            [],
            '',
            linked_ids=[e['id'] for e in entries],
            budget_chars=16_000,
        )
        data = json.loads(prompt.split('REFERENCE DATA:\n', 1)[1])
        self.assertEqual(len(data['codex']), 12)
        self.assertTrue(all(entry['name'] for entry in data['codex']))
        with self.assertRaisesRegex(ValueError, 'no longer exists'):
            prompt_context.clean_pins(['missing'], entries)
        with self.assertRaisesRegex(ValueError, '20'):
            prompt_context.clean_pins(['e-0'] * 21, entries)


if __name__ == '__main__':
    unittest.main()
