"""The upgrade wizard's repair step on a synthetic migrated clone: dry run, repair, restore and smoke test.

The passing audit is stubbed here; `test_foundry_upgrade` covers `passing_audit` and `clone_world` on a
real audit chain. Writes go through the classic-level development dependency, as they go through the
library a Foundry installation bundles.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
sys.path.insert(0, str(ROOT / 'tests'))

import foundry_backup
import foundry_readiness as readiness
import foundry_repair
import foundry_world_fixture as fixture
from foundry_world_fixture import LIGHTHOUSE, PACK_SPELL, PARTY


@unittest.skipUnless(
    fixture.node(), 'Node.js or the classic-level development dependency is missing'
)
class ReadinessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='readiness-test-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.backup = root / 'backup'
        self.backup.mkdir()
        (self.backup / foundry_backup.MANIFEST).write_text(
            json.dumps({'format': foundry_backup.FORMAT}), encoding='utf-8'
        )
        audit_file = self.backup / 'migration-audit-fixture.json'
        audit_file.write_text('{}', encoding='utf-8')
        self.clone = root / 'clone'
        self.world = fixture.write(
            self.clone / 'Data' / 'worlds' / 'fixture-world', fixture.documents(), classic=True
        )
        fixture.pack(self.clone, [PACK_SPELL])
        backup = {'path': str(self.backup), 'world': {'id': 'fixture-world'}}
        audit = {'target_build': '14.369', 'clone_path': str(self.clone)}
        install = {'root': root, 'library': fixture.CLASSIC_LEVEL, 'build': '14.369'}
        for target, name, value in (
            (readiness.foundry_backup, 'running_foundry', []),
            (readiness.foundry_upgrade, 'passing_audit', (backup, audit_file, audit, {})),
            (readiness.foundry_upgrade, 'clone_world', (self.clone, self.world)),
            (readiness.foundry_leveldb_write, 'installation', install),
        ):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.audit_path = str(audit_file)

    def scan(self):
        return readiness.scan(self.audit_path, confirmed_closed=True)

    def test_dry_run_reports_and_saves_without_changing_the_clone(self):
        before = foundry_repair.fingerprint(foundry_repair.load(self.world))
        with self.assertRaisesRegex(ValueError, 'Close both Foundry'):
            readiness.scan(self.audit_path)
        with patch.object(readiness.foundry_backup, 'running_foundry', return_value=[{'pid': 1}]):
            with self.assertRaisesRegex(ValueError, 'Close both Foundry'):
                self.scan()
        found = self.scan()
        self.assertEqual(
            found['summary'],
            {
                'scenes': 4,
                'floors_before': 12,
                'floors_after': 8,
                'source_ids': 2,
                'formulas': 5,
                'other_fields': 1,
                'summons': 1,
                'macros': 2,
            },
        )
        self.assertEqual(Path(found['scan_path']).parent, self.backup)
        self.assertEqual(json.loads(Path(found['scan_path']).read_text('utf-8')), found)
        self.assertEqual(
            [item['name'] for item in found['smoke_choices']['folders']], ['Heroes', 'Foes']
        )
        self.assertEqual(foundry_repair.fingerprint(foundry_repair.load(self.world)), before)

    def test_repair_backs_up_writes_the_reviewed_choices_and_verifies(self):
        found = self.scan()
        summon = found['findings']['summons'][0]['id']
        choices = {
            'summons': {summon: 'Actor.actorWolf0000001'},
            'macros': ['!macros!macroEffect00001'],
        }
        with self.assertRaisesRegex(ValueError, 'Review the dry run'):
            readiness.apply(found['scan_path'], '/foundry', choices, confirmed_closed=True)
        with self.assertRaisesRegex(ValueError, 'Close both Foundry'):
            readiness.apply(found['scan_path'], '/foundry', choices, confirmed_reviewed=True)
        done = readiness.apply(found['scan_path'], '/foundry', choices, True, True)
        self.assertEqual(done['status'], 'repaired', done)
        self.assertTrue(done['verification']['ok'])
        self.assertEqual(
            [check['name'] for check in done['verification']['checks']][-1],
            'Other world files unchanged',
        )
        self.assertEqual(done['written'], {'actors': 3, 'items': 4, 'macros': 1, 'scenes': 18})
        self.assertEqual(done['runtime']['classic_level'], '3.0.0')
        snapshot = foundry_backup.verify_snapshot(done['snapshot_path'])
        self.assertEqual(Path(snapshot['source']), self.world)
        self.assertEqual(Path(done['repair_path']).parent, self.backup)
        documents = foundry_repair.load(self.world)
        profile = documents['actors']['!actors.items!actorSummoner001.itemSummon000001'][1]
        self.assertEqual(
            profile['system']['activities']['summonShade00001']['profiles'][0]['uuid'],
            'Actor.actorWolf0000001',
        )
        again = self.scan()
        self.assertEqual(sum(foundry_repair.pending(again['findings']).values()), 0)
        self.assertEqual(again['summary']['summons'], 0)
        self.assertEqual([item['name'] for item in again['findings']['macros']], ['Bless Ally'])
        nothing = readiness.apply(again['scan_path'], '/foundry', None, True, True)
        self.assertEqual((nothing['status'], nothing['written']), ('nothing_to_repair', {}))
        self.assertNotIn('snapshot_path', nothing)

    def test_a_clone_changed_after_the_dry_run_must_be_checked_again(self):
        found = self.scan()
        fixture.write(
            self.world,
            {'items': {'!items!worldNew00000001': {'_id': 'worldNew00000001', 'name': 'New'}}},
            classic=True,
        )
        with self.assertRaisesRegex(ValueError, 'changed after the dry run'):
            readiness.apply(found['scan_path'], '/foundry', None, True, True)
        found['audit_sha256'] = '0' * 64
        Path(found['scan_path']).write_text(json.dumps(found), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            readiness.apply(found['scan_path'], '/foundry', None, True, True)

    def test_a_failed_verification_restores_the_world_backup(self):
        found = self.scan()
        before = foundry_repair.fingerprint(foundry_repair.load(self.world))
        failed = {'ok': False, 'checks': [{'name': 'Synthetic', 'ok': False, 'detail': ['no']}]}
        with patch.object(readiness.foundry_repair, 'verify', return_value=failed):
            result = readiness.apply(found['scan_path'], '/foundry', None, True, True)
        self.assertEqual(result['status'], 'restored')
        self.assertTrue(result['restore_verified'])
        self.assertCountEqual(result['restored'], ['data/actors', 'data/items', 'data/scenes'])
        self.assertEqual(foundry_repair.fingerprint(foundry_repair.load(self.world)), before)
        replaced = Path(result['snapshot_path']).parent / 'replaced'
        self.assertEqual(len(list(replaced.iterdir())), 3)

    def test_a_write_that_never_started_leaves_the_world_as_it_was(self):
        found = self.scan()
        before = foundry_repair.fingerprint(foundry_repair.load(self.world))
        refused = {'ok': False, 'committed': [], 'error': 'Database is not open', 'runtime': {}}
        with patch.object(readiness.foundry_leveldb_write, 'write', return_value=refused):
            result = readiness.apply(found['scan_path'], '/foundry', None, True, True)
        self.assertEqual((result['status'], result['error']), ('failed', 'Database is not open'))
        self.assertNotIn('restored', result)
        self.assertEqual(foundry_repair.fingerprint(foundry_repair.load(self.world)), before)

    def test_smoke_test_reads_the_clone_named_by_the_dry_run(self):
        found = self.scan()
        report = readiness.smoke_test(found['scan_path'], PARTY, [LIGHTHOUSE])
        self.assertEqual(report['format'], readiness.SMOKE_FORMAT)
        self.assertEqual(Path(report['report_path']).parent, self.backup)
        self.assertEqual(report['encounters'][0]['scene'], 'Lighthouse')
        self.assertIn('not a balance prediction', report['note'])
        with self.assertRaisesRegex(ValueError, 'evidence'):
            readiness.smoke_test(self.audit_path, PARTY, [LIGHTHOUSE])


if __name__ == '__main__':
    unittest.main()
