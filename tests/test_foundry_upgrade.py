"""Synthetic inventory, directory and dependency checks for the upgrade report."""

import contextlib
import json
import sys
import tempfile
import unittest
import urllib.error
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


def system_range(maximum, minimum='4.0'):
    return {
        'id': 'dnd5e',
        'type': 'system',
        'compatibility': {'minimum': minimum, 'maximum': maximum},
    }


def directory_row(version, foundry, manifest):
    """One release row in the official package directory's markup."""
    return (
        '<li class="package-version flexrow"><div>'
        f'<h4 class="package-title">Version {version}</h4></div>'
        '<div class="package-tags"><span class="tag fromver">'
        f'<i class="fas fa-code-branch"></i> Foundry Version {foundry}</span>'
        f'<span class="tag install"><a href="{manifest}" title="Manifest Installation URL" '
        'target="_blank">Manifest URL</a></span></div></li>'
    )


class DirectoryFetcher:
    """Serve synthetic stable builds, directory pages and manifests, recording each URL read."""

    BUILDS = ('14.369', '13.351', '13.346', '12.343', '12.331')

    def __init__(self):
        self.calls = []
        self.rows = {}  # package ID -> [(directory version, Foundry range text, manifest)]

    def add(self, package_id, version, foundry, compatibility, tag='', requires=(), systems=()):
        manifest = {
            'id': package_id,
            'version': tag + version,
            'compatibility': compatibility,
            'relationships': {'requires': list(requires), 'systems': list(systems)},
        }
        self.rows.setdefault(package_id, []).append((version, foundry, manifest))

    def manifest_reads(self):
        return sum(1 for url in self.calls if not url.startswith('https://foundryvtt.com/'))

    def __call__(self, url, *_args, **_kwargs):
        self.calls.append(url)
        if url == foundry_compat.RELEASES_URL:
            return ''.join(
                f'<li class="article release flexrow"><a href="/releases/{build}">Release</a>'
                '<span class="release-tag stable">Stable</span></li>'
                for build in self.BUILDS
            )
        if url.startswith(foundry_catalog.PACKAGE_URL):
            package_id = url.split('/')[-2]
            if package_id not in self.rows:
                raise urllib.error.HTTPError(url, 404, 'Not Found', {}, None)
            return ''.join(
                directory_row(version, foundry, f'https://example.org/{package_id}/{version}.json')
                for version, foundry, _manifest in self.rows[package_id]
            )
        package_id, file_name = url.split('/')[-2:]
        version = file_name.removesuffix('.json')
        return json.dumps(
            next(item for key, _range, item in self.rows[package_id] if key == version)
        )


