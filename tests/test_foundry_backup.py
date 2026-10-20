"""Synthetic checks for full Foundry backups and isolated restore rehearsals."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import foundry_backup


class FoundryBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='foundry-backup-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.live = self.root / 'live-user-data'
        self.world = self.live / 'Data' / 'worlds' / 'fixture-world'
        self.world.mkdir(parents=True)
        (self.world / 'world.json').write_text(
            json.dumps(
                {
                    'id': 'fixture-world',
                    'title': 'Fixture world',
                    'coreVersion': '12.343',
                    'system': 'dnd5e',
                    'systemVersion': '4.4.4',
                }
            ),
            encoding='utf-8',
        )
        (self.live / 'Data' / 'assets').mkdir()
        (self.live / 'Data' / 'assets' / 'map.png').write_bytes(b'synthetic map bytes')
        (self.live / 'Config').mkdir()
        (self.live / 'Config' / 'options.json').write_text('{}', encoding='utf-8')
        (self.live / 'Logs').mkdir()
        (self.live / 'Backups').mkdir()
        (self.live / 'Backups' / 'snapshot.bak').write_bytes(b'synthetic snapshot')
        self.settings_patch = patch.object(
            foundry_backup.config, 'settings', return_value={'world_path': str(self.world)}
        )
        self.process_patch = patch.object(foundry_backup, 'running_foundry', return_value=[])
        self.settings_patch.start()
        self.process_patch.start()
        self.addCleanup(self.process_patch.stop)
        self.addCleanup(self.settings_patch.stop)

    def test_backup_verifies_all_user_data_and_rehearses_without_touching_live_world(self):
        destination = self.root / 'copies'
        plan = foundry_backup.plan()
        self.assertEqual(plan['world']['foundry_version'], '12.343')
        self.assertEqual(plan['files'], 4)

        backup = foundry_backup.create(str(destination), confirmed_closed=True)
        package = Path(backup['path'])
        self.assertTrue(backup['verified'])
        self.assertEqual(foundry_backup.verify(str(package))['files'], 4)
        self.assertEqual(
            (package / 'User Data' / 'Data' / 'assets' / 'map.png').read_bytes(),
            b'synthetic map bytes',
        )
        self.assertTrue((package / 'User Data' / 'Logs').is_dir())

        # A restore test must still work when the original world is unavailable.
        (self.world / 'world.json').unlink()
        rehearsal = foundry_backup.rehearse(str(package), str(self.root / 'restore-test'))
        self.assertTrue(rehearsal['verified'])
        self.assertEqual(
            json.loads((Path(rehearsal['world_path']) / 'world.json').read_text())['coreVersion'],
            '12.343',
        )
        self.assertEqual(
            (Path(rehearsal['path']) / 'Data' / 'assets' / 'map.png').read_bytes(),
            b'synthetic map bytes',
        )

    def test_changes_since_the_backup_are_grouped_by_area_without_logs(self):
        for name, directory, expected in (
            ('Data/worlds/w/data/actors/000001.ldb', False, 'Data/worlds/w'),
            ('Data/worlds/w', True, 'Data/worlds/w'),
            ('Data/modules/README.txt', False, 'Data/modules'),
            ('Data/modules', True, 'Data/modules'),
            ('Data/assets/map.png', False, 'Data/assets'),
            ('Data/assets', True, 'Data/assets'),
            ('Data/loose.txt', False, 'Data'),
            ('Config/options.json', False, 'Config'),
            ('Config', True, 'Config'),
        ):
            with self.subTest(name=name):
                self.assertEqual(foundry_backup.area(name, directory), expected)
        backup = foundry_backup.create(str(self.root / 'copies'), confirmed_closed=True)
        self.assertEqual(foundry_backup.compare_source(backup['path'])['changes'], [])
        (self.live / 'Logs' / 'debug.log').write_text('rotated', encoding='utf-8')
        (self.live / 'Data' / 'assets' / 'map.png').write_bytes(b'synthetic map bytes, edited')
        added = self.live / 'Data' / 'worlds' / 'new-world'
        added.mkdir()
        (added / 'world.json').write_text('{}', encoding='utf-8')
        changes = foundry_backup.compare_source(backup['path'])['changes']
        self.assertEqual(
            [(item['area'], item['kind'], item['id'], item['change']) for item in changes],
            [
                ('Data/assets', 'folder', None, 'changed'),
                ('Data/worlds/new-world', 'world', 'new-world', 'added'),
            ],
        )
        self.assertEqual(
            (changes[1]['added'], changes[1]['examples']),
            (2, ['Data/worlds/new-world/', 'Data/worlds/new-world/world.json']),
        )

    def test_corrupt_backup_is_rejected_before_rehearsal(self):
        backup = foundry_backup.create(str(self.root / 'copies'), confirmed_closed=True)
        package = Path(backup['path'])
        (package / 'User Data' / 'Data' / 'assets' / 'map.png').write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'checksum failed'):
            foundry_backup.verify(str(package))
        target = self.root / 'restore-test'
        with self.assertRaisesRegex(ValueError, 'checksum failed'):
            foundry_backup.rehearse(str(package), str(target))
        self.assertFalse(target.exists())

    def test_new_source_file_during_copy_keeps_backup_incomplete(self):
        original = foundry_backup.storage.copy_and_hash
        changed = False

        def copy_then_change(source, target):
            nonlocal changed
            record = original(source, target)
            if not changed:
                changed = True
                (self.live / 'Data' / 'assets' / 'late.png').write_bytes(b'late file')
            return record

        with patch.object(foundry_backup.storage, 'copy_and_hash', side_effect=copy_then_change):
            with self.assertRaisesRegex(ValueError, 'changed during the backup'):
                foundry_backup.create(str(self.root / 'copies'), confirmed_closed=True)
        self.assertEqual(list((self.root / 'copies').glob('foundry-*')), [])
        self.assertEqual(len(list((self.root / 'copies').glob('.incomplete-*'))), 1)

    def test_backup_refuses_live_data_destination_and_running_foundry(self):
        with self.assertRaisesRegex(ValueError, 'outside Foundry User Data'):
            foundry_backup.create(str(self.live / 'Data' / 'copies'), confirmed_closed=True)
        with self.assertRaisesRegex(ValueError, 'Confirm'):
            foundry_backup.create(str(self.root / 'copies'))
        with patch.object(foundry_backup, 'running_foundry', return_value=[{'pid': 1}]):
            with self.assertRaisesRegex(ValueError, 'Close Foundry'):
                foundry_backup.create(str(self.root / 'copies'), confirmed_closed=True)
        self.assertFalse((self.root / 'copies').exists())

    def test_restore_refuses_existing_or_live_destination_and_manifest_traversal(self):
        backup = foundry_backup.create(str(self.root / 'copies'), confirmed_closed=True)
        package = Path(backup['path'])
        with self.assertRaisesRegex(ValueError, 'new, empty'):
            foundry_backup.rehearse(str(package), str(self.live))
        with self.assertRaisesRegex(ValueError, 'separate'):
            foundry_backup.rehearse(str(package), str(self.live / 'new-copy'))
        manifest_path = package / foundry_backup.MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['files'][0]['path'] = '../outside'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Invalid backup manifest path'):
            foundry_backup.verify(str(package))

    def test_world_snapshot_verifies_and_restores_only_the_named_folders(self):
        actors = self.world / 'data' / 'actors'
        scenes = self.world / 'data' / 'scenes'
        for folder in (actors, scenes):
            folder.mkdir(parents=True)
            (folder / 'CURRENT').write_text('MANIFEST-000001\n', encoding='utf-8')
            (folder / '000001.log').write_bytes(b'original ' + folder.name.encode())
        (self.world / 'data' / 'fog').mkdir()  # an empty folder is kept too
        with self.assertRaisesRegex(ValueError, 'outside the folder'):
            foundry_backup.snapshot(str(self.world), str(self.world / 'data' / 'copy'))
        with patch.object(foundry_backup, 'running_foundry', return_value=[{'pid': 1}]):
            with self.assertRaisesRegex(ValueError, 'Close Foundry'):
                foundry_backup.snapshot(str(self.world), str(self.root / 'snapshot'))
        copy = foundry_backup.snapshot(str(self.world), str(self.root / 'snapshot'))
        self.assertEqual((copy['files'], copy['verified']), (5, True))
        with self.assertRaisesRegex(ValueError, 'new, empty'):
            foundry_backup.snapshot(str(self.world), copy['path'])
        manifest = foundry_backup.verify_snapshot(copy['path'])
        self.assertIn('data/fog', manifest['directories'])

        (actors / '000001.log').write_bytes(b'repaired actors')
        (actors / '000002.log').write_bytes(b'new log')
        (scenes / '000001.log').write_bytes(b'repaired scenes')
        kept = self.root / 'replaced'
        restored = foundry_backup.restore_snapshot(copy['path'], ['data/actors'], str(kept))
        self.assertEqual(restored, ['data/actors'])
        self.assertEqual(sorted(path.name for path in actors.iterdir()), ['000001.log', 'CURRENT'])
        self.assertEqual((actors / '000001.log').read_bytes(), b'original actors')
        self.assertEqual((scenes / '000001.log').read_bytes(), b'repaired scenes')
        [aside] = kept.iterdir()
        self.assertEqual((aside / '000002.log').read_bytes(), b'new log')
        with self.assertRaisesRegex(ValueError, 'no folder'):
            foundry_backup.restore_snapshot(copy['path'], ['data/journal'], str(kept))
        with self.assertRaisesRegex(ValueError, 'outside the restored folder'):
            foundry_backup.restore_snapshot(copy['path'], ['data/actors'], str(actors))

        (Path(copy['path']) / 'folder' / 'world.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'checksum failed'):
            foundry_backup.restore_snapshot(copy['path'], ['data/scenes'], str(kept))
        self.assertEqual((scenes / '000001.log').read_bytes(), b'repaired scenes')
