"""Disposable UI fixture; never reads or writes the live campaign."""

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
    fixture.request('/api/settings', {'world_path': str(world)})
    print('FIXTURE_URL ' + fixture.url + '/#/maps/' + slug, flush=True)
    with patch.object(http_routes.shutil, 'which', return_value=None):
        input('Press Enter to close the disposable preview.\n')
finally:
    fixture.tearDown()
