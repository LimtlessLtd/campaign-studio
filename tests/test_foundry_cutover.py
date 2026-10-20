"""The upgrade wizard's cutover on synthetic User Data trees: review, switch and roll back.

The passing migration audit is stubbed here; `test_foundry_upgrade` and the workflow tests run the real
audit chain into the review. The backup, the clone and the live folder are real folders.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))

import foundry_backup
import foundry_cutover as cutover

WORLD = 'Data/worlds/fixture-world'
TEST_WORLD = 'Data/worlds/ai-test-world'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')


def world(core_version):
    return {
        'id': 'fixture-world',
        'title': 'Fixture',
        'system': 'dnd5e',
        'coreVersion': core_version,
    }


class CutoverTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='cutover-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.live = self.root / 'FoundryVTT'
        for name, value in {
            f'{WORLD}/world.json': world('12.343'),
            f'{WORLD}/data/actors/000001.ldb': b'v12 actors',
            f'{TEST_WORLD}/world.json': {'id': 'ai-test-world', 'coreVersion': '12.343'},
            f'{TEST_WORLD}/data/actors/000001.ldb': b'test actors',
            'Data/modules/alpha/module.json': {'id': 'alpha', 'version': '1.0.0'},
            'Data/modules/README.txt': 'Foundry',
            'Data/assets/map.webp': b'map',
            'Config/options.json': {'dataPath': str(self.live), 'port': 30000, 'language': 'en'},
            'Logs/debug.log': 'launch 1',
        }.items():
            write(self.live / name, value)
        self.world = self.live / WORLD
        for target, name, value in (
            (foundry_backup.config, 'settings', {'world_path': str(self.world)}),
            (foundry_backup, 'running_foundry', []),
        ):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        backup = foundry_backup.create(str(self.root / 'backups'), confirmed_closed=True)
        self.backup = Path(backup['path'])
        # The clone the GM migrated and opened from its own folder, on another port.
        self.clone = self.root / 'clone-for-v14'
        foundry_backup.rehearse(str(self.backup), str(self.clone))
        write(self.clone / WORLD / 'world.json', world('14.369'))
        write(self.clone / WORLD / 'data/actors/000001.ldb', b'v14 actors')
        write(
            self.clone / 'Config/options.json',
            {'dataPath': str(self.clone), 'port': 30001, 'language': 'en', 'v14': True},
        )
        (self.clone / 'Config/options.json.lock').mkdir()
        write(self.clone / 'Logs/foundry.log', 'migrated')
        audit_file = self.backup / 'migration-audit-fixture.json'
        write(audit_file, {})
        audit = {
            'status': 'reviewed',
            'clone_path': str(self.clone),
            'target_build': '14.369',
            'target_generation': 14,
            'v12_review_path': str(self.backup / 'clone-review-fixture.json'),
            'manual_checks': {},
        }
        verified = foundry_backup.verify(str(self.backup))
        for name, value in (
            ('passing_audit', (verified, audit_file, audit, {'world': {'id': 'fixture-world'}})),
            ('audit_migration', dict(audit)),
        ):
            patcher = patch.object(cutover.foundry_upgrade, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.audit_path = str(audit_file)

    def uninstall_test_world_and_rotate_logs(self):
        shutil.rmtree(self.live / TEST_WORLD)
        (self.live / 'Logs/debug.log').unlink()
        write(self.live / 'Logs/debug.1.log', 'launch 2')

    def ready(self):
        self.uninstall_test_world_and_rotate_logs()
        return cutover.review(self.audit_path, True, [TEST_WORLD])

    def tree(self, folder):
        return {
            path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob('*'))
            if path.is_file()
        }

    def test_logs_are_ignored_and_other_changes_are_listed_per_world_and_package(self):
        self.assertEqual(cutover.review(self.audit_path, True)['status'], 'ready_for_cutover')
        self.uninstall_test_world_and_rotate_logs()
        write(self.live / 'Data/modules/alpha/module.json', {'id': 'alpha', 'version': '1.0.1'})
        write(self.live / 'Data/modules/beta/module.json', {'id': 'beta', 'version': '1.0.0'})
        write(self.live / 'Data/assets/token.webp', b'token')
        found = cutover.review(self.audit_path, True)
        self.assertEqual(found['status'], 'needs_confirmation')
        self.assertFalse(found['cutover_ready'])
        self.assertNotIn('review_path', found)
        self.assertEqual(
            [(item['area'], item['kind'], item['id'], item['change']) for item in found['changes']],
            [
                ('Data/assets', 'folder', None, 'changed'),
                ('Data/modules/alpha', 'module', 'alpha', 'changed'),
                ('Data/modules/beta', 'module', 'beta', 'added'),
                (TEST_WORLD, 'world', 'ai-test-world', 'removed'),
            ],
        )
        removed = found['changes'][-1]  # two files and three folders
        self.assertEqual((removed['added'], removed['removed'], removed['changed']), (0, 5, 0))
        self.assertIn(f'{TEST_WORLD}/world.json', removed['examples'])
        self.assertEqual(found['unconfirmed_changes'], [item['area'] for item in found['changes']])
        partly = cutover.review(self.audit_path, True, [TEST_WORLD, 'Data/assets'])
        self.assertEqual(partly['unconfirmed_changes'], ['Data/modules/alpha', 'Data/modules/beta'])
        everything = [item['area'] for item in found['changes']]
        ready = cutover.review(self.audit_path, True, everything)
        self.assertEqual(ready['status'], 'ready_for_cutover')
        self.assertEqual(ready['removed_worlds'], ['ai-test-world'])
        self.assertEqual(json.loads(Path(ready['review_path']).read_text('utf-8')), ready)
        with self.assertRaisesRegex(ValueError, 'list of User Data areas'):
            cutover.review(self.audit_path, True, TEST_WORLD)

    def test_any_change_to_the_upgraded_world_blocks_and_cannot_be_confirmed(self):
        write(self.world / 'data/actors/000002.ldb', b'played after the backup')
        blocked = cutover.review(self.audit_path, True, [WORLD])
        self.assertEqual(blocked['status'], 'blocked')
        self.assertEqual(blocked['confirmed_changes'], [])
        self.assertIn('Take a new backup and repeat the upgrade', blocked['blockers'][0])
        self.assertNotIn('review_path', blocked)
        for confirmed, running in ((False, []), (True, [{'pid': 1}])):
            with (
                self.subTest(confirmed=confirmed),
                patch.object(foundry_backup, 'running_foundry', return_value=running),
                self.assertRaisesRegex(ValueError, 'Close both'),
            ):
                cutover.review(self.audit_path, confirmed)

    def test_cut_over_copies_the_clone_into_place_and_keeps_the_original(self):
        review = self.ready()
        original, clone = self.tree(self.live), self.tree(self.clone)
        with self.assertRaisesRegex(ValueError, 'Close both'):
            cutover.cut_over(review['review_path'])
        done = cutover.cut_over(review['review_path'], True)
        self.assertEqual(done['status'], 'cut_over')
        rollback = Path(done['rollback_user_data'])
        self.assertRegex(rollback.name, r'^FoundryVTT-v12-before-v14-\d{8}-\d{6}$')
        self.assertEqual(rollback.parent, self.live.parent)
        self.assertEqual(self.tree(rollback), original)
        self.assertEqual(self.tree(self.clone), clone)
        self.assertTrue((self.clone / 'Config/options.json.lock').is_dir())
        self.assertEqual(
            json.loads((self.live / WORLD / 'world.json').read_text())['coreVersion'], '14.369'
        )
        self.assertFalse((self.live / TEST_WORLD).exists())
        self.assertTrue((self.clone / TEST_WORLD).is_dir())
        self.assertFalse((self.live / 'Config/options.json.lock').exists())
        self.assertEqual(
            json.loads((self.live / 'Config/options.json').read_text('utf-8')),
            {'dataPath': str(self.live), 'port': 30000, 'language': 'en', 'v14': True},
        )
        expected = {
            name: value
            for name, value in clone.items()
            if not name.startswith(TEST_WORLD + '/') and name != 'Config/options.json'
        }
        copied = self.tree(self.live)
        del copied['Config/options.json']
        self.assertEqual(copied, expected)
        self.assertEqual(
            (done['left_out_worlds'], done['stale_locks_left_out']),
            (['ai-test-world'], ['Config/options.json.lock']),
        )
        self.assertEqual(done['options']['before'], {'dataPath': str(self.clone), 'port': 30001})
        receipt = Path(done['receipt_path'])
        self.assertEqual(receipt.parent, self.backup)
        self.assertEqual(json.loads(receipt.read_text('utf-8')), done)
        self.assertEqual([path.name for path in self.root.glob('.incomplete-*')], [])
        # Studio's selected world is now the migrated one at the same path.
        self.assertEqual(
            foundry_backup.config.world_info(str(self.world))['foundry_version'], '14.369'
        )

        back = cutover.roll_back(done['receipt_path'], True)
        self.assertEqual(back['status'], 'rolled_back')
        self.assertEqual(self.tree(self.live), original)
        self.assertFalse(rollback.exists())
        aside = Path(back['set_aside_user_data'])
        self.assertRegex(aside.name, r'^FoundryVTT-v14-rolled-back-\d{8}-\d{6}$')
        self.assertEqual(
            json.loads((aside / WORLD / 'world.json').read_text())['coreVersion'], '14.369'
        )
        with self.assertRaisesRegex(ValueError, 'Nothing to roll back'):
            cutover.roll_back(done['receipt_path'], True)

    def test_a_change_since_the_review_or_a_failed_copy_switches_nothing(self):
        review = self.ready()
        original = self.tree(self.live)
        write(self.live / 'Data/assets/late.webp', b'late')
        with self.assertRaisesRegex(ValueError, 'changed since the cutover review'):
            cutover.cut_over(review['review_path'], True)
        (self.live / 'Data/assets/late.webp').unlink()
        with patch.object(foundry_backup, '_space', side_effect=ValueError('No space.')):
            with self.assertRaisesRegex(ValueError, 'No space. Nothing was switched.'):
                cutover.cut_over(review['review_path'], True)
        self.assertEqual(self.tree(self.live), original)
        self.assertEqual(
            sorted(path.name for path in self.root.iterdir()),
            ['FoundryVTT', 'backups', 'clone-for-v14'],
        )
        self.assertEqual(list(self.backup.glob('live-cutover-*.json')), [])

    def test_a_failed_rename_puts_the_original_back_and_an_interrupted_one_rolls_back(self):
        review = self.ready()
        original = self.tree(self.live)
        rename = Path.rename
        refused = []

        def refuse(source, target):
            if Path(target) == self.live and len(refused) < limit:
                refused.append(Path(source).name)
                raise PermissionError('in use')
            return rename(source, target)

        limit = 1  # The copy cannot take the live name; the original is renamed back.
        with patch.object(Path, 'rename', refuse):
            with self.assertRaisesRegex(ValueError, 'The original is back in place'):
                cutover.cut_over(review['review_path'], True)
        self.assertEqual(self.tree(self.live), original)
        [failed] = self.backup.glob('live-cutover-*.json')
        self.assertEqual(json.loads(failed.read_text('utf-8'))['status'], 'failed')
        self.assertEqual(
            sorted(path.name for path in self.root.iterdir()),
            ['FoundryVTT', 'backups', 'clone-for-v14'],
        )
        failed.unlink()

        refused.clear()
        limit = 2  # Neither folder can take the live name: the receipt says how to roll back.
        with patch.object(Path, 'rename', refuse):
            with self.assertRaisesRegex(ValueError, 'Roll back with the receipt'):
                cutover.cut_over(review['review_path'], True)
        self.assertFalse(self.live.exists())
        [interrupted] = self.backup.glob('live-cutover-*.json')
        self.assertEqual(json.loads(interrupted.read_text('utf-8'))['status'], 'switching')
        back = cutover.roll_back(str(interrupted), True)
        self.assertEqual((back['status'], back['set_aside_user_data']), ('rolled_back', None))
        self.assertEqual(self.tree(self.live), original)

    def test_roll_back_refuses_a_folder_that_is_not_the_original_world(self):
        done = cutover.cut_over(self.ready()['review_path'], True)
        rollback = Path(done['rollback_user_data'])
        write(rollback / WORLD / 'world.json', world('14.369'))
        with self.assertRaisesRegex(ValueError, 'no longer holds the original world'):
            cutover.roll_back(done['receipt_path'], True)
        self.assertTrue(rollback.is_dir())
        self.assertEqual(
            json.loads((self.live / WORLD / 'world.json').read_text())['coreVersion'], '14.369'
        )
        with self.assertRaisesRegex(ValueError, 'Select a saved cutover receipt'):
            cutover.roll_back(done['review_path'], True)


if __name__ == '__main__':
    unittest.main()
