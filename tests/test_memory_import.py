"""Synthetic, reviewed campaign-memory imports through the HTTP boundary."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM' / 'tools'))

import test_workflows as fixtures  # noqa: E402
from test_workflows import campaign_core, shapes  # noqa: E402


class MemoryImportTests(unittest.TestCase):
    def setUp(self):
        self.studio = fixtures.StudioIntegration()
        self.studio.setUp()
        self.addCleanup(self.studio.tearDown)
        self.source = tempfile.TemporaryDirectory(prefix='studio-memory-source-')
        self.addCleanup(self.source.cleanup)
        self.root = Path(self.source.name)

    def preview(self, kind, path='', folders=None):
        return self.studio.request(
            '/api/memory/preview', {'kind': kind, 'path': str(path), 'folders': folders or []}
        )

    def apply(self, kind, preview, path='', folders=None, selected=None, expected=200):
        return self.studio.request(
            '/api/memory/apply',
            {
                'kind': kind,
                'path': str(path),
                'folders': folders or [],
                'fingerprint': preview['fingerprint'],
                'selected': selected or [item['key'] for item in preview['items']],
            },
            expected=expected,
        )

    def test_legacy_folder_is_reviewed_linked_and_idempotent(self):
        data = self.root / 'DM' / 'data'
        (data / 'prep').mkdir(parents=True)
        codex = {
            'entries': [
                {'id': 'hero', 'type': 'pc', 'name': 'Hero', 'public': 'Known adventurer'},
                {
                    'id': 'keep',
                    'type': 'place',
                    'name': 'Old Keep',
                    'notes': 'The bell was stolen.',
                    'related': ['hero'],
                    'image': 'private/old.png',
                },
            ]
        }
        (data / 'codex.json').write_text(json.dumps(codex), encoding='utf-8')
        (data / 'threads.json').write_text(
            json.dumps({'threads': [{'id': 'bell', 'title': 'Missing bell', 'pcs': ['hero']}]}),
            encoding='utf-8',
        )
        (data / 'prep' / 's2.json').write_text(
            json.dumps({'n': 2, 'title': 'Second session', 'log': {'summary': 'Found the keep.'}}),
            encoding='utf-8',
        )
        before = (data / 'codex.json').read_bytes()
        shown = self.preview('folder', self.root)
        self.assertEqual(
            {item['title'] for item in shown['items']},
            {'Hero', 'Old Keep', 'Missing bell', 'Second session'},
        )
        self.assertIsNone(self.studio.stored('prep/s2'))
        hero_key = next(item['key'] for item in shown['items'] if item['title'] == 'Hero')
        self.assertEqual(self.apply('folder', shown, self.root, selected=[hero_key])['added'], 1)
        self.assertEqual(len(self.studio.stored('codex')['entries']), 1)
        self.assertIsNone(self.studio.stored('prep/s2'))
        self.assertEqual(self.apply('folder', shown, self.root)['added'], 3)
        self.assertEqual((data / 'codex.json').read_bytes(), before)
        self.assertEqual(self.studio.stored('prep/s2')['log']['summary'], 'Found the keep.')
        entries = self.studio.stored('codex')['entries']
        hero = next(row for row in entries if row['name'] == 'Hero')
        keep = next(row for row in entries if row['name'] == 'Old Keep')
        self.assertEqual(keep['related'], [hero['id']])
        self.assertEqual(keep['image'], '')
        self.assertEqual(self.studio.stored('threads')['threads'][0]['pcs'], [hero['id']])
        self.assertEqual(self.apply('folder', shown, self.root)['skipped'], 4)
        self.studio.assert_shaped()

    def test_summary_file_fills_empty_log_and_refuses_changed_preview(self):
        first = self.root / 'Session 1.md'
        first.write_text('# First session\nThe party escaped.\n', encoding='utf-8')
        second = self.root / 's2.json'
        second.write_text(
            json.dumps(
                {
                    'session': 's2',
                    'title': 'Second session',
                    'summary': 'They returned.',
                    'outcomes': ['Gate opened.'],
                }
            ),
            encoding='utf-8',
        )
        self.studio.seed('prep/s2', shapes.PREP.new(n=2, title='Existing prep'))
        settings = self.studio.stored('settings')
        settings['world_path'] = str(self.root / 'world-that-moved')
        campaign_core.write_doc('settings', settings)
        shown = self.preview('summaries', self.root)
        self.assertEqual(len(shown['items']), 2)
        second.write_text(json.dumps({'session': 's2', 'summary': 'Changed.'}), encoding='utf-8')
        self.apply('summaries', shown, self.root, expected=409)
        self.assertIsNone(self.studio.stored('prep/s1'))
        fresh = self.preview('summaries', self.root)
        report = self.apply('summaries', fresh, self.root)
        self.assertEqual(report, {'ok': True, 'added': 1, 'filled': 1, 'skipped': 0})
        self.assertEqual(self.studio.stored('prep/s1')['log']['summary'], 'The party escaped.')
        self.assertEqual(self.studio.stored('prep/s2')['log']['summary'], 'Changed.')
        self.assertEqual(self.studio.stored('prep/s2')['title'], 'Existing prep')
        self.studio.assert_shaped()

    def test_lore_folder_selection_imports_only_selected_journals(self):
        world = self.studio.world
        snapshot = {
            'format': 'campaign-studio-foundry-library',
            'schema': 1,
            'world': {
                'id': 'fixture-world',
                'title': 'Fixture',
                'system': 'dnd5e',
                'path': str(world),
                'core_version': '12.331',
            },
            'exported_at': '2026-10-08',
            'source': 'macro',
            'stamp': '',
            'omitted': {},
            'documents': {
                'scenes': [],
                'actors': [],
                'items': [],
                'journals': [
                    {
                        'id': 'l1',
                        'name': 'Old history',
                        'folder': 'Lore',
                        'folder_id': 'lore-folder',
                        'folder_path': 'History / Lore',
                        'summary': '',
                        'pages': [{'id': 'p1', 'name': 'Fall', 'text': 'A city fell.'}],
                    },
                    {
                        'id': 'j2',
                        'name': 'Jokes',
                        'folder': 'Chat',
                        'folder_id': 'chat-folder',
                        'folder_path': 'Chat',
                        'summary': 'Not canon',
                        'pages': [],
                    },
                ],
            },
        }
        campaign_core.write_doc('foundry-library', snapshot)
        folders = self.studio.request('/api/memory/lore-folders', method='GET')['folders']
        self.assertEqual({row['id'] for row in folders}, {'lore-folder', 'chat-folder'})
        shown = self.preview('lore', folders=['lore-folder'])
        self.assertEqual([item['title'] for item in shown['items']], ['Old history'])
        self.assertEqual(self.apply('lore', shown, folders=['lore-folder'])['added'], 1)
        entry = self.studio.stored('codex')['entries'][0]
        self.assertEqual(entry['type'], 'lore')
        self.assertIn('A city fell.', entry['notes'])
        self.assertEqual(entry['foundry']['uuid'], 'JournalEntry.l1')
        self.assertEqual(len(self.studio.stored('codex')['entries']), 1)
        self.studio.assert_shaped()


if __name__ == '__main__':
    unittest.main()
