"""Browser smoke and accessibility checks against a disposable synthetic campaign.

The checks drive Chromium through Playwright and inject axe-core from node_modules. Without either they
skip locally; CI sets CAMPAIGN_STUDIO_BROWSER_TESTS=required so a missing browser fails instead.
"""

import json
import os
import re
import sys
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM' / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))

import test_workflows as fixtures  # noqa: E402
import leveldb_writer as writer  # noqa: E402
from test_workflows import campaign_core, workflow  # noqa: E402

AXE = ROOT / 'node_modules' / 'axe-core' / 'axe.min.js'
REQUIRED = os.environ.get('CAMPAIGN_STUDIO_BROWSER_TESTS') == 'required'
DESKTOP = {'width': 1280, 'height': 800}
NARROW = {'width': 430, 'height': 900}
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082'
)
# axe impact levels that fail the build. Minor and moderate findings are not gated yet.
BLOCKING = ('serious', 'critical')

try:
    from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright
except ImportError:  # pragma: no cover - depends on the developer's environment
    sync_playwright = None


def setUpModule():
    global playwright, browser
    problem = None
    if sync_playwright is None:
        problem = 'Playwright is not installed (pip install -r requirements-dev.txt)'
    elif not AXE.is_file():
        problem = 'axe-core is not installed (npm ci)'
    if problem is None:
        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            playwright.stop()
            problem = (
                'Chromium is not installed (python -m playwright install chromium): '
                + str(error).splitlines()[0]
            )
    if problem:
        if REQUIRED:
            raise RuntimeError(problem)
        raise unittest.SkipTest(problem)


def tearDownModule():
    browser.close()
    playwright.stop()


