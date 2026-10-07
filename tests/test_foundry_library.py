"""World discovery and read-only media/snapshot boundaries with synthetic data."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'DM'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import campaign
import foundry_library
import leveldb_writer as writer


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

    def test_pushed_snapshot_beyond_the_limit_is_trimmed_and_counted(self):
        payload = self.snapshot()
        payload['documents']['items'] = [{'id': 'b', 'name': 'B'}, {'id': 'a', 'name': 'A'}]
        with patch.object(foundry_library, 'MAX_DOCUMENTS', 1):
            clean = foundry_library.normalize_snapshot(
                payload, foundry_library.selected_world(), origin='live'
            )
        self.assertEqual([item['id'] for item in clean['documents']['items']], ['a'])
        self.assertEqual(clean['omitted'], {'items': 1})

    def database(self, name, documents, embedded=()):
        """Write a world database: {id: document} plus (sublevel, parent, id, document) rows."""
        rows = [
            (f'!{name}!{key}'.encode(), json.dumps(document).encode())
            for key, document in documents.items()
        ]
        rows += [
            (f'!{name}.{sub}!{parent}.{key}'.encode(), json.dumps(document).encode())
            for sub, parent, key, document in embedded
        ]
        writer.database(self.world / 'data' / name, logs=[writer.log([rows])])

    def world_folder(self):
        self.database('folders', {'f1': {'_id': 'f1', 'name': 'Handouts', 'type': 'JournalEntry'}})
        text = '<p>Hidden<br>door</p><script>x()</script><p>&amp; more</p>'
        self.database(
            'journal',
            {
                'j1': {'_id': 'j1', 'name': 'Legend', 'folder': 'f1', 'pages': ['p2', 'p1']},
                'j2': {'_id': 'j2', 'name': 'Empty', 'folder': None, 'pages': []},
            },
            [
                (
                    'pages',
                    'j1',
                    'p1',
                    {'_id': 'p1', 'name': 'Second', 'sort': 200, 'text': {'content': text}},
                ),
                (
                    'pages',
                    'j1',
                    'p2',
                    {'_id': 'p2', 'name': 'First', 'sort': 100, 'src': 'maps/a.png'},
                ),
            ],
        )
        mira = {
            '_id': 'a1',
            'name': 'Mira',
            'type': 'npc',
            'img': 'tokens/mira.png',
            'system': {'details': {'biography': {'value': '<p>Scout</p>'}}},
        }
        self.database(
            'actors',
            {
                'a1': mira,
                # the key is the document's identity even when the stored document omits `_id`
                'a2': {'name': 'Bob', 'system': {'description': {'value': 'Fallback'}}},
                'a3': {'_id': 'a3', 'type': 'npc'},
            },
            [('items', 'a1', 'i9', {'_id': 'i9', 'name': 'Embedded sword'})],
        )
        key = {'_id': 'i1', 'name': 'Key', 'system': {'description': {'value': '<b>Brass</b>'}}}
        self.database('items', {'i1': key})
        self.database(
            'scenes',
            {
                's1': {'_id': 's1', 'name': 'Keep', 'background': {'src': 'a.png'}, 'thumb': 't'},
                's2': {'_id': 's2', 'name': 'Moat', 'background': {'src': None}, 'thumb': 't2'},
            },
        )

    def test_import_into_codex_keeps_studio_edits(self):
        world = foundry_library.selected_world()
        snapshot = foundry_library.normalize_snapshot(self.snapshot(), world)
        codex = {'entries': [{'id': 'mine', 'type': 'npc', 'name': 'Mine'}]}
        report = foundry_library.import_into_codex(snapshot, codex)
        self.assertEqual(
            report, {'added': 3, 'updated': 0, 'kept': 0, 'unchanged': 0, 'skipped': 0}
        )
        self.assertEqual(
            {entry['name']: entry['type'] for entry in codex['entries'][1:]},
            {'Mira': 'npc', 'Key': 'item', 'Keep': 'place'},
        )
        self.assertEqual(foundry_library.import_into_codex(snapshot, codex)['unchanged'], 3)
        mira = next(entry for entry in codex['entries'] if entry['name'] == 'Mira')
        key = next(entry for entry in codex['entries'] if entry['name'] == 'Key')
        mira['notes'] = 'Rewritten in Studio'
        for document in snapshot['documents']['actors'] + snapshot['documents']['items']:
            document['summary'] = 'Changed in Foundry'
        report = foundry_library.import_into_codex(snapshot, codex)
        self.assertEqual((report['updated'], report['kept'], report['unchanged']), (1, 1, 1))
        self.assertEqual(mira['notes'], 'Rewritten in Studio')
        self.assertEqual(key['notes'], 'Changed in Foundry')
        self.assertEqual(len({entry['id'] for entry in codex['entries']}), 4)

    def test_import_selection_skips_compendium_copies_and_unchosen_folders(self):
        payload = self.snapshot()
        payload['documents']['items'][0]['compendium'] = True
        payload['documents']['actors'][0]['folder'] = 'Allies'
        snapshot = foundry_library.normalize_snapshot(payload, foundry_library.selected_world())
        self.assertTrue(snapshot['documents']['items'][0]['compendium'])
        codex = {'entries': []}
        report = foundry_library.import_into_codex(snapshot, codex)
        self.assertEqual((report['added'], report['skipped']), (2, 1))
        self.assertEqual({entry['name'] for entry in codex['entries']}, {'Mira', 'Keep'})
        codex = {'entries': []}
        report = foundry_library.import_into_codex(snapshot, codex, ['Allies'])
        self.assertEqual(
            ([entry['name'] for entry in codex['entries']], report['skipped']), (['Mira'], 2)
        )
        report = foundry_library.import_into_codex(snapshot, codex, [''])
        self.assertEqual(
            sorted(entry['name'] for entry in codex['entries']), ['Keep', 'Key', 'Mira']
        )
        self.assertEqual(report['skipped'], 1)

    def test_read_world_marks_compendium_copies(self):
        self.database(
            'items',
            {
                'i2': {
                    '_id': 'i2',
                    'name': 'Rope',
                    'flags': {'core': {'sourceId': 'Compendium.x.y'}},
                },
                'i3': {'_id': 'i3', 'name': 'Own', '_stats': {}},
            },
        )
        items = foundry_library.read_world(foundry_library.selected_world())['documents']['items']
        self.assertEqual(
            {item['name']: item['compendium'] for item in items}, {'Own': False, 'Rope': True}
        )

    def test_import_provenance_separates_worlds_and_legacy_entries(self):
        first = foundry_library.normalize_snapshot(
            self.snapshot(), foundry_library.selected_world()
        )
        codex = {'entries': []}
        self.assertEqual(foundry_library.import_into_codex(first, codex)['added'], 3)
        old = next(entry for entry in codex['entries'] if entry['foundry']['uuid'] == 'Actor.a1')
        old['foundry'].pop('world_key')  # an entry made by the earlier importer has no source world
        self.assertEqual(foundry_library.import_into_codex(first, codex)['added'], 1)
        self.assertEqual(old['name'], 'Mira')

        self.settings_file.write_text(json.dumps({'world_path': str(self.other)}), encoding='utf-8')
        second = self.snapshot()
        second['world'].update(id='other-world', title='Other')
        second['documents']['actors'][0]['name'] = 'Someone else'
        second = foundry_library.normalize_snapshot(second, foundry_library.selected_world())
        self.assertEqual(foundry_library.import_into_codex(second, codex)['added'], 3)
        actors = [entry for entry in codex['entries'] if entry['foundry']['uuid'] == 'Actor.a1']
        self.assertEqual({entry['name'] for entry in actors}, {'Mira', 'Someone else'})
        self.assertEqual(len({entry['id'] for entry in codex['entries']}), len(codex['entries']))

    def test_import_only_serves_supported_images_from_the_selected_world(self):
        image = self.world / 'portrait.png'
        image.write_bytes(b'synthetic image')
        snapshot = self.snapshot()
        snapshot['documents']['actors'][0]['image'] = 'worlds/fixture-world/portrait.png'
        snapshot['documents']['items'][0]['image'] = 'https://example.test/item.png'
        clean = foundry_library.normalize_snapshot(snapshot, foundry_library.selected_world())
        codex = {'entries': []}
        foundry_library.import_into_codex(clean, codex)
        actor = next(entry for entry in codex['entries'] if entry['type'] == 'npc')
        item = next(entry for entry in codex['entries'] if entry['type'] == 'item')
        source_key = actor['foundry']['world_key']
        self.assertEqual(actor['image'], 'worlds/fixture-world/portrait.png')
        self.assertEqual(item['image'], '')
        self.assertTrue(foundry_library.media_file(actor['image'], source_key)[0].samefile(image))
        self.settings_file.write_text(json.dumps({'world_path': str(self.other)}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'different connected world'):
            foundry_library.media_file(actor['image'], source_key)

    def test_reads_documents_from_the_world_folder(self):
        self.world_folder()
        world = foundry_library.selected_world()
        self.assertIsNotNone(foundry_library.source_stamp(world))
        snapshot = foundry_library.read_world(world)
        self.assertEqual(snapshot['source'], 'folder')
        documents = snapshot['documents']
        self.assertEqual([item['name'] for item in documents['scenes']], ['Keep', 'Moat'])
        self.assertEqual([item['image'] for item in documents['scenes']], ['a.png', 't2'])
        empty, legend = documents['journals']
        self.assertEqual((legend['folder'], legend['uuid']), ('Handouts', 'JournalEntry.j1'))
        self.assertEqual(empty['folder'], '')
        self.assertEqual([page['name'] for page in legend['pages']], ['First', 'Second'])
        self.assertEqual(legend['pages'][0]['image'], 'maps/a.png')
        self.assertEqual(legend['pages'][1]['text'], 'Hidden\ndoor\n& more')
        self.assertEqual([item['name'] for item in documents['actors']], ['Bob', 'Mira'])
        self.assertEqual([item['summary'] for item in documents['actors']], ['Fallback', 'Scout'])
        self.assertEqual(documents['actors'][1]['image'], 'tokens/mira.png')
        self.assertEqual(documents['items'][0]['summary'], 'Brass')
        self.assertEqual(snapshot['omitted'], {})

    def test_world_folder_snapshot_goes_stale_only_when_files_change(self):
        self.world_folder()
        world = foundry_library.selected_world()
        snapshot = foundry_library.read_world(world)
        status = foundry_library.library(snapshot, kind='journals')
        self.assertTrue(status['readable'])
        self.assertEqual(
            (status['snapshot']['source'], status['snapshot']['stale']), ('folder', False)
        )
        self.database(
            'items', {'i1': {'_id': 'i1', 'name': 'Key'}, 'i2': {'_id': 'i2', 'name': 'Lamp'}}
        )
        self.assertTrue(foundry_library.library(snapshot)['snapshot']['stale'])
        refreshed = foundry_library.read_world(world)
        self.assertEqual(len(refreshed['documents']['items']), 2)
        self.assertFalse(foundry_library.library(refreshed)['snapshot']['stale'])
        macro = foundry_library.library(foundry_library.normalize_snapshot(self.snapshot(), world))
        self.assertEqual(
            (macro['snapshot']['source'], macro['snapshot']['stale']), ('macro', False)
        )

    def test_manifest_change_marks_folder_snapshot_stale(self):
        self.world_folder()
        world = foundry_library.selected_world()
        snapshot = foundry_library.read_world(world)
        manifest = self.world / 'data' / 'journal' / 'MANIFEST-000001'
        manifest.write_bytes(
            manifest.read_bytes() + writer.physical_records([writer.manifest_edit()])
        )
        self.assertTrue(foundry_library.library(snapshot)['snapshot']['stale'])

    def test_reads_older_worlds_that_keep_one_document_per_line(self):
        lines = [
            {'_id': 'i1', 'name': 'Key', 'system': {'description': {'value': 'Old'}}},
            {'_id': 'i2', 'name': 'Lamp'},
            {'_id': 'i1', 'name': 'Key', 'system': {'description': {'value': 'New'}}},
            {'$$indexCreated': {'fieldName': '_id'}},
            {'_id': 'i2', '$$deleted': True},
        ]
        (self.world / 'data').mkdir()
        content = '\n'.join(json.dumps(line) for line in lines) + '\nnot json\n'
        (self.world / 'data' / 'items.db').write_text(content, encoding='utf-8')
        snapshot = foundry_library.read_world(foundry_library.selected_world())
        self.assertEqual(
            [(item['name'], item['summary']) for item in snapshot['documents']['items']],
            [('Key', 'New')],
        )

    def test_world_without_databases_points_to_the_macro(self):
        self.assertFalse(foundry_library.library(None)['readable'])
        with self.assertRaisesRegex(ValueError, 'export macro'):
            foundry_library.read_world(foundry_library.selected_world())

    def test_damaged_referenced_database_is_reported_not_partly_read(self):
        self.world_folder()
        # A corrupt unreferenced file is left behind by compaction and must not
        # block a read; damage to a referenced file must not yield partial data.
        (self.world / 'data' / 'journal' / '000009.ldb').write_bytes(b'\x07' * 200)
        foundry_library.read_world(foundry_library.selected_world())
        (self.world / 'data' / 'journal' / '000001.log').write_bytes(b'\0\0\0\0\x01\0\x09x')
        with self.assertRaisesRegex(ValueError, 'damaged'):
            foundry_library.read_world(foundry_library.selected_world())

    def test_documents_beyond_the_limit_are_counted(self):
        self.world_folder()
        with patch.object(foundry_library, 'MAX_DOCUMENTS', 1):
            snapshot = foundry_library.read_world(foundry_library.selected_world())
        self.assertEqual(snapshot['omitted'], {'scenes': 1, 'journals': 1, 'actors': 1})
        self.assertEqual(len(snapshot['documents']['actors']), 1)
        self.assertEqual(foundry_library.library(snapshot)['snapshot']['omitted']['journals'], 1)


if __name__ == '__main__':
    unittest.main()