def large_world():
    """A synthetic world shaped like a real 33-module one whose upgrade report recommended nothing."""
    fetcher = DirectoryFetcher()
    for version, minimum in [
        ('6.0.6', '14.367'),
        ('5.3.3', '13.347'),
        ('5.0.4', '13.341'),
        ('4.4.4', '12.331'),
        ('4.3.9', '12.331'),
        ('3.3.1', '11.315'),
        ('2.1.5', '10.291'),
    ]:
        verified = minimum.split('.')[0]
        compatibility = {'minimum': minimum, 'verified': verified}
        fetcher.add('dnd5e', version, f'{minimum}+ (Verified {verified})', compatibility)
    # libWrapper declares a pre-v9 minimum on every release.
    for version in [*(f'1.{minor}.0.0' for minor in range(13)), '1.13.5.1']:
        compatibility = {'minimum': '0.6.5', 'verified': '13'}
        fetcher.add('lib-wrapper', version, '0.6.5+ (Verified 13)', compatibility)
    fetcher.add('socketlib', '1.1.4', '11+ (Verified 13)', {'minimum': '11', 'verified': '13'})
    for patch_number in range(5):
        compatibility = {'minimum': '0.7.9', 'verified': '0.8.9'}
        fetcher.add('socketlib', f'1.0.{patch_number}', '0.7.9+ (Verified 0.8.9)', compatibility)
    # Only dnd5e 4.x: v13 retains it only if the system stays on 4.4.4.
    fetcher.add(
        'charges-module',
        '5.0.0-beta.2',
        '12+ (Verified 12.331)',
        {'minimum': '12', 'verified': '12.331', 'maximum': '13.999'},
        systems=[system_range('4.4.999', '4.3.0')],
    )
    fetcher.add('charges-module', '4.2.0', '12+ (Verified 12)', {'minimum': '12', 'maximum': '12'})
    libraries = [dependency('lib-wrapper'), dependency('socketlib')]
    names = [f'module-{number:02}' for number in range(1, 31)]
    for name in names:
        for generation, systems in (('13', ('5.3.x', '5.0')), ('12', ('4.4.x', '4.0'))):
            for patch_number in reversed(range(15)):
                fetcher.add(
                    name,
                    f'{generation}.0.{patch_number}',
                    f'{generation}+ (Verified {generation})',
                    {'minimum': generation, 'verified': generation, 'maximum': generation + '.999'},
                    requires=libraries,
                    systems=[system_range(*systems)],
                )
    # Last in the queue, with manifests tagged "v4.6.12" for directory release 4.6.12.
    fetcher.add(
        'token-attacher', '4.6.12', '13+ (Verified 13)', {'minimum': 13, 'verified': 13}, 'v'
    )
    for version in ('4.6.11', '4.6.10'):
        compatibility = {'minimum': 12, 'verified': 12, 'maximum': 12}
        fetcher.add('token-attacher', version, '12+ (Verified 12)', compatibility, 'v')
    installed = [
        ('lib-wrapper', '1.13.2.0'),
        ('socketlib', '1.1.0'),
        ('charges-module', '5.0.0-beta.2'),
        *((name, '12.0.10') for name in names),
        ('token-attacher', 'v4.6.10'),
    ]
    data = inventory()
    data['system']['version'] = '4.4.4'
    data['modules'] = [
        {'id': package_id, 'version': version, 'enabled': True, 'relationships': {}}
        for package_id, version in installed
    ]
    data['enabledModuleIds'] = [package_id for package_id, _version in installed]
    return data, fetcher


