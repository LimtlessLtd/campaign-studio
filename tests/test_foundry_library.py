"""World discovery and read-only media/snapshot boundaries with synthetic data."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'DM'))

import campaign
import foundry_library


class FoundryLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='studio-library-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'Foundry User Data'
        self.world = self.root / 'Data' / 'worlds' / 'fixture-world'
        self.world.mkdir(parents=True)
        (self.world / 'world.json').write_text(
            json.dumps(
                {
                    'id': 'fixture-world',
                    'title': 'Fixture World',
                    'system': 'dnd5e',
                    'coreVersion': '12.331',
                }
            ),
            encoding='utf-8',
        )
        self.other = self.root / 'Data' / 'worlds' / 'other-world'
        self.other.mkdir()
        (self.other / 'world.json').write_text(
            json.dumps({'id': 'other-world', 'title': 'Other', 'system': 'dnd5e'}),
            encoding='utf-8',
        )
        fixture = campaign.Campaign(Path(self.temp.name) / 'DM')
        self.settings_file = Path(fixture.settings)
        self.settings_file.parent.mkdir(parents=True)
        self.settings_file.write_text(json.dumps({'world_path': str(self.world)}), encoding='utf-8')
        self.addCleanup(campaign.activate, campaign.activate(fixture))

    def snapshot(self):
        return {
            'format': foundry_library.SNAPSHOT_FORMAT,
            'schema': 1,
            'world': {
                'id': 'fixture-world',
                'title': 'Fixture World',
                'system': 'dnd5e',
                'coreVersion': '12.331',
            },
            'exportedAt': '2026-10-03T12:00:00Z',
            'documents': {
                'scenes': [
                    {'id': 'abc', 'uuid': 'Scene.abc', 'name': 'Keep', 'summary': 'Old ruins'}
                ],
                'journals': [
                    {
                        'id': 'j1',
                        'uuid': 'JournalEntry.j1',
                        'name': 'Legend',
                        'pages': [{'id': 'p1', 'name': 'Clue', 'text': 'Secret clue'}],
                    }
                ],
                'actors': [{'id': 'a1', 'name': 'Mira', 'type': 'npc', 'summary': 'Scout'}],
                'items': [{'id': 'i1', 'name': 'Key'}],
            },
        }

    def test_discover_worlds_and_browse_own_media(self):
        found = foundry_library.discover(str(self.root / 'Data'))
        self.assertEqual([world['title'] for world in found['worlds']], ['Fixture World', 'Other'])
        (self.world / 'maps').mkdir()
        (self.world / 'maps' / 'keep.png').write_bytes(b'fake image')
        (self.other / 'secret.png').write_bytes(b'other')
        (self.root / 'Data' / 'shared.jpg').write_bytes(b'shared')
        (self.root / 'Data' / 'systems').mkdir()
        (self.root / 'Data' / 'systems' / 'icon.png').write_bytes(b'system')
        listing = foundry_library.library(None, kind='assets')
        self.assertEqual(
            [item['path'] for item in listing['items']],
            ['shared.jpg', 'worlds/fixture-world/maps/keep.png'],
        )
        path, mime = foundry_library.media_file('worlds/fixture-world/maps/keep.png')
        self.assertEqual((path.name, mime), ('keep.png', 'image/png'))
        for unsafe in ('../other.png', 'worlds/other-world/secret.png', 'systems/icon.png'):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                foundry_library.media_file(unsafe)

    def test_snapshot_is_bound_to_selected_world_and_remains_read_only(self):
        world = foundry_library.selected_world()
        clean = foundry_library.normalize_snapshot(self.snapshot(), world)
        result = foundry_library.library(clean, kind='journals', query='legend')
        self.assertEqual(result['counts']['actors'], 1)
        self.assertEqual(result['items'][0]['pages'][0]['text'], 'Secret clue')
        self.assertEqual(result['snapshot']['core_version'], '12.331')
        other = self.snapshot()
        other['world']['id'] = 'other-world'
        with self.assertRaisesRegex(ValueError, 'different Foundry world'):
            foundry_library.normalize_snapshot(other, world)
        self.settings_file.write_text(json.dumps({'world_path': str(self.other)}), encoding='utf-8')
        self.assertIsNone(foundry_library.library(clean)['snapshot'])
        self.assertEqual(foundry_library.library(clean)['items'], [])

    def test_invalid_snapshot_document_ids_are_rejected(self):
        payload = self.snapshot()
        payload['documents']['items'] = [{'id': 'same'}, {'id': 'same'}]
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            foundry_library.normalize_snapshot(payload, foundry_library.selected_world())


if __name__ == '__main__':
    unittest.main()