class BrowserSmoke(unittest.TestCase):
    def setUp(self):
        # Only the fixture: importing the class here would also collect its integration tests.
        self.studio = fixtures.StudioIntegration()
        self.studio.setUp()
        self.addCleanup(self.studio.tearDown)
        self.context = browser.new_context(viewport=DESKTOP)
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(self.errors, [], 'uncaught page errors'))

    def open(self, hash_path):
        self.page.goto(self.studio.url + '/' + hash_path)
        expect(self.page.locator('#main h1').first).to_be_visible()

    def stored(self, name):
        return campaign_core.read_json(campaign_core.doc_path(name))

    def assert_accessible(self, label):
        """Fail on serious or critical axe-core findings in the page as currently shown."""
        self.page.add_script_tag(path=str(AXE))
        result = self.page.evaluate(
            "axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa']}})"
        )
        found = [
            f'{v["id"]} ({v["impact"]}): {v["help"]} -> '
            + ', '.join(' '.join(node['target']) for node in v['nodes'][:5])
            for v in result['violations']
            if v['impact'] in BLOCKING
        ]
        self.assertEqual(found, [], label)

    def test_first_run_wizard_connects_a_world(self):
        (self.studio.dm / 'data' / 'settings.json').unlink()
        user_data = self.studio.root / 'User Data'
        world = user_data / 'Data' / 'worlds' / 'wizard-world'
        world.mkdir(parents=True)
        (world / 'world.json').write_text(
            json.dumps(
                {
                    'id': 'wizard-world',
                    'title': 'Wizard world',
                    'system': 'dnd5e',
                    'compatibility': {},
                }
            )
        )
        self.page.goto(self.studio.url + '/')
        expect(self.page).to_have_url(self.studio.url + '/#/welcome')
        self.assert_accessible('first-run wizard')

        self.page.get_by_role('button', name='Create project and import world').click()
        expect(self.page.get_by_text('Name your Studio project.').last).to_be_visible()
        self.page.get_by_label('Campaign name').fill('Synthetic campaign')
        self.page.get_by_label('Foundry User Data folder').fill(str(user_data))
        self.page.get_by_role('button', name='Scan for worlds').click()
        self.page.get_by_label('Wizard world').check()
        self.page.get_by_role('button', name='Create project and import world').click()

        expect(self.page).to_have_url(self.studio.url + '/#/library')
        expect(self.page.locator('.campaign-switch')).to_contain_text('Synthetic campaign')
        expect(self.page.locator('.library-status')).to_contain_text(
            'World connected, but import failed'
        )
        expect(self.page.get_by_text('Use the export macro instead', exact=True)).to_be_visible()
        settings = self.stored('settings')
        self.assertEqual(settings['campaign_name'], 'Synthetic campaign')
        self.assertEqual(Path(settings['world_path']).resolve(), world.resolve())

    def test_first_run_imports_codex_and_displays_a_foundry_image(self):
        (self.studio.dm / 'data' / 'settings.json').unlink()
        user_data = self.studio.root / 'User Data'
        world = user_data / 'Data' / 'worlds' / 'wizard-world'
        world.mkdir(parents=True)
        (world / 'world.json').write_text(
            json.dumps({'id': 'wizard-world', 'title': 'Wizard world', 'system': 'dnd5e'}),
            encoding='utf-8',
        )
        Image.new('RGB', (1, 1), 'red').save(world / 'portrait.png')
        actor = {
            '_id': 'a1',
            'name': 'Mira',
            'type': 'npc',
            'img': 'worlds/wizard-world/portrait.png',
        }
        writer.database(
            world / 'data' / 'actors',
            logs=[writer.log([[(b'!actors!a1', json.dumps(actor).encode())]])],
        )
        self.page.goto(self.studio.url + '/')
        self.page.get_by_label('Campaign name').fill('Synthetic campaign')
        self.page.get_by_label('Foundry User Data folder').fill(str(user_data))
        self.page.get_by_role('button', name='Scan for worlds').click()
        self.page.get_by_label('Wizard world').check()
        self.page.get_by_role('button', name='Create project and import world').click()

        expect(self.page).to_have_url(self.studio.url + '/#/library')
        expect(self.page.locator('.library-status')).to_contain_text('1 added')
        self.page.get_by_role('link', name='Campaign codex').click()
        expect(self.page.get_by_text('Mira').first).to_be_visible()
        image = self.page.locator('.codex .thumb[src]')
        expect(image).to_have_count(1)
        self.assertIn('/api/foundry/asset?', image.get_attribute('src'))
        self.assertEqual(
            self.context.request.get(self.studio.url + image.get_attribute('src')).status, 200
        )

    def test_pin_editor_persists_across_navigation(self):
        slug, _brief = self.studio.import_map()
        self.open('#/maps/' + slug)
        self.page.get_by_role('button', name='Area 1: Landing').click()
        name = self.page.get_by_label('Name', exact=True)
        expect(name).to_have_value('Landing')
        self.assert_accessible('map studio with a selected pin')

        name.fill('Lantern landing')
        self.page.get_by_label('Read-aloud description').fill('Fog hangs over the water.')
        expect(self.page.locator('#saved')).to_contain_text('Saved')
        self.page.get_by_role('link', name='Campaign codex').click()
        expect(self.page.locator('#main h1').first).to_be_visible()
        self.page.go_back()

        self.page.get_by_role('button', name='Area 1: Lantern landing').click()
        expect(self.page.get_by_label('Name', exact=True)).to_have_value('Lantern landing')
        expect(self.page.get_by_label('Read-aloud description')).to_have_value(
            'Fog hangs over the water.'
        )
        area = self.stored('mapkey/' + slug)['areas'][0]
        self.assertEqual(
            (area['name'], area['text']), ('Lantern landing', 'Fog hangs over the water.')
        )

    def test_proposal_review_applies_to_the_campaign(self):
        slug, brief = self.studio.import_map()
        workflow.stage(workflow.create(slug, brief), self.studio.proposal())
        self.open('#/maps/' + slug)
        self.page.get_by_role('button', name='AI workflow').click()
        review = self.page.locator('summary', has_text='Review proposed content')
        review.click()
        expect(self.page.locator('.draft-entry b', has_text='The Watcher')).to_be_visible()
        self.assert_accessible('proposal review')

        self.page.get_by_role('button', name='Apply to campaign').click()
        expect(self.page.get_by_text('Campaign content applied.')).to_be_visible()
        codex = self.stored('codex')['entries']
        self.assertIn('The Watcher', [entry['name'] for entry in codex])
        self.page.get_by_role('link', name='Campaign codex').click()
        expect(self.page.get_by_text('The Watcher').first).to_be_visible()
        self.assert_accessible('codex')

    def test_world_map_pin_links_a_battle_map(self):
        slug, _brief = self.studio.import_map()
        self.open('#/world')
        self.page.get_by_role('button', name='Add a world map').click()
        self.page.get_by_label('Name', exact=True).fill('The Realm')
        self.page.locator('dialog input[type=file]').set_input_files(
            {'name': 'realm.png', 'mimeType': 'image/png', 'buffer': PNG}
        )
        expect(self.page.get_by_text('Image uploaded.')).to_be_visible()
        self.page.get_by_role('button', name='Add world map').click()
        expect(self.page.get_by_role('heading', name='The Realm', level=1)).to_be_visible()

        self.page.get_by_role('button', name='Add pin').click()
        expect(self.page.locator('.map-stage img')).to_be_visible()
        self.page.locator('.map-stage img').click(position={'x': 3, 'y': 3})
        self.page.get_by_label('Place name').fill('Lantern Quay')
        self.page.get_by_label('Battle map').select_option(slug)
        expect(self.page.locator('#saved')).to_contain_text('Saved')
        self.assert_accessible('world map with a selected pin')

        pin = self.stored('world-maps')['maps'][0]['pins'][0]
        self.assertEqual((pin['label'], pin['map']), ('Lantern Quay', slug))
        self.page.get_by_role('link', name='Open battle map').click()
        expect(self.page).to_have_url(re.compile('#/maps/' + slug))

    def test_narrow_navigation_reaches_every_page(self):
        self.page.set_viewport_size(NARROW)
        self.open('#/')
        nav = self.page.locator('#side nav a')
        labels = [label.strip() for label in nav.all_inner_texts()]
        self.assertIn('Settings & Foundry', labels)
        for label in labels:
            with self.subTest(page=label):
                link = self.page.locator('#side nav a', has_text=label)
                link.scroll_into_view_if_needed()
                link.click()
                expect(link).to_have_class(re.compile(r'(^| )on( |$)'))
                expect(self.page.locator('#main h1').first).to_be_visible()
                expect(self.page.get_by_text('Something went wrong')).to_have_count(0)
                overflow = self.page.evaluate(
                    'document.documentElement.scrollWidth - document.documentElement.clientWidth'
                )
                self.assertLessEqual(overflow, 0, 'the page scrolls sideways')
                self.assert_accessible(label + ' at narrow width')


if __name__ == '__main__':
    unittest.main()
