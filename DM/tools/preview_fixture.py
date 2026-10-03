"""Disposable UI fixture; never reads or writes the live campaign."""

import json
import os
import shutil
import sys
from pathlib import Path
from test_workflows import StudioIntegration, http_routes, workflow
from unittest.mock import patch

import foundry_backup
import foundry_upgrade

fixture = StudioIntegration()
fixture.setUp()
try:
    slug, brief = fixture.import_map()
    workflow.stage(workflow.create(slug, brief), fixture.proposal())
    world = fixture.root / 'Foundry User Data' / 'Data' / 'worlds' / 'fixture-world'
    world.mkdir(parents=True)
    (world / 'world.json').write_text((fixture.world / 'world.json').read_text())
    (world / 'maps').mkdir()
    (world / 'maps' / 'bridge.png').write_bytes(fixture.png)
    os.environ['FOUNDRY_DATA'] = str(fixture.root / 'Foundry User Data')
    os.environ['LOCALAPPDATA'] = str(fixture.root)
    (fixture.dm / 'forge').mkdir()
    shutil.copy2(
        Path(__file__).resolve().parents[1] / 'forge' / 'foundry-library-export.js',
        fixture.dm / 'forge' / 'foundry-library-export.js',
    )
    shutil.copy2(
        Path(__file__).resolve().parents[1] / 'forge' / 'foundry-upgrade-inventory.js',
        fixture.dm / 'forge' / 'foundry-upgrade-inventory.js',
    )
    if '--first-run' in sys.argv:
        (fixture.dm / 'data' / 'settings.json').unlink()
        url = fixture.url + '/'
    else:
        fixture.request('/api/settings', {'world_path': str(world)})
        url = fixture.url + '/#/library'
    snapshot = {
        'format': 'campaign-studio-foundry-library',
        'schema': 1,
        'world': {
            'id': 'fixture-world',
            'title': 'Fixture',
            'system': 'dnd5e',
            'coreVersion': '12.331',
        },
        'exportedAt': '2026-10-03T12:00:00Z',
        'documents': {
            'scenes': [
                {
                    'id': 'scene1',
                    'uuid': 'Scene.scene1',
                    'name': 'Old Bridge',
                    'summary': 'A mossy crossing.',
                }
            ],
            'journals': [
                {
                    'id': 'journal1',
                    'name': 'Bridge Legend',
                    'pages': [{'id': 'page1', 'name': 'Clue', 'text': 'A hidden inscription.'}],
                }
            ],
            'actors': [
                {
                    'id': 'actor1',
                    'name': 'Bridge Keeper',
                    'type': 'npc',
                    'summary': 'A cautious guide.',
                }
            ],
            'items': [{'id': 'item1', 'name': 'Bronze Key'}],
        },
    }
    snapshot_path = fixture.root / 'synthetic-library.json'
    snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
    upgrade_inventory = {
        'format': foundry_upgrade.INVENTORY_FORMAT,
        'schema': 2,
        'exportedAt': '2026-10-03T12:00:00Z',
        'world': snapshot['world'],
        'system': {
            'id': 'dnd5e',
            'version': '3.0.0',
            'manifest': 'https://example.org/system.json',
        },
        'enabledModuleIds': ['fixture-module', 'Plutonium'],
        'modules': [
            {'id': 'fixture-module', 'version': '1.0.0', 'enabled': True},
            {'id': 'Plutonium', 'version': '1.0.0', 'enabled': True},
        ],
    }
    inventory_path = fixture.root / 'synthetic-upgrade-inventory.json'
    inventory_path.write_text(json.dumps(upgrade_inventory), encoding='utf-8')

    def fixture_release(package_id, version, minimum, maximum, verified):
        compatibility = {'minimum': minimum, 'maximum': maximum, 'verified': verified}
        return {
            'id': package_id,
            'version': version,
            'manifest': f'https://example.org/{package_id}/{version}.json',
            'compatibility': compatibility,
            'manifest_compatibility': compatibility,
            'requires': [],
            'systems': [],
        }

    fixture_catalog = {
        'source': 'synthetic-preview',
        'builds': ['13.351', '12.343', '12.331'],
        'packages': {
            'dnd5e': {
                'status': 'listed',
                'releases': [
                    fixture_release('dnd5e', '4.0.0', '13', '13', '13'),
                    fixture_release('dnd5e', '3.0.0', '12', '12', '12'),
                ],
            },
            'fixture-module': {
                'status': 'listed',
                'releases': [
                    fixture_release('fixture-module', '2.0.0', '13', '13', '12'),
                    fixture_release('fixture-module', '1.0.0', '12', '12', '12'),
                ],
            },
            'Plutonium': {'status': 'unlisted', 'releases': []},
        },
    }
    backup = {'path': ''}
    if '--first-run' not in sys.argv:
        with patch.object(foundry_backup, 'running_foundry', return_value=[]):
            backup = foundry_backup.create(str(fixture.root / 'backups'), confirmed_closed=True)
    print('FIXTURE_URL ' + url, flush=True)
    print('SNAPSHOT_PATH ' + str(snapshot_path), flush=True)
    print('INVENTORY_PATH ' + str(inventory_path), flush=True)
    print('BACKUP_PATH ' + backup['path'], flush=True)
    with (
        patch.object(http_routes.shutil, 'which', return_value=None),
        patch.object(foundry_upgrade, 'collect_catalog', return_value=fixture_catalog),
    ):
        input('Press Enter to close the disposable preview.\n')
finally:
    fixture.tearDown()
