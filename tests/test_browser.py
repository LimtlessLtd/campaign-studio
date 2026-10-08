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
from unittest.mock import patch

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
        return self.studio.stored(name)

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

    def test_memory_import_reviews_a_session_summary_on_phone_and_desktop(self):
        summary = self.studio.root / 's3.md'
        summary.write_text('# Third session\nThe party found the sealed gate.', encoding='utf-8')
        self.open('#/memory')
        self.page.get_by_role('combobox', name='Source type').select_option('summaries')
        self.page.get_by_label('Local source path').fill(str(summary))
        self.page.get_by_role('button', name='Preview import').click()
        expect(self.page.get_by_text('Third session')).to_be_visible()
        self.assertIsNone(self.stored('prep/s3'))
        self.assert_accessible('memory preview desktop')
        self.page.set_viewport_size(NARROW)
        self.assert_accessible('memory preview phone')
        self.page.get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Apply 1 selected').click()
        expect(self.page.get_by_text('Imported 1 new records')).to_be_visible()
        self.assertEqual(
            self.stored('prep/s3')['log']['summary'], 'The party found the sealed gate.'
        )

    def test_openai_settings_enable_structured_draft_controls(self):
        self.page.set_viewport_size(NARROW)
        with patch.dict(os.environ, {'STUDIO_TEST_KEY': 'synthetic-key'}):
            self.open('#/settings')
            self.page.get_by_label('Draft provider').select_option('openai')
            self.page.get_by_label('AI model').fill('gpt-4o-mini')
            self.page.get_by_label('OpenAI API key environment variable').fill('STUDIO_TEST_KEY')
            self.page.get_by_role('button', name='Save settings').click()
            expect(self.page.get_by_text('OpenAI API configured')).to_be_visible()
            self.assert_accessible('OpenAI provider settings')
            self.assertEqual(self.stored('settings')['ai']['key_env'], 'STUDIO_TEST_KEY')
            campaign_core.write_doc(
                'inbox',
                {
                    'items': [
                        {'id': 'req-provider', 'kind': 'npc', 'text': 'A guard', 'status': 'new'}
                    ]
                },
            )
            self.open('#/inbox')
            expect(self.page.get_by_role('button', name='Draft with OpenAI API')).to_be_visible()

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

        self.open('#/library')
        self.page.get_by_role('button', name='Actors & NPCs').click()
        picked = self.page.locator('.library-entry.on')
        expect(picked).to_have_count(0)
        self.page.locator('.library-entry', has_text='Mira').click()
        expect(picked).to_have_count(1)
        expect(picked).to_have_attribute('aria-pressed', 'true')
        self.page.reload()
        self.page.get_by_role('button', name='Actors & NPCs').click()
        expect(picked).to_contain_text('Mira')

        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.get_by_role('button', name='Remove imported data').click()
        expect(self.page.locator('.library-status')).to_contain_text('No documents read yet')
        self.assertIsNone(self.stored('foundry-library'))
        self.assertTrue((world / 'world.json').is_file())
        self.open('#/codex')
        self.open('#/library')
        expect(self.page.locator('.library-status')).to_contain_text('No documents read yet')
        expect(self.page.get_by_role('button', name='Remove imported data')).to_have_count(0)
        self.assertIsNone(self.stored('foundry-library'))

    def test_archiving_the_last_prep_keeps_new_session_number_unique(self):
        self.open('#/prep/new')
        expect(self.page).to_have_url(self.studio.url + '/#/prep/s1')
        expect(self.page.locator('#saved')).to_contain_text('Saved')

        self.page.get_by_role('button', name='Archive session').click()
        expect(self.page).to_have_url(self.studio.url + '/#/prep/s2')
        expect(self.page.locator('#main h1')).to_have_text('Session 2')
        expect(self.page.locator('#saved')).to_contain_text('Saved')
        self.assertTrue(self.stored('prep/s1')['archived'])
        self.assertFalse(self.stored('prep/s2')['archived'])

    def test_prep_add_buttons_clear_the_next_heading(self):
        self.open('#/prep/new')
        for viewport in (DESKTOP, NARROW):
            self.page.set_viewport_size(viewport)
            for button, heading in (
                ('+ Add scene', 'Handouts for this session'),
                ('+ Add handout', 'Loot for this session'),
                ('+ add', 'Checklist'),
            ):
                action = self.page.get_by_role('button', name=button, exact=True).bounding_box()
                section = self.page.get_by_role('heading', name=heading).bounding_box()
                self.assertIsNotNone(action)
                self.assertIsNotNone(section)
                gap = section['y'] - (action['y'] + action['height'])
                self.assertGreaterEqual(gap, 16, (viewport['width'], button, heading, gap))

    def test_codex_pages_and_thread_edits_save_individual_records(self):
        self.studio.seed(
            'codex',
            {
                'entries': [
                    fixtures.shapes.CODEX_ENTRY.new(
                        id=f'entry-{n:04d}', type='npc', name=f'Entry {n:04d}'
                    )
                    for n in range(45)
                ]
            },
        )
        self.studio.seed(
            'threads',
            {
                'threads': [
                    fixtures.shapes.THREAD.new(id='first', title='First thread'),
                    fixtures.shapes.THREAD.new(id='second', title='Second thread'),
                ]
            },
        )
        self.open('#/codex')
        expect(self.page.get_by_text('Entry 0000')).to_be_visible()
        expect(self.page.get_by_text('Entry 0044')).to_have_count(0)
        self.page.get_by_role('button', name='Next').click()
        self.page.get_by_text('Entry 0044').click()
        expect(self.page.get_by_role('heading', name='Entry 0044')).to_be_visible()
        self.page.get_by_label('Notes, voice, mannerisms, stats').fill('A new clue')
        expect(self.page.locator('#saved')).to_contain_text('Saved')
        self.assertEqual(self.studio.stored('codex')['entries'][-1]['notes'], 'A new clue')
        self.assertEqual(self.studio.stored('codex')['entries'][-2]['notes'], '')

        self.page.get_by_role('link', name='Story threads').click()
        expect(self.page.get_by_role('heading', name='Threads')).to_be_visible()
        self.page.locator('.card input[type="text"]').first.fill('Changed thread')
        expect(self.page.locator('#saved')).to_contain_text('Saved')
        self.assertEqual(
            fixtures.records.read(self.studio.dm / 'data', 'threads', 'first')['title'],
            'Changed thread',
        )
        self.assertEqual(
            fixtures.records.read(self.studio.dm / 'data', 'threads', 'second')['title'],
            'Second thread',
        )
        self.assert_accessible('per-record threads')

    def test_threads_link_entries_and_sort_by_staleness(self):
        self.studio.seed(
            'codex',
            {'entries': [fixtures.shapes.CODEX_ENTRY.new(id='mira', type='npc', name='Mira')]},
        )
        self.studio.seed(
            'threads',
            {
                'threads': [
                    fixtures.shapes.THREAD.new(id='fresh', title='Fresh lead', sessions=['s9']),
                    fixtures.shapes.THREAD.new(id='stale', title='Zed forgotten promise'),
                ]
            },
        )
        self.open('#/threads')
        expect(self.page.locator('.card input[type="text"]').first).to_have_value('Fresh lead')
        self.page.get_by_label('Thread order').select_option('stale')
        expect(self.page.locator('.card input[type="text"]').first).to_have_value(
            'Zed forgotten promise'
        )
        expect(self.page.get_by_text('Last touched in session 9')).to_be_visible()
        self.page.get_by_placeholder('Link a codex entry…').first.fill('Mira')
        self.page.get_by_role('button', name='Mira').first.click()
        expect(self.page.locator('#saved')).to_contain_text('Saved')
        stored = [
            fixtures.records.read(self.studio.dm / 'data', 'threads', key)
            for key in ('fresh', 'stale')
        ]
        self.assertEqual([t['entries'] for t in stored], [[], ['mira']])
        self.open('#/codex/mira')
        expect(self.page.get_by_role('link', name='Thread: Zed forgotten promise')).to_be_visible()
        self.assert_accessible('thread links')

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

    def test_cutover_review_shows_the_manual_switch_path(self):
        requests = []

        def cutover_response(route):
            requests.append(route.request.post_data_json)
            route.fulfill(
                status=200,
                content_type='application/json',
                body=json.dumps(
                    {
                        'target_build': '13.351',
                        'world_id': 'fixture-world',
                        'clone_path': '/synthetic/clone',
                        'original_user_data': '/synthetic/original',
                        'backup_path': '/synthetic/backup',
                        'review_path': '/synthetic/backup/cutover-review.json',
                    }
                ),
            )

        self.page.route('**/api/foundry/upgrade/review-cutover', cutover_response)
        self.open('#/settings')
        self.page.get_by_label('Passing migrated-clone audit').fill('/synthetic/backup/audit.json')
        self.page.get_by_label('I closed both Foundry installations').check()
        self.page.get_by_role('button', name='Review cutover readiness').click()
        expect(self.page.get_by_role('heading', name='Ready for manual cutover')).to_be_visible()
        expect(self.page.get_by_text('Use this User Data path: /synthetic/clone')).to_be_visible()
        self.assertEqual(
            requests, [{'audit_path': '/synthetic/backup/audit.json', 'confirmed_closed': True}]
        )

    def test_handouts_keep_nested_folder_names(self):
        for folder in ('set-a', 'set-b'):
            target = self.studio.dm / 'uploads' / folder
            target.mkdir(parents=True)
            (target / 'portrait.png').write_bytes(PNG)
        self.open('#/handouts')
        captions = self.page.locator('.gallery figcaption')
        expect(captions).to_have_count(2)
        self.assertCountEqual(
            captions.all_text_contents(), ['set-a/portrait.png', 'set-b/portrait.png']
        )

    def test_live_library_pairs_with_a_foundry_tab_and_refreshes(self):
        world = self.studio.root / 'User Data' / 'Data' / 'worlds' / 'fixture-world'
        world.mkdir(parents=True)
        (world / 'world.json').write_text((self.studio.world / 'world.json').read_text())
        campaign_core.write_doc('settings', {**self.stored('settings'), 'world_path': str(world)})
        snapshot = {
            'format': 'campaign-studio-foundry-library',
            'schema': 1,
            'world': {'id': 'fixture-world', 'title': 'Fixture', 'system': 'dnd5e'},
            'exportedAt': '2026-10-06T12:00:00Z',
            'documents': {
                'scenes': [],
                'journals': [],
                'actors': [{'id': 'actor1', 'name': 'Scout', 'type': 'npc', 'summary': 'First'}],
                'items': [],
            },
        }
        self.open('#/library')
        with self.page.expect_download() as saved_macro:
            self.page.get_by_role('button', name='Download live bridge macro').click()
        self.assertIn(
            'const CAMPAIGN_STUDIO_LIVE_ORIGIN =',
            Path(saved_macro.value.path()).read_text(encoding='utf-8'),
        )
        with self.page.expect_popup() as opened:
            self.page.evaluate("window.__livePopup = window.open('/#/library', '_blank')")
        live = opened.value
        live.on('pageerror', lambda error: self.errors.append(str(error)))
        expect(live.get_by_role('heading', name='Live Foundry connection')).to_be_visible()
        self.page.evaluate(
            """() => window.__livePopup.postMessage({
              studioLive: 1, type: 'hello', nonce: 'wrong-world',
              world: {id: 'other-world', title: 'Other'},
            }, location.origin)"""
        )
        expect(live.get_by_role('button', name='Connect and import')).to_be_disabled()
        self.page.evaluate(
            """(snapshot) => {
              window.__liveSnapshot = snapshot;
              window.addEventListener('message', (event) => {
                const request = event.data;
                if (request?.studioLive === 1 && request.type === 'request') {
                  window.__livePopup.postMessage({
                    studioLive: 1, type: 'snapshot', nonce: request.nonce,
                    requestId: request.requestId, snapshot: window.__liveSnapshot,
                  }, location.origin);
                }
              });
              window.__livePopup.postMessage({
                studioLive: 1, type: 'hello', nonce: 'fixture-nonce',
                world: {id: 'fixture-world', title: 'Fixture'},
              }, location.origin);
            }""",
            snapshot,
        )
        live.get_by_role('button', name='Connect and import').click()
        expect(live.get_by_role('button', name='Refresh from Foundry')).to_be_visible()
        self.assertEqual(self.stored('foundry-library')['source'], 'live')
        self.assertEqual(self.stored('codex')['entries'][0]['notes'], 'First')
        self.page.evaluate(
            """() => {
              window.__liveSnapshot.documents.actors[0].summary = 'Second';
              window.__livePopup.postMessage({
                studioLive: 1, type: 'changed', nonce: 'fixture-nonce',
              }, location.origin);
            }"""
        )
        expect(
            live.get_by_text('Foundry changed. Refresh to read its current documents.')
        ).to_be_visible()
        with live.expect_response('**/api/foundry/library/live-import') as refreshed:
            live.get_by_role('button', name='Refresh from Foundry').click()
        self.assertEqual(refreshed.value.status, 200)
        self.assertEqual(self.stored('codex')['entries'][0]['notes'], 'Second')

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

    def test_request_context_preview_can_pin_a_codex_entry(self):
        self.studio.seed(
            'codex',
            {'entries': [{'id': 'mira', 'name': 'Mira', 'type': 'npc', 'notes': 'Keeps the clue'}]},
        )
        campaign_core.write_doc(
            'inbox',
            {'items': [{'id': 'req-context', 'kind': 'other', 'text': 'A gate', 'status': 'new'}]},
        )
        self.open('#/inbox')
        self.page.get_by_role('button', name='Preview context').click()
        expect(self.page.get_by_role('heading', name='Draft context')).to_be_visible()
        self.assert_accessible('context preview')
        self.page.get_by_role('searchbox', name='Search codex entries to pin').fill('Mir')
        self.page.get_by_role('button', name='Pin Mira (npc)').click()
        self.page.get_by_role('button', name='Update preview').click()
        expect(self.page.get_by_text('Linked and pinned entries: 1')).to_be_visible()
        self.page.get_by_role('button', name='Save pinned entries').click()
        expect(self.page.get_by_role('heading', name='Draft context')).not_to_be_visible()
        self.assertEqual(self.stored('inbox')['items'][0]['context_pins'], ['mira'])

        campaign_core.delete_record('codex', 'mira')
        self.page.get_by_role('button', name='Preview context').click()
        expect(
            self.page.get_by_text('Previously pinned entries were deleted: mira.')
        ).to_be_visible()
        self.page.get_by_role('button', name='Save pinned entries').click()
        expect(self.page.get_by_role('heading', name='Draft context')).not_to_be_visible()
        self.assertEqual(self.stored('inbox')['items'][0]['context_pins'], [])

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

        self.page.get_by_role('button', name='Rename map').click()
        self.page.get_by_label('Name', exact=True).fill('The Wide Realm')
        self.page.get_by_role('button', name='Rename', exact=True).click()
        expect(self.page.get_by_role('heading', name='The Wide Realm', level=1)).to_be_visible()
        stored = self.stored('world-maps')['maps'][0]
        self.assertEqual((stored['name'], stored['pins'][0]['map']), ('The Wide Realm', slug))
        self.page.get_by_role('link', name='Open battle map').click()
        expect(self.page).to_have_url(re.compile('#/maps/' + slug))

        self.page.goto(self.studio.url + '/#/world')
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.get_by_role('button', name='Remove world map').click()
        expect(self.page.get_by_role('button', name='Add a world map')).to_be_visible()
        self.assertEqual(self.stored('world-maps')['maps'], [])
        self.page.goto(self.studio.url + '/#/maps/' + slug)
        expect(self.page.locator('body')).not_to_contain_text('not found')

    def test_phone_can_place_and_adjust_pins_without_dragging(self):
        slug, _brief = self.studio.import_map()
        phone = browser.new_context(viewport=NARROW, is_mobile=True, has_touch=True)
        self.addCleanup(phone.close)
        page = phone.new_page()
        page.on('pageerror', lambda error: self.errors.append(str(error)))
        page.goto(self.studio.url + '/#/world')
        page.get_by_role('button', name='Add a world map').tap()
        page.get_by_label('Name', exact=True).fill('Phone realm')
        page.locator('dialog input[type=file]').set_input_files(
            {'name': 'realm.png', 'mimeType': 'image/png', 'buffer': PNG}
        )
        expect(page.get_by_text('Image uploaded.')).to_be_visible()
        page.get_by_role('button', name='Add world map').tap()

        page.get_by_role('button', name='Add pin').tap()
        expect(page.get_by_text('Tap the map to place the pin.')).to_be_visible()
        page.locator('.map-stage img').tap(position={'x': 50, 'y': 50})
        expect(page.get_by_role('button', name='Pin 1: unnamed')).to_be_visible()
        page.get_by_label('Place name').fill('Quay')
        page.get_by_label('Battle map').select_option(slug)
        page.get_by_role('button', name='Move pin', exact=True).tap()
        page.get_by_role('button', name='Place at center').tap()
        page.get_by_role('button', name='Move pin right one percent').tap()
        expect(page.locator('#saved')).to_contain_text('Saved')
        pin = self.stored('world-maps')['maps'][0]['pins'][0]
        self.assertAlmostEqual(pin['x'], 0.51)
        self.assertAlmostEqual(pin['y'], 0.5)

        for control in [
            page.get_by_role('button', name='Pin 1: Quay'),
            page.get_by_role('button', name='Move pin right one percent'),
            page.locator('#side nav a[data-nav="maps"]'),
        ]:
            box = control.bounding_box()
            self.assertGreaterEqual(box['width'], 44, control.inner_text())
            self.assertGreaterEqual(box['height'], 44, control.inner_text())
        overflow = page.evaluate(
            'document.documentElement.scrollWidth - document.documentElement.clientWidth'
        )
        self.assertLessEqual(overflow, 0)

        page.goto(self.studio.url + '/#/maps/' + slug)
        page.get_by_role('button', name='Add location').tap()
        page.locator('.map-stage img').tap(position={'x': 35, 'y': 35})
        page.get_by_label('Location name').fill('Harbor')
        page.get_by_role('button', name='Add location').last.tap()
        expect(page.locator('#saved')).to_contain_text('Saved')
        self.assertIn('Harbor', [area['name'] for area in self.stored('mapkey/' + slug)['areas']])

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