@contextlib.contextmanager
def selected_backup(backup):
    """Select the synthetic world and accept `backup` as its verified backup."""
    world = {'id': 'fixture-world', 'system': 'dnd5e', 'foundry_version': '12.331'}
    verified = {
        'path': str(backup),
        'world': {'id': 'fixture-world', 'system': 'dnd5e', 'core_version': '12.331'},
    }
    with (
        patch.object(upgrade.config, 'settings', return_value={'world_path': str(backup)}),
        patch.object(upgrade.config, 'world_info', return_value=world),
        patch.object(foundry_backup, 'verify', return_value=verified) as verify,
    ):
        yield verify


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

        def version(package_id):
            return '3.0.0' if package_id == 'dnd5e' else '1.0.0'

        def package_page(package_id):
            return (
                f'<li class="package-version flexrow"><h4 class="package-title">'
                f'Version {version(package_id)}</h4>'
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
                {'id': package_id, 'version': version(package_id), 'relationships': relationships}
            )

        with patch.object(foundry_catalog, 'fetch', side_effect=fake_fetch):
            catalog = foundry_catalog.collect_catalog(data)
            self.assertEqual(catalog.builds, ['13.351', '12.331'])
            self.assertEqual(catalog.manifest_reads, 0)  # The solver reads manifests it needs.
            result = foundry_solver.analyze(data, catalog)
        packages = result['catalog_evidence']
        self.assertEqual(packages['alpha']['releases'][0]['requires'][0]['id'], 'beta')
        self.assertEqual(packages['beta']['status'], 'listed')
        self.assertNotIn('requires', packages['beta']['releases'][0])
        beta = next(item for item in result['modules'] if item['id'] == 'beta')
        self.assertFalse(beta['proposed_enabled'])
        self.assertIn('GM approval', ' '.join(result['candidates'][0]['blockers']))

    def test_directory_rows_with_pre_v9_bounds_and_wildcard_system_ranges_are_usable(self):
        # libWrapper lists every release as "Foundry Version 0.6.5+", which no row parse accepted.
        page = ''.join(
            directory_row(version, foundry, f'https://example.org/lib-wrapper/{version}.json')
            for version, foundry in [
                ('1.13.5.1', '0.6.5+ (Verified 14)'),
                ('1.9.2.0', '0.6.5+ (Verified 0.8.9)'),
                ('1.0.0.0', '0.6.5 - 0.7.9 (Verified 0.7.9)'),
                ('0.9.0.0', '10.285.1.2+ (Verified 10)'),
            ]
        )
        rows = foundry_catalog.package_releases(page, 'lib-wrapper')
        self.assertEqual([row['version'] for row in rows], ['1.13.5.1', '1.9.2.0', '1.0.0.0'])
        self.assertEqual(
            rows[1]['compatibility'], {'minimum': '0.6.5', 'maximum': '', 'verified': '0.8.9'}
        )
        build = (13, 351)
        self.assertTrue(foundry_compat.compatible(rows[0]['compatibility'], build))
        self.assertFalse(foundry_compat.compatible(rows[2]['compatibility'], build))
        self.assertTrue(foundry_compat.is_verified(rows[0]['compatibility'], build))
        self.assertFalse(foundry_compat.is_verified(rows[1]['compatibility'], build))
        # A module declaring dnd5e maximum "4.4.x" supports 4.4.4, as Foundry reads it.
        self.assertTrue(foundry_compat.version_covers('4.4.4', {'maximum': '4.4.x'}))
        self.assertFalse(foundry_compat.version_covers('4.5.0', {'maximum': '4.4.x'}))
        self.assertTrue(foundry_compat.version_covers('4.0.0', {'minimum': '4.x'}))

    def test_manifest_tagged_with_a_leading_v_matches_its_directory_release(self):
        def fake_fetch(url, *_args, **_kwargs):
            version = url.rsplit('/', 1)[-1].removesuffix('.json')
            return json.dumps({'id': 'token-attacher', 'version': version.replace('-', 'v')})

        catalog = foundry_catalog.Catalog({'builds': [], 'packages': {}}, fake_fetch)
        tagged = {'version': '4.6.12', 'manifest': 'https://example.org/-4.6.12.json'}
        renamed = {'version': '4.6.12', 'manifest': 'https://example.org/-4.6.11.json'}
        catalog.review('token-attacher', tagged)
        catalog.review('token-attacher', renamed)
        self.assertNotIn('error', tagged)
        # The clone installs the manifest, so the plan and audit compare its spelling.
        self.assertEqual((tagged['version'], tagged['directory_version']), ('v4.6.12', '4.6.12'))
        self.assertIn('version differs', renamed['error'])

    def test_report_on_a_large_world_reads_only_the_releases_it_needs(self):
        # The shape of a real 33-module dnd5e world that recommended nothing: long release
        # histories, libWrapper listed with a pre-v9 minimum, token-attacher last in the queue with
        # manifests tagged "v4.6.12", and one module tied to dnd5e 4.x while v13 modules need 5.x.
        data, fake_fetch = large_world()
        with tempfile.TemporaryDirectory(prefix='upgrade-large-fixture-') as folder:
            backup = Path(folder)
            with (
                selected_backup(backup),
                patch.object(foundry_catalog, 'fetch', side_effect=fake_fetch),
            ):
                stay = upgrade.report(data, str(backup))
                first_reads = fake_fetch.manifest_reads()
                fake_fetch.calls.clear()
                newer = upgrade.report(data, str(backup), disabled_modules=['charges-module'])
        self.assertEqual(stay['recommended_build'], '12.343')
        self.assertEqual(stay['system']['selected_version'], '4.4.4')
        v13 = next(item for item in stay['candidates'] if item['build'] == '13.351')
        self.assertIn(
            'charges-module: no release for Foundry 13.351 works with dnd5e 5.3.3',
            ' '.join(v13['blockers']),
        )
        self.assertNotIn('search limit', ' '.join(v13['blockers']))
        self.assertEqual(newer['recommended_build'], '13.351')
        self.assertEqual(newer['system']['selected_version'], '5.3.3')
        modules = {item['id']: item for item in newer['modules']}
        self.assertEqual(modules['lib-wrapper']['directory_status'], 'listed')
        self.assertEqual(modules['lib-wrapper']['selected_version'], '1.13.5.1')
        self.assertEqual(modules['token-attacher']['selected_version'], 'v4.6.12')
        self.assertEqual(modules['module-30']['selected_version'], '13.0.14')
        self.assertFalse(modules['charges-module']['proposed_enabled'])
        # The directory lists over 900 releases; the old collector stopped after reading 600.
        self.assertLess(first_reads, 120)
        self.assertLess(fake_fetch.manifest_reads(), 120)

    def test_solver_jumps_back_to_the_package_that_caused_a_conflict(self):
        data = inventory()
        data['system']['version'] = '1.0.0'
        data['modules'] = [
            {'id': f'module-{number:02}', 'version': '1.0', 'enabled': True}
            for number in range(1, 31)
        ]
        packages = {
            'dnd5e': {
                'status': 'listed',
                'releases': [
                    release('dnd5e', f'{major}.0.0', '13', '', '13') for major in (1, 2, 3)
                ],
            }
        }
        for module in data['modules']:
            packages[module['id']] = {
                'status': 'listed',
                'releases': [
                    release(module['id'], f'1.{minor}', '13', '', '13') for minor in range(10)
                ],
            }
        # Only the last module limits the system, so 10^29 combinations of the others cannot help.
        for item in packages['module-30']['releases']:
            item['systems'] = [{'id': 'dnd5e', 'compatibility': {'maximum': '2.x'}}]
        result = foundry_solver.analyze(data, {'builds': ['13.351'], 'packages': packages})
        self.assertEqual(result['recommended_build'], '13.351')
        self.assertEqual(result['system']['selected_version'], '2.0.0')
        self.assertEqual(result['modules'][0]['selected_version'], '1.9')
        # A library missing from the directory, required by the last module's newest release, sends
        # the search straight back to that module.
        packages['module-30']['releases'][-1]['requires'] = [dependency('missing-library')]
        packages['missing-library'] = {'status': 'unknown', 'releases': []}
        result = foundry_solver.analyze(
            data,
            {'builds': ['13.351'], 'packages': packages},
            approved_dependencies=['missing-library'],
        )
        self.assertEqual(result['recommended_build'], '13.351')
        self.assertEqual(result['modules'][29]['selected_version'], '1.8')
        self.assertEqual(result['modules'][28]['selected_version'], '1.9')

    def test_solver_never_downgrades_an_installed_package(self):
        data = inventory()
        data['system']['version'] = '4.4.4'
        data['modules'] = [{'id': 'sheet', 'version': '9.8.3', 'enabled': True}]
        packages = {
            'dnd5e': {
                'status': 'listed',
                'releases': [
                    release('dnd5e', version, '12', '', '12') for version in ('4.4.4', '4.3.9')
                ],
            },
            'sheet': {
                'status': 'listed',
                'releases': [
                    release('sheet', '9.8.4', '12', '', '12', systems=[system_range('4.3.9')]),
                    release('sheet', '9.8.3', '12', '', '12', systems=[system_range('4.3.9')]),
                    release('sheet', '9.0.0', '12', '', '12'),
                ],
            },
        }
        result = foundry_solver.analyze(data, {'builds': ['12.343'], 'packages': packages})
        # dnd5e 4.3.9 or sheet 9.0.0 would make a full match, but each is older than installed.
        self.assertIsNone(result['recommended_build'])
        self.assertEqual(
            result['candidates'][0]['blockers'],
            ['sheet: no release for Foundry 12.343 works with dnd5e 4.4.4.'],
        )
        packages['sheet']['releases'] = [release('sheet', '9.0.0', '12', '', '12')]
        result = foundry_solver.analyze(data, {'builds': ['12.343'], 'packages': packages})
        self.assertEqual(
            result['candidates'][0]['blockers'],
            ['sheet: no release for Foundry 12.343 is as new as the installed 9.8.3.'],
        )

    def test_unreadable_manifest_is_retried_once_and_never_replaced_by_an_older_release(self):
        limited = {'calls': 0, 'headers': {}}

        def fake_fetch(url, *_args, **_kwargs):
            version = url.rsplit('/', 2)[-2]
            if version == '2.0.0' and limited['calls'] < limited['limit']:
                limited['calls'] += 1
                headers = limited['headers']
                raise urllib.error.HTTPError(url, 429, 'Too Many Requests', headers, None)
            return json.dumps({'id': 'alpha', 'version': version})

        def catalog():
            releases = [
                {
                    'id': 'alpha',
                    'version': version,
                    'manifest': f'https://example.org/alpha/{version}/module.json',
                    'compatibility': {'minimum': '12', 'maximum': '', 'verified': '12'},
                }
                for version in ('2.0.0', '1.0.0')
            ]
            return foundry_catalog.Catalog(
                {
                    'builds': ['12.343'],
                    'packages': {
                        'dnd5e': {
                            'status': 'listed',
                            'releases': [release('dnd5e', '3.0.0', '12', '', '12')],
                        },
                        'alpha': {'status': 'listed', 'releases': releases},
                    },
                },
                fake_fetch,
            )

        data = inventory()
        data['modules'] = [{'id': 'alpha', 'version': '1.0.0', 'enabled': True}]
        with patch.object(foundry_catalog.time, 'sleep') as pause:
            limited.update(calls=0, limit=1)
            result = foundry_solver.analyze(data, catalog())
            self.assertEqual(result['modules'][0]['selected_version'], '2.0.0')
            pause.reset_mock()
            limited.update(calls=0, limit=2)
            result = foundry_solver.analyze(data, catalog())
            pause.assert_called_once_with(5)
            self.assertIsNone(result['recommended_build'])
            self.assertIn('run the report again later', result['candidates'][0]['blockers'][0])
            self.assertNotIn('requires', result['catalog_evidence']['alpha']['releases'][1])
            # A host asking for a long pause is not waited for; the blocker says how long.
            pause.reset_mock()
            limited.update(calls=0, limit=1, headers={'Retry-After': '2919'})
            result = foundry_solver.analyze(data, catalog())
            pause.assert_not_called()
        self.assertIn('wait about 49 min', result['candidates'][0]['blockers'][0])

    def test_report_requires_matching_inventory_and_verified_backup(self):
        with tempfile.TemporaryDirectory(prefix='upgrade-fixture-') as folder:
            backup = Path(folder)
            with selected_backup(backup) as verify:
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

    def test_report_accepts_disabled_module_requiring_underscore_library(self):
        # Foundry library modules may start with an underscore: Custom CSS requires `_CodeMirror`.
        data = inventory()
        data['modules'].append(
            {
                'id': 'custom-css',
                'version': '2.0.0',
                'enabled': False,
                'relationships': {'requires': [dependency('_CodeMirror')]},
            }
        )
        with tempfile.TemporaryDirectory(prefix='upgrade-fixture-') as folder:
            backup = Path(folder)
            # The library is installed in the backup but absent from the GM export.
            library = backup / 'User Data' / 'Data' / 'modules' / '_CodeMirror'
            library.mkdir(parents=True)
            (library / 'module.json').write_text(
                json.dumps({'id': '_CodeMirror', 'version': '5.65.0'}), encoding='utf-8'
            )
            with selected_backup(backup):
                result = upgrade.report(data, str(backup), catalog=catalog())
        modules = {item['id']: item for item in result['modules']}
        self.assertEqual(modules['custom-css']['original_requires'][0]['id'], '_CodeMirror')
        self.assertFalse(modules['custom-css']['proposed_enabled'])
        self.assertFalse(modules['_CodeMirror']['proposed_enabled'])
        self.assertEqual(result['recommended_build'], '12.343')
        for unsafe in ('', '.', '..', '_.x', '_/x', '_\\x'):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                foundry_compat.relationships({'relationships': {'requires': [{'id': unsafe}]}})

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
                with self.assertRaisesRegex(ValueError, 'Close both'):
                    upgrade.review_cutover(audited['audit_path'])
                with self.assertRaisesRegex(ValueError, 'passing'):
                    upgrade.review_cutover(pending['audit_path'], True)
                cutover = upgrade.review_cutover(audited['audit_path'], True)
                self.assertTrue(cutover['cutover_ready'])
                self.assertEqual(cutover['clone_path'], plan['clone_path'])
                self.assertTrue(Path(cutover['original_user_data']).samefile(user_data))
                self.assertEqual(
                    upgrade.review_cutover(audited['audit_path'], True)['review_path'],
                    cutover['review_path'],
                )
                with patch.object(foundry_backup, 'running_foundry', return_value=[{'pid': 1}]):
                    with self.assertRaisesRegex(ValueError, 'Close both'):
                        upgrade.review_cutover(audited['audit_path'], True)
                with patch.object(
                    foundry_backup, 'running_foundry', side_effect=[[], [{'pid': 1}]]
                ):
                    with self.assertRaisesRegex(ValueError, 'started during'):
                        upgrade.review_cutover(audited['audit_path'], True)
                with patch.object(
                    upgrade.config,
                    'world_info',
                    return_value={'id': 'different-world', 'path': str(world_path)},
                ):
                    with self.assertRaisesRegex(ValueError, 'Select the original'):
                        upgrade.review_cutover(audited['audit_path'], True)
                original_world = world_path / 'world.json'
                original_world.write_text(
                    json.dumps({**world_json, 'title': 'Changed after backup'}), encoding='utf-8'
                )
                with self.assertRaisesRegex(ValueError, 'checksum failed'):
                    upgrade.review_cutover(audited['audit_path'], True)
                original_world.write_text(json.dumps(world_json), encoding='utf-8')
                migrated_world_path.write_text(
                    json.dumps({**migrated_world, 'coreVersion': '12.331'}), encoding='utf-8'
                )
                with self.assertRaisesRegex(ValueError, 'changed after the audit'):
                    upgrade.review_cutover(audited['audit_path'], True)
                migrated_world_path.write_text(json.dumps(migrated_world), encoding='utf-8')
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


