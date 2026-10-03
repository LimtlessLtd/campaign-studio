"""Disposable UI fixture; never reads or writes the live campaign."""

import json
import os
import shutil
import sys
from pathlib import Path
from test_workflows import StudioIntegration, http_routes, workflow
from unittest.mock import patch

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
    print('FIXTURE_URL ' + url, flush=True)
    print('SNAPSHOT_PATH ' + str(snapshot_path), flush=True)
    with patch.object(http_routes.shutil, 'which', return_value=None):
        input('Press Enter to close the disposable preview.\n')
finally:
    fixture.tearDown()
