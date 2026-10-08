"""Versioned campaign data: migration with verified backups, restore and future-version refusal."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import campaign
import campaign_core
import migrate
import foundry_library
import records
import schema
import shapes


def legacy_campaign(dm):
    """A synthetic campaign in the shapes written by 0.1.x code paths and hand edits."""
    documents = {
        'data/settings.json': {'campaign_name': 'Fixture', 'world_path': ''},
        'data/codex.json': {
            'entries': [
                {
                    'id': 'ui-npc',
                    'type': 'npc',
                    'name': 'Created in the codex',
                    'group': '',
                    'status': '',
                    'public': 'Known',
                    'secrets': '',
                    'notes': '',
                    'image': '',
                    'files': [],
                    'tags': ['harbour'],
                },
                {'id': 'old-place', 'type': 'place', 'name': 'Hand edited', 'tags': None},
            ]
        },
        'data/threads.json': {
            'threads': [
                {'id': 'ui', 'title': 'From the UI', 'pcs': [], 'status': 'open', 'detail': ''},
                {'id': 'old', 'title': 'Hand edited', 'status': 'planned', 'custom': 7},
            ]
        },
        'data/prep/s1.json': {
            'n': 1,
            'title': 'Session 1',
            'date': '',
            'status': 'planning',
            'recap': '',
            'goals': ['Reach the harbour'],
            'threads': [],
            'scenes': [{'id': 'gate', 'title': 'At the gate'}],
            'checklist': [{'text': 'Print map', 'done': True}],
            'notes': 'Keep.',
            'loot': [],
        },
        'data/prep/s2.json': {'scenes': [], 'goals': []},
        'data/inbox.json': {'items': [{'id': 'req', 'kind': 'battle map', 'status': 'new'}]},
        'data/maps/index.json': {
            'items': [
                {
                    'slug': 'harbour',
                    'name': 'Harbour',
                    'theme': 'imported',
                    'imported': True,
                    'cells': [30, 20],
                    'image': 'DM/maps/harbour/harbour.png',
                    'scene': 'DM/maps/harbour/harbour.foundry.json',
                    'key': 'DM/maps/harbour/key.json',
                    'stocked': True,
                    'session': '',
                    'updated': '2026-10-02T12:00:00',
                }
            ]
        },
        'data/workflows/wf-old.json': {'id': 'wf-old', 'status': 'applied'},
        'maps/harbour/key.json': {
            'map': 'Harbour',
            'areas': [
                {
                    'n': 1,
                    'name': 'Pier',
                    'kind': 'dock',
                    'at': [2, 3],
                    'text': 'Salt spray.',
                    'creatures': '',
                    'loot': [],
                    'events': [],
                    'npcs': ['ui-npc'],
                    'items': [],
                    'journal': [],
                    'images': [],
                }
            ],
            'events': [],
            'notes': '',
            'stocked': True,
            'session': '',
            'images': [],
        },
    }
    for rel, value in documents.items():
        path = dm / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=1), encoding='utf-8')
    (dm / 'maps/harbour/plan.txt').write_text('name: Harbour\n---\n..\n', encoding='utf-8')
    (dm / 'data/.history/codex').mkdir(parents=True)
    (dm / 'data/.history/codex/20261002-120000-000000.json').write_text('{"entries": []}')
    (dm / 'data/jobs').mkdir()
    (dm / 'data/jobs/20261002-120000-ab12.json').write_text('{"status": "done"}')


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dm = Path(self.temp.name) / 'DM'
        self.data, self.maps, self.backups = (str(self.dm / n) for n in ('data', 'maps', 'backups'))
        legacy_campaign(self.dm)
        self.original = self.snapshot()

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        return {
            str(path.relative_to(self.dm)).replace('\\', '/'): path.read_bytes()
            for path in self.dm.rglob('*')
            if path.is_file()
            and 'backups' not in path.parts
            and path.name != schema.MARKER
            and path.suffix != '.lock'  # storage.file_lock keeps its lock files by design
        }

    def read(self, rel):
        return json.loads((self.dm / rel).read_text(encoding='utf-8'))

    def migrate(self, **options):
        return schema.migrate(self.data, self.maps, self.backups, **options)

    def test_unversioned_campaign_is_backed_up_then_completed(self):
        self.assertEqual(schema.version(self.data, self.maps), 0)
        planned = self.migrate(dry_run=True)
        self.assertEqual(planned['status'], 'needs migration')
        self.assertEqual(
            planned['changes'],
            [
                'data/codex.json',
                'data/threads.json',
                'data/prep/s1.json',
                'data/prep/s2.json',
                'maps/harbour/key.json',
                'data/codex/ui-npc.json',
                'data/codex/old-place.json',
                'data/threads/ui.json',
                'data/threads/old.json',
            ],
        )
        self.assertEqual(self.snapshot(), self.original)
        self.assertFalse(Path(self.backups).exists())

        result = self.migrate()

        self.assertEqual(
            (result['status'], result['from'], result['version']), ('migrated', 0, schema.CURRENT)
        )
        self.assertEqual(schema.version(self.data, self.maps), schema.CURRENT)
        backup = Path(result['backup'])
        manifest = json.loads((backup / 'backup.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['version'], 0)
        saved = {entry['path'] for entry in manifest['files']}
        self.assertIn('data/workflows/wf-old.json', saved)
        self.assertIn('maps/harbour/key.json', saved)
        self.assertFalse(any('.history' in name or 'jobs' in name for name in saved))
        for name in saved:
            self.assertEqual((backup / name).read_bytes(), self.original[name])

        self.assertFalse((self.dm / 'data/codex.json').exists())
        self.assertFalse((self.dm / 'data/threads.json').exists())
        codex = [self.read('data/codex/' + ident + '.json') for ident in ('ui-npc', 'old-place')]
        self.assertEqual(codex[0], json.loads(self.original['data/codex.json'])['entries'][0])
        self.assertEqual((codex[1]['tags'], codex[1]['files'], codex[1]['public']), ([], [], ''))
        self.assertEqual(codex[1]['name'], 'Hand edited')
        threads = [self.read('data/threads/' + ident + '.json') for ident in ('ui', 'old')]
        self.assertEqual(
            (threads[1]['pcs'], threads[1]['source'], threads[1]['custom']), ([], '', 7)
        )
        self.assertEqual(threads[1]['status'], 'planned')
        self.assertEqual((threads[1]['entries'], threads[1]['sessions']), ([], []))
        s1 = self.read('data/prep/s1.json')
        self.assertEqual((s1['handouts'], s1['scenes'][0]['npcs']), ([], []))
        self.assertEqual((s1['notes'], s1['checklist'][0]['done']), ('Keep.', True))
        s2 = self.read('data/prep/s2.json')
        self.assertEqual((s2['checklist'], s2['loot'], s2['notes']), ([], [], ''))
        area = self.read('maps/harbour/key.json')['areas'][0]
        self.assertEqual((area['rooms'], area['threads'], area['npcs']), ([], [], ['ui-npc']))
        current = self.snapshot()
        for rel in ('data/settings.json', 'data/inbox.json', 'data/maps/index.json'):
            self.assertEqual(current[rel], self.original[rel])
        self.assertEqual(current['maps/harbour/plan.txt'], self.original['maps/harbour/plan.txt'])

        self.assertEqual(self.migrate()['status'], 'current')
        self.assertEqual(len(list(Path(self.backups).iterdir())), 1)

    def test_version_two_campaign_keeps_world_maps_and_gains_their_new_fields(self):
        self.migrate()
        schema.write_marker(self.data, 2, 'Synthetic version 2 campaign')
        (self.dm / 'data/world-maps.json').write_text(
            json.dumps({'maps': [{'id': 'w1', 'name': 'Realm', 'pins': [{'id': 'p1'}]}]})
        )

        result = self.migrate()

        self.assertEqual((result['from'], result['version']), (2, schema.CURRENT))
        self.assertEqual(result['changes'], ['data/world-maps.json'])
        world = self.read('data/world-maps.json')['maps'][0]
        self.assertEqual((world['name'], world['image']), ('Realm', ''))
        self.assertEqual(
            world['pins'][0], {'id': 'p1', 'label': '', 'map': '', 'x': 0.5, 'y': 0.5, 'note': ''}
        )

    def test_version_three_campaign_hashes_foundry_imports_instead_of_copying_them(self):
        schema.write_marker(self.data, 3, 'Synthetic version 3 campaign')
        values = {'name': 'Mira', 'group': 'Allies', 'notes': 'Summary', 'image': 'a.png'}
        codex = self.read('data/codex.json')
        codex['entries'].append(
            {
                'id': 'fvtt-a1',
                'type': 'npc',
                'name': 'Mira',
                'foundry': {'uuid': 'Actor.a1', 'world_key': 'w', 'imported': values},
            }
        )
        (self.dm / 'data/codex.json').write_text(json.dumps(codex))

        result = self.migrate()

        self.assertEqual((result['from'], result['version']), (3, schema.CURRENT))
        self.assertFalse((self.dm / 'data/codex.json').exists())
        link = self.read('data/codex/fvtt-a1.json')['foundry']
        self.assertEqual(
            link,
            {
                'uuid': 'Actor.a1',
                'world_key': 'w',
                'hash': foundry_library.imported_hash(values),
                'image': 'a.png',
            },
        )

    def test_version_five_prep_gains_an_empty_session_log(self):
        self.migrate()
        schema.write_marker(self.data, 5, 'Synthetic version 5 campaign')
        prep = self.dm / 'data/prep/s9.json'
        prep.parent.mkdir(exist_ok=True)
        prep.write_text(json.dumps({'n': 9, 'title': 'Nine'}))

        result = self.migrate()

        self.assertEqual((result['from'], result['version']), (5, schema.CURRENT))
        self.assertEqual(
            self.read('data/prep/s9.json')['log'], {'summary': '', 'notes': '', 'outcomes': []}
        )

    def test_version_six_campaign_splits_records_without_changing_foundry_links(self):
        schema.write_marker(self.data, 6, 'Synthetic version 6 campaign')
        codex = self.read('data/codex.json')
        codex['entries'][0]['foundry'] = {
            'uuid': 'Actor.a1',
            'world_key': 'w',
            'hash': 'saved-hash',
            'image': 'worlds/w/portrait.png',
        }
        (self.dm / 'data/codex.json').write_text(json.dumps(codex))

        result = self.migrate()

        self.assertEqual((result['from'], result['version']), (6, schema.CURRENT))
        self.assertFalse((self.dm / 'data/codex.json').exists())
        self.assertFalse((self.dm / 'data/threads.json').exists())
        self.assertEqual(
            self.read('data/codex/ui-npc.json')['foundry'], codex['entries'][0]['foundry']
        )
        self.assertEqual(self.read('data/threads/ui.json')['title'], 'From the UI')
        self.assertEqual(self.read('data/threads/old.json')['custom'], 7)

    def test_version_seven_prep_gains_session_plan_fields_without_losing_notes(self):
        schema.write_marker(self.data, 7, 'Synthetic version 7 campaign')
        path = self.dm / 'data/prep/s1.json'
        old = {
            'n': 1,
            'title': 'Session 1',
            'recap': 'A hand-written recap.',
            'scenes': [{'id': 'gate', 'title': 'At the gate', 'notes': 'Keep this twist.'}],
            'handouts': [{'id': 'notice', 'title': 'Notice', 'player_text': 'Read me.'}],
        }
        path.write_text(json.dumps(old))

        result = self.migrate()

        self.assertEqual((result['from'], result['version']), (7, schema.CURRENT))
        self.assertTrue(result['backup'])
        prep = self.read('data/prep/s1.json')
        self.assertEqual(prep['recap'], old['recap'])
        self.assertEqual(prep['pitch'], '')
        self.assertEqual(prep['scenes'][0]['notes'], 'Keep this twist.')
        self.assertEqual(prep['scenes'][0]['clues'], [])
        self.assertEqual(prep['scenes'][0]['encounter_detail']['creatures'], [])
        self.assertEqual(prep['handouts'][0]['image_prompt'], '')
        self.assertEqual(self.migrate()['status'], 'current')
        self.assertEqual(self.read('data/prep/s1.json'), prep)

    def test_version_eight_adds_session_plan_without_losing_thread_links(self):
        self.migrate()
        schema.write_marker(self.data, 8, 'Synthetic version 8 campaign')
        thread_path = self.dm / 'data/threads/ui.json'
        thread = self.read('data/threads/ui.json')
        thread['entries'] = ['ui-npc']
        thread['sessions'] = ['s1']
        thread_path.write_text(json.dumps(thread))
        prep_path = self.dm / 'data/prep/s1.json'
        prep = self.read('data/prep/s1.json')
        prep.pop('pitch', None)
        prep['scenes'][0].pop('clues', None)
        prep['handouts'] = [{'id': 'notice', 'title': 'Notice', 'player_text': 'Keep this.'}]
        prep_path.write_text(json.dumps(prep))

        result = self.migrate()

        self.assertEqual((result['from'], result['version']), (8, schema.CURRENT))
        self.assertTrue(result['backup'])
        self.assertEqual(self.read('data/threads/ui.json'), thread)
        migrated = self.read('data/prep/s1.json')
        self.assertEqual(migrated['pitch'], '')
        self.assertEqual(migrated['scenes'][0]['clues'], [])
        self.assertEqual(migrated['handouts'][0]['player_text'], 'Keep this.')
        self.assertEqual(migrated['handouts'][0]['image_prompt'], '')

    def test_version_one_campaign_gains_the_records_completed_in_version_two(self):
        self.migrate()
        schema.write_marker(self.data, 1, 'Synthetic version 1 campaign')
        (self.dm / 'data/art.json').write_text(
            json.dumps({'items': [{'id': 'art-1', 'prompt': 'A harbour at dusk'}]})
        )
        key = self.read('maps/harbour/key.json')
        key['areas'][0]['journal'] = [{'id': 'j1', 'title': 'Tide table'}]
        key['areas'][0]['events'] = [{'id': 'e1', 'trigger': 'Bell rings'}]
        (self.dm / 'maps/harbour/key.json').write_text(json.dumps(key))
        prep = self.read('data/prep/s1.json')
        prep['loot'] = [{'item': 'Rope'}]
        (self.dm / 'data/prep/s1.json').write_text(json.dumps(prep))

        result = self.migrate()

        self.assertEqual((result['from'], result['version']), (1, schema.CURRENT))
        self.assertEqual(
            result['changes'], ['data/art.json', 'data/prep/s1.json', 'maps/harbour/key.json']
        )
        art = self.read('data/art.json')['items'][0]
        self.assertEqual((art['status'], art['image'], art['codex']), ('queued', '', ''))
        area = self.read('maps/harbour/key.json')['areas'][0]
        self.assertEqual(
            area['journal'][0], {'id': 'j1', 'title': 'Tide table', 'text': '', 'secrets': ''}
        )
        self.assertEqual(area['events'][0]['effect'], '')
        self.assertEqual(
            self.read('data/prep/s1.json')['loot'][0], {'item': 'Rope', 'where': '', 'value': ''}
        )
        for rel, shape in (
            ('data/codex/ui-npc.json', shapes.CODEX_ENTRY),
            ('data/threads/ui.json', shapes.THREAD),
            ('data/art.json', shapes.ART),
            ('maps/harbour/key.json', shapes.MAP_KEY),
        ):
            self.assertEqual(shape.problems(self.read(rel)), [], rel)

    def test_restore_returns_documents_and_their_version(self):
        result = self.migrate()
        (self.dm / 'data/codex.json').write_text('{"entries": []}', encoding='utf-8')

        schema.restore(result['backup'], self.data, self.maps)

        self.assertEqual(self.snapshot(), self.original)
        self.assertEqual(schema.version(self.data, self.maps), 0)
        copy = Path(result['backup']) / 'data/threads.json'
        copy.write_text('{"threads": []}', encoding='utf-8')
        with self.assertRaisesRegex(schema.SchemaError, 'changed since'):
            schema.restore(result['backup'], self.data, self.maps)

    def test_restoring_schema_six_removes_schema_seven_record_folders(self):
        schema.write_marker(self.data, 6, 'Synthetic version 6 campaign')
        result = self.migrate()
        self.assertTrue((self.dm / 'data/codex/ui-npc.json').exists())
        self.assertTrue((self.dm / 'data/threads/ui.json').exists())

        schema.restore(result['backup'], self.data, self.maps)

        self.assertEqual(schema.version(self.data, self.maps), 6)
        self.assertFalse((self.dm / 'data/codex').exists())
        self.assertFalse((self.dm / 'data/threads').exists())
        self.assertEqual(self.read('data/codex.json')['entries'][0]['id'], 'ui-npc')
        self.assertEqual(self.read('data/threads.json')['threads'][0]['id'], 'ui')

    def test_restoring_schema_seven_removes_records_added_after_the_backup(self):
        self.migrate()
        saved = schema.backup(self.data, self.maps, self.backups, 'synthetic-current')
        extra = self.dm / 'data/codex/later.json'
        extra.write_text(json.dumps(shapes.CODEX_ENTRY.new(id='later', type='npc', name='Later')))

        schema.restore(saved, self.data, self.maps)

        self.assertEqual(schema.version(self.data, self.maps), schema.CURRENT)
        self.assertFalse(extra.exists())
        self.assertTrue((self.dm / 'data/codex/ui-npc.json').exists())

    def test_newer_schema_is_refused_without_changes(self):
        (self.dm / 'data' / schema.MARKER).write_text(
            json.dumps({'format': schema.FORMAT, 'version': schema.CURRENT + 1})
        )
        for action in (self.migrate, lambda: schema.check(self.data, self.maps)):
            with self.assertRaisesRegex(schema.SchemaError, 'supports up to'):
                action()
        self.assertEqual(self.snapshot(), self.original)
        self.assertFalse(Path(self.backups).exists())
        (self.dm / 'data' / schema.MARKER).write_text('{"version": "1"}')
        with self.assertRaisesRegex(schema.SchemaError, 'not a Campaign Studio schema'):
            self.migrate()

    def test_empty_campaign_is_stamped_only_once_it_has_documents(self):
        empty = Path(self.temp.name) / 'empty'
        data, maps, backups = (str(empty / n) for n in ('data', 'maps', 'backups'))
        (empty / 'data' / 'jobs').mkdir(parents=True)
        self.assertEqual(schema.migrate(data, maps, backups)['status'], 'new')
        self.assertFalse((empty / 'data' / schema.MARKER).exists())
        # Documents copied in later are still recognised as unversioned and migrated.
        (empty / 'data/threads.json').write_text('{"threads": null}', encoding='utf-8')
        result = schema.migrate(data, maps, backups)
        self.assertEqual((result['status'], result['from']), ('migrated', 0))
        self.assertTrue(Path(result['backup']).is_dir())
        self.assertFalse((empty / 'data/threads.json').exists())
        self.assertEqual(records.collection(data, 'threads')['threads'], [])
        self.assertEqual(schema.version(data, maps), schema.CURRENT)

    def test_a_new_campaign_records_its_schema_with_its_first_document(self):
        new = Path(self.temp.name) / 'new'
        fresh = campaign.Campaign(new)
        with campaign.using(fresh):
            campaign_core.write_doc('settings', {'campaign_name': 'Fixture'})
            # An older build then refuses it instead of treating it as unversioned 0.1.x data.
            self.assertEqual(schema.version(fresh.data, fresh.maps), schema.CURRENT)
            campaign_core.write_doc(
                records.document_name('codex', 'sample'),
                shapes.CODEX_ENTRY.new(id='sample', type='npc', name='Sample'),
            )
        marker = json.loads((new / 'data' / schema.MARKER).read_text(encoding='utf-8'))
        self.assertEqual(len(marker['history']), 1)
        # Unversioned documents already present are left for the next start to migrate.
        with campaign.using(campaign.Campaign(self.dm)):
            campaign_core.write_doc('threads', {'threads': []})
        self.assertEqual(schema.version(self.data, self.maps), 0)

    def test_unchanged_documents_are_stamped_without_a_backup(self):
        bare = Path(self.temp.name) / 'bare'
        (bare / 'data').mkdir(parents=True)
        (bare / 'data/settings.json').write_text('{"campaign_name": "Fixture"}')
        result = schema.migrate(str(bare / 'data'), str(bare / 'maps'), str(bare / 'backups'))
        self.assertEqual((result['status'], result['backup']), ('migrated', None))
        self.assertFalse((bare / 'backups').exists())
        self.assertEqual(schema.version(str(bare / 'data'), str(bare / 'maps')), schema.CURRENT)

    def test_restore_refuses_paths_outside_the_campaign_on_any_platform(self):
        result = self.migrate()
        manifest_file = Path(result['backup']) / 'backup.json'
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        for unsafe in (
            'data/..\\..\\escape.json',
            'data/C:\\escape.json',
            'data/../escape.json',
            'uploads/x.json',
        ):
            with self.subTest(path=unsafe):
                manifest['files'][0]['path'] = unsafe
                manifest_file.write_text(json.dumps(manifest), encoding='utf-8')
                before = self.snapshot()
                with self.assertRaisesRegex(schema.SchemaError, 'Unsafe backup path'):
                    schema.restore(result['backup'], self.data, self.maps)
                self.assertEqual(self.snapshot(), before)

    def test_interrupted_migration_runs_again_from_a_fresh_backup(self):
        writes = []
        real = schema.storage.atomic_json

        def stop_after_two(path, value, durable=False):
            if len(writes) == 2:
                raise OSError('Synthetic interruption')
            writes.append(path)
            real(path, value, durable)

        with patch.object(schema.storage, 'atomic_json', stop_after_two):
            with self.assertRaises(OSError):
                self.migrate()
        self.assertEqual(schema.version(self.data, self.maps), 0)
        first = next(p for p in Path(self.backups).iterdir() if p.is_dir())
        self.assertEqual(
            (first / 'data/threads.json').read_bytes(), self.original['data/threads.json']
        )

        result = self.migrate()

        self.assertEqual(result['status'], 'migrated')
        self.assertEqual(schema.version(self.data, self.maps), schema.CURRENT)
        self.assertEqual(self.read('data/codex/old-place.json')['tags'], [])
        self.assertEqual(self.read('maps/harbour/key.json')['areas'][0]['rooms'], [])

    def test_a_retried_migration_backup_names_the_first_attempt_as_partly_migrated(self):
        real = schema.storage.atomic_json

        def interrupt_after_documents_change(path, value, durable=False):
            if str(path).endswith('.json') and 'backups' not in Path(path).parts:
                real(path, value, durable)
                raise OSError('Synthetic interruption')
            real(path, value, durable)

        def backup_manifests():
            folders = sorted(Path(self.backups).iterdir(), key=lambda p: p.stat().st_mtime_ns)
            return [
                json.loads((f / 'backup.json').read_text(encoding='utf-8'))
                for f in folders
                if f.is_dir()
            ], [f.name for f in folders if f.is_dir()]

        for _ in range(2):
            with patch.object(schema.storage, 'atomic_json', interrupt_after_documents_change):
                with self.assertRaises(OSError):
                    self.migrate()
        manifests, names = backup_manifests()
        self.assertNotIn('partly_migrated', manifests[0])
        self.assertTrue(manifests[1]['partly_migrated'])
        self.assertEqual(manifests[1]['first_attempt_backup'], names[0])

        self.migrate()
        manifests, names = backup_manifests()
        self.assertEqual(manifests[2]['first_attempt_backup'], names[0])  # not the second attempt
        self.assertFalse((Path(self.backups) / schema.PENDING).exists())

    def test_completed_migration_clears_a_pending_record_left_by_interrupted_cleanup(self):
        real_remove = schema.os.remove

        def interrupt_cleanup(path):
            if path == schema.pending_path(self.backups):
                raise OSError('Synthetic interruption after recording the schema version')
            return real_remove(path)

        with patch.object(schema.os, 'remove', interrupt_cleanup):
            with self.assertRaises(OSError):
                self.migrate()
        self.assertEqual(schema.version(self.data, self.maps), schema.CURRENT)
        pending = Path(schema.pending_path(self.backups))
        self.assertTrue(pending.exists())
        self.assertIsNone(schema.interrupted_attempt(self.backups, schema.CURRENT))

        self.assertEqual(self.migrate()['status'], 'current')
        self.assertFalse(pending.exists())

    def test_a_document_that_cannot_be_backed_up_blocks_migration_cleanly(self):
        linked = self.dm / 'data/prep/linked.json'
        try:
            linked.symlink_to(self.dm / 'data/prep/s2.json')
        except OSError:
            self.skipTest('This platform does not allow symbolic links here.')
        with self.assertRaisesRegex(schema.SchemaError, 'Could not back up data/prep/linked.json'):
            self.migrate()
        self.assertEqual(schema.version(self.data, self.maps), 0)
        self.assertFalse(any(Path(self.backups).glob('*')))

    def test_malformed_document_blocks_migration_and_names_it(self):
        (self.dm / 'data/codex.json').write_text('[]', encoding='utf-8')
        with self.assertRaisesRegex(schema.SchemaError, 'codex.json'):
            self.migrate()
        self.assertEqual(schema.version(self.data, self.maps), 0)
        self.assertFalse(Path(self.backups).exists())

    def test_command_line_reports_then_migrates(self):
        with (
            campaign.using(campaign.Campaign(self.dm)),
            patch.object(migrate, 'server_running', return_value=False),
        ):
            report = io.StringIO()
            with contextlib.redirect_stdout(report):
                self.assertEqual(migrate.main([]), 0)
            self.assertIn('9 documents will be updated', report.getvalue())
            self.assertEqual(self.snapshot(), self.original)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(migrate.main(['--apply']), 0)
            self.assertEqual(schema.version(self.data, self.maps), schema.CURRENT)
        with patch.object(migrate, 'server_running', return_value=True):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(migrate.main(['--apply']), 1)

    def test_command_line_leaves_an_interrupted_change_to_the_server(self):
        # Completing it would queue a render in a process with no job workers, and lose it.
        pending = Path(self.data) / '.commits' / '20261004-120000-000000-abcdef.pending.json'
        pending.parent.mkdir()
        entry = {
            'id': '20261004-120000-000000-abcdef',
            'label': 'Apply layout',
            'created': '2026-10-04T12:00:00',
            'targets': [{'name': 'codex', 'text': False, 'before': None, 'value': {'entries': []}}],
            'after': [{'type': 'forge', 'slug': 'harbour', 'label': 'Render', 'populate': True}],
        }
        pending.write_text(json.dumps(entry), encoding='utf-8')
        before = self.snapshot()
        with (
            campaign.using(campaign.Campaign(self.dm)),
            patch.object(migrate, 'server_running', return_value=False),
            patch.object(campaign_core, 'queue_forge') as queue_forge,
        ):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(migrate.main(['--apply']), 1)
        self.assertIn('Start Campaign Studio', error.getvalue())
        queue_forge.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(schema.version(self.data, self.maps), 0)


if __name__ == '__main__':
    unittest.main()
