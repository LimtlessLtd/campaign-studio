"""Disposable UI fixture; never reads or writes the live campaign."""

from test_workflows import StudioIntegration, workflow
from unittest.mock import patch

fixture = StudioIntegration()
fixture.setUp()
try:
    slug, brief = fixture.import_map()
    workflow.stage(workflow.create(slug, brief), fixture.proposal())
    print('FIXTURE_URL ' + fixture.url + '/#/maps/' + slug, flush=True)
    with patch('server.shutil.which', return_value=None):
        input('Press Enter to close the disposable preview.\n')
finally:
    fixture.tearDown()
