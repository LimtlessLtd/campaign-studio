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
        original = foundry_backup._copy_and_hash
        changed = False

        def copy_then_change(source, target):
            nonlocal changed
            record = original(source, target)
            if not changed:
                changed = True
                (self.live / 'Data' / 'assets' / 'late.png').write_bytes(b'late file')
            return record

        with patch.object(foundry_backup, '_copy_and_hash', side_effect=copy_then_change):
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
