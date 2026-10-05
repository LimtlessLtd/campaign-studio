"""Synthetic inventory, directory and dependency checks for the upgrade report."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'DM'))

import foundry_backup
import foundry_catalog
import foundry_compat
import foundry_solver
import foundry_upgrade as upgrade


def release(package_id, version, minimum, maximum, verified, requires=(), systems=()):
    compatibility = {'minimum': minimum, 'maximum': maximum, 'verified': verified}
    return {
        'id': package_id,
        'version': version,
        'manifest': f'https://example.org/{package_id}/{version}/module.json',
        'compatibility': compatibility,
        'manifest_compatibility': compatibility,
        'requires': list(requires),
        'systems': list(systems),
    }


def dependency(package_id, minimum=''):
    return {'id': package_id, 'type': 'module', 'compatibility': {'minimum': minimum}}


def inventory():
    return {
        'format': upgrade.INVENTORY_FORMAT,
        'schema': 2,
        'exportedAt': '2026-10-03T12:00:00Z',
        'world': {
            'id': 'fixture-world',
            'title': 'Synthetic World',
            'system': 'dnd5e',
            'coreVersion': '12.331',
        },
        'system': {
            'id': 'dnd5e',
            'version': '3.0.0',
            'manifest': 'https://example.org/system.json',
        },
        'enabledModuleIds': ['alpha', 'gamma', 'Plutonium', 'unlisted'],
        'modules': [
            {'id': 'alpha', 'version': '1.0.0', 'enabled': True, 'relationships': {}},
            {'id': 'gamma', 'version': '1.0.0', 'enabled': True, 'relationships': {}},
            {'id': 'beta', 'version': '1.0.0', 'enabled': False, 'relationships': {}},
            {'id': 'Plutonium', 'version': '1.0.0', 'enabled': True, 'relationships': {}},
            {'id': 'unlisted', 'version': '1.0.0', 'enabled': True, 'relationships': {}},
        ],
    }


def catalog():
    return {
        'source': foundry_compat.RELEASES_URL,
        'builds': ['14.368', '13.351', '12.343', '12.331'],
        'packages': {
            'dnd5e': {
                'status': 'listed',
                'releases': [
                    release('dnd5e', '5.0.0', '14', '14', '14'),
                    release('dnd5e', '4.0.0', '13', '13', '13'),
                    release('dnd5e', '3.0.0', '12', '12', '12'),
                ],
            },
            'alpha': {
                'status': 'listed',
                'releases': [
                    release('alpha', '3.0.0', '14', '14', '14', [dependency('beta', '3')]),
                    release('alpha', '2.0.0', '13', '13', '13', [dependency('beta', '2')]),
                    release('alpha', '1.0.0', '12', '12', '12'),
                ],
            },
            'beta': {
                'status': 'listed',
                'releases': [
                    release('beta', '2.0.0', '13', '13', '13'),
                    release('beta', '1.0.0', '12', '12', '12'),
                ],
            },
            'gamma': {
                'status': 'listed',
                'releases': [
                    release('gamma', '2.0.0', '13', '13', '12'),
                    release('gamma', '1.0.0', '12', '12', '12'),
                ],
            },
            'Plutonium': {'status': 'listed', 'releases': []},
            'unlisted': {'status': 'unlisted', 'releases': []},
        },
    }


class UpgradeTests(unittest.TestCase):
    def test_choose_newest_full_match_and_keep_disabled_modules_off(self):
        result = foundry_solver.analyze(inventory(), catalog())
        self.assertEqual(result['recommended_build'], '12.343')
        self.assertEqual(result['system']['selected_version'], '3.0.0')
        self.assertTrue(result['candidates'][0]['blockers'])
        self.assertIn('gamma: no eligible release', ' '.join(result['candidates'][0]['blockers']))
        self.assertIn('GM approval', ' '.join(result['candidates'][1]['blockers']))
        decisions = {item['id']: item for item in result['modules']}
        self.assertFalse(decisions['Plutonium']['proposed_enabled'])
        self.assertFalse(decisions['unlisted']['proposed_enabled'])
        self.assertFalse(decisions['beta']['proposed_enabled'])
        self.assertEqual(decisions['alpha']['selected_version'], '1.0.0')

    def test_runtime_active_module_is_retained_when_saved_configuration_disagrees(self):
        data = inventory()
        data['enabledModuleIds'].remove('gamma')
        with tempfile.TemporaryDirectory(prefix='upgrade-mismatch-fixture-') as folder:
            completed = upgrade.complete_inventory(data, folder)
        self.assertEqual(completed['activation_discrepancies'], ['gamma'])
        gamma = next(item for item in completed['modules'] if item['id'] == 'gamma')
        self.assertTrue(gamma['enabled'])
        result = foundry_solver.analyze(completed, catalog())
        decision = next(item for item in result['modules'] if item['id'] == 'gamma')
        self.assertTrue(decision['original_enabled'])
        self.assertTrue(decision['proposed_enabled'])

    def test_selects_newest_package_version_when_directory_rows_are_unsorted(self):
        data = inventory()
        data['modules'] = []
        releases = catalog()
        releases['packages']['dnd5e']['releases'].append(
            release('dnd5e', '5.1.0', '14', '14', '14')
        )
        result = foundry_solver.analyze(data, releases)
        self.assertEqual(result['recommended_build'], '14.368')
        self.assertEqual(result['system']['selected_version'], '5.1.0')

    def test_approved_dependency_enables_newer_unverified_candidate(self):
        result = foundry_solver.analyze(inventory(), catalog(), approved_dependencies=['beta'])
        self.assertEqual(result['recommended_build'], '13.351')
        self.assertEqual(result['newest_all_verified_build'], '12.343')
        self.assertTrue(result['needs_clone_testing'])
        self.assertEqual(result['dependencies'][0]['id'], 'beta')
        self.assertEqual(result['dependencies'][0]['version'], '2.0.0')
        beta = next(item for item in result['modules'] if item['id'] == 'beta')
        self.assertTrue(beta['proposed_enabled'])
        self.assertIn('GM approved', beta['activation_reason'])

    def test_excluded_dependency_blocks_full_match_until_gm_disables_dependent(self):
        data = inventory()
        data['modules'] = [data['modules'][0], data['modules'][3]]
        releases = catalog()
        releases['packages']['alpha']['releases'] = [
            release('alpha', '1.0.0', '12', '14', '12', [dependency('Plutonium')])
        ]
        result = foundry_solver.analyze(data, releases)
        self.assertIsNone(result['recommended_build'])
        self.assertIsNone(result['modules'][0]['proposed_enabled'])
        self.assertIn('requires excluded Plutonium', ' '.join(result['candidates'][0]['blockers']))
        chosen = foundry_solver.analyze(data, releases, disabled_modules=['alpha'])
        self.assertEqual(chosen['recommended_build'], '14.368')
        self.assertIn('GM explicitly', chosen['modules'][0]['disabled_reason'])

    def test_system_relationship_and_package_lock_are_reported(self):
        data = inventory()
        data['modules'] = [data['modules'][0]]
        data['system']['locked'] = True
        releases = catalog()
        releases['packages']['alpha']['releases'][1]['systems'] = [
            {'id': 'dnd5e', 'compatibility': {'maximum': '3.0.0'}}
        ]
        result = foundry_solver.analyze(data, releases)
        self.assertEqual(result['recommended_build'], '12.343')
        releases['packages']['alpha']['releases'][1]['systems'] = []
        result = foundry_solver.analyze(data, releases, approved_dependencies=['beta'])
        self.assertEqual(result['recommended_build'], '13.351')
        self.assertEqual(result['locked_changes'], ['dnd5e'])

    def test_collector_reads_dependency_manifests_and_keeps_disabled_status(self):
        data = inventory()
        data['modules'] = [data['modules'][0], data['modules'][2]]
        releases_page = (
            '<li class="article release flexrow"><a href="/releases/13.351">Release</a>'
            '<span class="release-tag stable">Stable</span></li>'
            '<li class="article release flexrow"><a href="/releases/12.331">Release</a>'
            '<span class="release-tag stable">Stable</span></li>'
        )

        def package_page(package_id):
            return (
                f'<li class="package-version flexrow"><h4 class="package-title">Version 1.0.0</h4>'
                f'<span>Foundry Version 12 - 13 (Verified 13)</span>'
                f'<a href="https://example.org/{package_id}.json" '
                f'title="Manifest Installation URL">Manifest URL</a></li>'
            )

        def fake_fetch(url, *_args, **_kwargs):
            if url == foundry_compat.RELEASES_URL:
                return releases_page
            if url.startswith(foundry_catalog.PACKAGE_URL):
                return package_page(url.split('/')[-2])
            package_id = url.rsplit('/', 1)[-1].removesuffix('.json')
            relationships = {'requires': [dependency('beta')]} if package_id == 'alpha' else {}
            return json.dumps(
                {'id': package_id, 'version': '1.0.0', 'relationships': relationships}
            )

        with patch.object(foundry_catalog, 'fetch', side_effect=fake_fetch):
            result = foundry_catalog.collect_catalog(data)
        self.assertEqual(result['builds'], ['13.351', '12.331'])
        self.assertEqual(result['packages']['alpha']['releases'][0]['requires'][0]['id'], 'beta')
        self.assertEqual(result['packages']['beta']['status'], 'listed')

    def test_report_requires_matching_inventory_and_verified_backup(self):
        with tempfile.TemporaryDirectory(prefix='upgrade-fixture-') as folder:
            backup = Path(folder)
            world = {
                'id': 'fixture-world',
                'system': 'dnd5e',
                'foundry_version': '12.331',
            }
            verified = {
                'path': str(backup),
                'world': {'id': 'fixture-world', 'system': 'dnd5e', 'core_version': '12.331'},
            }
            with (
                patch.object(upgrade.config, 'settings', return_value={'world_path': str(backup)}),
                patch.object(upgrade.config, 'world_info', return_value=world),
                patch.object(foundry_backup, 'verify', return_value=verified) as verify,
            ):
                result = upgrade.report(inventory(), str(backup), catalog=catalog())
                verify.assert_called_once_with(str(backup))
                self.assertFalse(result['migration_ready'])
                self.assertTrue(Path(result['inventory_path']).is_file())
                self.assertEqual(
                    json.loads(Path(result['report_path']).read_text(encoding='utf-8'))[
                        'recommended_build'
                    ],
                    '12.343',
                )
                wrong = inventory()
                wrong['world']['id'] = 'another-world'
                with self.assertRaises(ValueError):
                    upgrade.report(wrong, str(backup), catalog=catalog())

    def test_prepare_clone_uses_v12_restore_receipt_and_keeps_live_world_unchanged(self):
        with tempfile.TemporaryDirectory(prefix='upgrade-clone-fixture-') as folder:
            root = Path(folder)
            user_data = root / 'live-user-data'
            world_path = user_data / 'Data' / 'worlds' / 'fixture-world'
            world_path.mkdir(parents=True)
            world_json = {
                'id': 'fixture-world',
                'title': 'Synthetic World',
                'system': 'dnd5e',
                'systemVersion': '3.0.0',
                'coreVersion': '12.331',
            }
            (world_path / 'world.json').write_text(json.dumps(world_json), encoding='utf-8')
            system_path = user_data / 'Data' / 'systems' / 'dnd5e'
            system_path.mkdir(parents=True)
            (system_path / 'system.json').write_text(
                json.dumps({'id': 'dnd5e', 'version': '3.0.0'}), encoding='utf-8'
            )
            for item in inventory()['modules']:
                module_path = user_data / 'Data' / 'modules' / item['id']
                module_path.mkdir(parents=True)
                (module_path / 'module.json').write_text(
                    json.dumps({'id': item['id'], 'version': item['version']}), encoding='utf-8'
                )
            extra = user_data / 'Data' / 'modules' / 'inactive-extra'
            extra.mkdir(parents=True)
            (extra / 'module.json').write_text(
                json.dumps({'id': 'inactive-extra', 'version': '0.1.0'}), encoding='utf-8'
            )
            with (
                patch.object(
                    upgrade.config, 'settings', return_value={'world_path': str(world_path)}
                ),
                patch.object(foundry_backup, 'running_foundry', return_value=[]),
            ):
                backup = foundry_backup.create(str(root / 'backups'), confirmed_closed=True)
                restored = foundry_backup.rehearse(backup['path'], str(root / 'restore-test'))
                configured_missing = inventory()
                configured_missing['enabledModuleIds'].append('inactive-extra')
                warning = upgrade.report(configured_missing, backup['path'], catalog=catalog())
                self.assertEqual(warning['activation_discrepancies'], ['inactive-extra'])
                self.assertTrue(warning['needs_clone_testing'])
                self.assertTrue(warning['requires_gm_choice'])
                data = upgrade.report(
                    inventory(), backup['path'], approved_dependencies=['beta'], catalog=catalog()
                )
                self.assertEqual(data['recommended_build'], '13.351')
                extra_decision = next(
                    item for item in data['modules'] if item['id'] == 'inactive-extra'
                )
                self.assertFalse(extra_decision['original_enabled'])
                with self.assertRaisesRegex(ValueError, 'Confirm'):
                    upgrade.prepare_clone(
                        data['report_path'], restored['receipt_path'], str(root / 'clone')
                    )
                with self.assertRaisesRegex(ValueError, 'separate'):
                    upgrade.prepare_clone(
                        data['report_path'],
                        restored['receipt_path'],
                        str(root / 'restore-test' / 'nested'),
                        True,
                        True,
                    )
                plan = upgrade.prepare_clone(
                    data['report_path'], restored['receipt_path'], str(root / 'clone'), True, True
                )
                self.assertEqual(plan['status'], 'awaiting_v12_module_review')
                self.assertFalse(plan['migration_ready'])
                self.assertEqual(plan['target_build'], '13.351')
                self.assertEqual(
                    {item['id'] for item in plan['disable_in_v12']}, {'Plutonium', 'unlisted'}
                )
                self.assertEqual(plan['enable_after_review'], ['beta'])
                self.assertTrue(Path(plan['plan_path']).is_file())
                self.assertEqual(
                    (
                        Path(plan['clone_path'])
                        / 'Data'
                        / 'worlds'
                        / 'fixture-world'
                        / 'world.json'
                    ).read_text(),
                    (world_path / 'world.json').read_text(),
                )
                self.assertEqual(json.loads((world_path / 'world.json').read_text()), world_json)
                clone_inventory = inventory()
                with self.assertRaisesRegex(ValueError, 'Confirm'):
                    upgrade.review_clone(plan['plan_path'], clone_inventory)
                blocked = upgrade.review_clone(plan['plan_path'], clone_inventory, True)
                self.assertEqual(blocked['status'], 'blocked')
                self.assertTrue(any('Plutonium' in issue for issue in blocked['blockers']))
                self.assertTrue(Path(blocked['review_path']).is_file())
                clone_inventory['enabledModuleIds'] = ['alpha', 'gamma']
                for module in clone_inventory['modules']:
                    module['enabled'] = module['id'] in clone_inventory['enabledModuleIds']
                reviewed = upgrade.review_clone(plan['plan_path'], clone_inventory, True)
                self.assertEqual(reviewed['status'], 'v12_modules_reviewed')
                self.assertEqual(reviewed['expected_enabled'], ['alpha', 'gamma'])
                self.assertFalse(reviewed['migration_ready'])
                self.assertEqual(
                    json.loads(Path(reviewed['review_path']).read_text())['plan_sha256'],
                    foundry_backup.storage.sha256_file(Path(plan['plan_path'])),
                )
                clone_inventory['modules'][1]['enabled'] = False
                activation_conflict = upgrade.review_clone(plan['plan_path'], clone_inventory, True)
                self.assertTrue(
                    any(
                        'saved configuration and active state disagree' in issue
                        for issue in activation_conflict['blockers']
                    )
                )
                clone_inventory['modules'][1]['enabled'] = True
                clone_inventory['modules'][0]['version'] = '9.0.0'
                mismatch = upgrade.review_clone(plan['plan_path'], clone_inventory, True)
                self.assertTrue(any('GM module version' in issue for issue in mismatch['blockers']))
                plan_file = Path(plan['plan_path'])
                original_plan = plan_file.read_text(encoding='utf-8')
                changed_plan = json.loads(original_plan)
                changed_plan['selected_modules'] = []
                plan_file.write_text(json.dumps(changed_plan), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'do not match'):
                    upgrade.review_clone(plan['plan_path'], clone_inventory, True)
                plan_file.write_text(original_plan, encoding='utf-8')
                migrated_world_path = (
                    Path(plan['clone_path']) / 'Data/worlds/fixture-world/world.json'
                )
                migrated_world = json.loads(migrated_world_path.read_text(encoding='utf-8'))
                migrated_world.update(coreVersion='13.351', systemVersion='4.0.0')
                migrated_world_path.write_text(json.dumps(migrated_world), encoding='utf-8')
                (Path(plan['clone_path']) / 'Data/systems/dnd5e/system.json').write_text(
                    json.dumps({'id': 'dnd5e', 'version': '4.0.0'}), encoding='utf-8'
                )
                for package_id in ('alpha', 'gamma', 'beta'):
                    (
                        Path(plan['clone_path']) / 'Data/modules' / package_id / 'module.json'
                    ).write_text(
                        json.dumps({'id': package_id, 'version': '2.0.0'}), encoding='utf-8'
                    )
                migrated_inventory = inventory()
                migrated_inventory['phase'] = 'migrated-clone'
                migrated_inventory['world']['coreVersion'] = '13.351'
                migrated_inventory['system']['version'] = '4.0.0'
                migrated_inventory['enabledModuleIds'] = ['alpha', 'gamma', 'beta']
                for module in migrated_inventory['modules']:
                    module['enabled'] = module['id'] in migrated_inventory['enabledModuleIds']
                    if module['enabled']:
                        module['version'] = '2.0.0'
                with self.assertRaisesRegex(ValueError, 'passing v12 review'):
                    upgrade.audit_migration(blocked['review_path'], migrated_inventory, True)
                pending = upgrade.audit_migration(reviewed['review_path'], migrated_inventory, True)
                self.assertEqual(pending['status'], 'blocked')
                self.assertTrue(
                    any('GM has not confirmed' in issue for issue in pending['blockers'])
                )
                checks = dict.fromkeys(
                    ('launch', 'scenes', 'journals', 'actors_items', 'modules'), True
                )
                audited = upgrade.audit_migration(
                    reviewed['review_path'], migrated_inventory, True, checks
                )
                self.assertEqual(audited['status'], 'reviewed')
                self.assertFalse(audited['cutover_ready'])
                self.assertTrue(Path(audited['audit_path']).is_file())
                migrated_inventory['enabledModuleIds'].append('Plutonium')
                migrated_inventory['modules'][3]['enabled'] = True
                unsafe = upgrade.audit_migration(
                    reviewed['review_path'], migrated_inventory, True, checks
                )
                self.assertTrue(any('Plutonium' in issue for issue in unsafe['blockers']))
                (
                    Path(backup['path'])
                    / 'User Data'
                    / 'Data'
                    / 'modules'
                    / 'alpha'
                    / 'module.json'
                ).write_text('{}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'checksum failed'):
                    upgrade.prepare_clone(
                        data['report_path'],
                        restored['receipt_path'],
                        str(root / 'another-clone'),
                        True,
                        True,
                    )
                self.assertFalse((root / 'another-clone').exists())

    def test_parse_official_page_shapes_and_core_bounds(self):
        self.assertEqual(
            foundry_catalog.stable_builds(
                '<li class="article release flexrow"><a href="/releases/14.368">Release</a>'
                '<span class="release-tag stable">Stable</span></li>'
                '<li class="article release flexrow"><a href="/releases/14.369">Release</a>'
                '<span class="release-tag testing">Testing</span></li>'
            ),
            ['14.368'],
        )
        page = (
            '<li class="package-version flexrow"><h4 class="package-title">Version 2.0.0</h4>'
            '<span>Foundry Version 12 - 13 (Verified 12.343)</span>'
            '<a href="https://example.org/module.json" title="Manifest Installation URL">Manifest URL</a></li>'
        )
        self.assertEqual(
            foundry_catalog.package_releases(page, 'alpha')[0]['compatibility']['maximum'], '13'
        )
        self.assertTrue(foundry_compat.compatible({'minimum': '12', 'maximum': '12'}, (12, 343)))
        self.assertFalse(foundry_compat.compatible({'minimum': '12', 'maximum': '12'}, (13, 341)))


if __name__ == '__main__':
    unittest.main()