class PinnedDownloadTests(unittest.TestCase):
    PUBLIC = (2, 1, 6, '', ('93.184.216.34', 443))
    PRIVATE = (2, 1, 6, '', ('127.0.0.1', 443))

    class FakeSocket:
        connected = []

        def __init__(self, *args):
            pass

        def settimeout(self, _value):
            pass

        def connect(self, address):
            self.connected.append(address)
            raise ConnectionRefusedError('stop after connect')

        def close(self):
            pass

    def test_rebinding_answer_cannot_redirect_the_connection(self):
        answers = [[self.PUBLIC], [self.PRIVATE]]
        self.FakeSocket.connected = []
        with (
            patch.object(
                foundry_catalog.socket, 'getaddrinfo', side_effect=lambda *a, **k: answers.pop(0)
            ),
            patch.object(foundry_catalog.socket, 'socket', self.FakeSocket),
        ):
            with self.assertRaises(OSError):
                foundry_catalog.fetch('https://rebind.example.org/module.json')
        self.assertEqual(self.FakeSocket.connected, [('93.184.216.34', 443)])
        self.assertEqual(answers, [[self.PRIVATE]])

    def test_private_resolution_opens_no_connection(self):
        self.FakeSocket.connected = []
        with (
            patch.object(
                foundry_catalog.socket, 'getaddrinfo', return_value=[self.PUBLIC, self.PRIVATE]
            ),
            patch.object(foundry_catalog.socket, 'socket', self.FakeSocket),
        ):
            with self.assertRaisesRegex(Exception, 'private address'):
                foundry_catalog.fetch('https://mixed.example.org/module.json')
        self.assertEqual(self.FakeSocket.connected, [])

    def test_environment_proxy_cannot_resolve_the_target_again(self):
        lookups = []
        self.FakeSocket.connected = []

        def resolve(host, *args, **kwargs):
            lookups.append(host)
            return [self.PUBLIC]

        with (
            patch.dict('os.environ', {'HTTPS_PROXY': 'http://127.0.0.1:8888'}),
            patch.object(foundry_catalog.socket, 'getaddrinfo', side_effect=resolve),
            patch.object(foundry_catalog.socket, 'socket', self.FakeSocket),
        ):
            with self.assertRaises(OSError):
                foundry_catalog.fetch('https://public.example.org/module.json')
        self.assertEqual(lookups, ['public.example.org'])
        self.assertEqual(self.FakeSocket.connected, [('93.184.216.34', 443)])


if __name__ == '__main__':
    unittest.main()
