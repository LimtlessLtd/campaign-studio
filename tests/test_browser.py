"""Browser smoke and accessibility checks against a disposable synthetic campaign.

The checks drive Chromium through Playwright and inject axe-core from node_modules. Without either they
skip locally; CI sets CAMPAIGN_STUDIO_BROWSER_TESTS=required so a missing browser fails instead.
"""

import json
import os
import re
import sys
import time
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

sys.path.insert(0, str(ROOT / 'DM'))
import storage  # noqa: E402
import transcription  # noqa: E402

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


class StagingRunner:
    """Stands in for the transcription worker: it leaves the result a real engine would."""

    SEGMENTS = [
        {'start': 0, 'end': 4, 'text': 'The party reaches the gate.'},
        {'start': 4, 'end': 9, 'text': 'Mira says <b>open</b> it.'},
    ]

    def start(self, cmd, cwd, env, log, has_stdin):
        log.write('PROGRESS 100% done\n')
        log.flush()
        return self

    def feed(self, text):
        storage.atomic_json(
            json.loads(text)['out'], {'language': 'en', 'duration': 9, 'segments': self.SEGMENTS}
        )

    def wait(self):
        return 0

    def terminate(self):
        pass


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

    def test_a_recording_is_transcribed_read_and_removed_on_phone_and_desktop(self):
        recordings = self.studio.root / 'Session recordings'
        recordings.mkdir()
        recording = recordings / 'session-one.mp4'
        recording.write_bytes(b'not really a video')
        self.addCleanup(patch.stopall)
        patch.object(transcription.FasterWhisper, 'problem', return_value='').start()
        patch.object(campaign_core.JOBS_SERVICE, 'runner', StagingRunner()).start()

        self.page.set_viewport_size(NARROW)
        self.open('#/recordings')
        expect(self.page.get_by_text('session-one.mp4')).to_be_visible()
        self.assert_accessible('recordings phone')
        self.page.get_by_role('button', name='Transcribe session-one.mp4').click()
        expect(self.page.get_by_text('Transcribing session-one.mp4')).to_be_visible()
        job, cmd, stdin = campaign_core.LANES['transcribe'].get_nowait()
        campaign_core.execute_job(job, cmd, stdin)

        read = self.page.get_by_role('button', name='Read the transcript of session-one')
        expect(read).to_be_visible(timeout=15000)  # the page notices the finished job
        self.assertEqual(len(self.stored('transcripts/' + job['transcript'])['segments']), 2)
        read.click()
        expect(self.page.get_by_text('The party reaches the gate.')).to_be_visible()
        expect(self.page.get_by_text('Mira says <b>open</b> it.')).to_be_visible()  # text, not HTML
        self.assertEqual(self.page.locator('.transcript b').count(), 0)
        self.assert_accessible('transcript phone')
        self.page.set_viewport_size(DESKTOP)
        self.assert_accessible('transcript desktop')

        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.get_by_role('button', name='Remove the transcript of session-one').click()
        expect(self.page.get_by_text('No transcripts yet.')).to_be_visible()
        self.assertEqual(campaign_core.list_docs('transcripts'), [])
        self.assertTrue(recording.exists(), 'removing a transcript never touches the recording')

    def test_a_transcript_is_sorted_reviewed_and_its_banter_remembered_on_phone_and_desktop(self):
        import test_transcript_classifier as sorting

        ident = 'rec-0123456789abcdef'
        texts = list(sorting.SCRIPT)
        texts[1] = 'Mira says <b>open</b> the gate of the church of Auril.'
        campaign_core.write_doc('transcripts/' + ident, sorting.transcript(ident, texts))
        self.addCleanup(patch.stopall)
        patch.object(campaign_core.JOBS_SERVICE, 'runner', sorting.FakeClaude()).start()
        patch('ai_provider.command', return_value=['claude', '-p']).start()
        banter = '0:00:10–0:00:19'

        self.page.set_viewport_size(NARROW)
        self.open('#/recordings')
        expect(self.page.get_by_text('Not sorted yet')).to_be_visible()
        self.assert_accessible('sorting phone')
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.get_by_role('button', name='Sort play from banter: session-one').click()
        expect(self.page.get_by_text('Sorting started')).to_be_visible()
        job, cmd, stdin = campaign_core.LANES['claude'].get_nowait()
        campaign_core.execute_job(job, cmd, stdin)

        review = self.page.get_by_role('button', name='Review passages: session-one')
        expect(review).to_be_visible(timeout=15000)  # the page notices the finished job
        review.click()
        expect(
            self.page.get_by_text('Mira says <b>open</b> the gate')
        ).to_be_visible()  # text, not HTML
        self.assertEqual(self.page.locator('.passage-text b').count(), 0)
        self.assert_accessible('review phone')
        self.page.set_viewport_size(DESKTOP)
        self.assert_accessible('review desktop')

        note = self.page.get_by_label(f'Table lore note for {banter} (optional)')
        self.assertEqual(note.input_value(), sorting.HORSE)
        self.page.get_by_role('button', name=f'Table banter, {banter}').click()
        expect(
            self.page.get_by_role('button', name='Confirm the 2 proposed passages on this page')
        ).to_be_visible()
        self.page.get_by_role('button', name='Confirm the 2 proposed passages on this page').click()
        expect(
            self.page.get_by_text('1 need a decision · 0 match known table lore · 3 confirmed.')
        ).to_be_visible()
        passages = {p['id']: p for p in self.stored('transcripts/' + ident)['passages']}
        self.assertEqual(
            {i: (p['kind'], p['confirmed']) for i, p in passages.items()},
            {
                'p0': ('play', True),
                'p2': ('banter', True),
                'p4': ('unclear', False),
                'p5': ('play', True),
            },
        )
        self.assertEqual([i['text'] for i in self.stored('table-lore')['items']], [sorting.HORSE])

        self.page.get_by_role('button', name='Done reviewing').click()
        expect(self.page.get_by_text(sorting.HORSE, exact=True)).to_be_visible()
        self.assert_accessible('table lore desktop')
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.get_by_role('button', name=f'Remove table lore: {sorting.HORSE}').click()
        expect(self.page.get_by_text('No table lore yet.')).to_be_visible()
        self.assertEqual(self.stored('table-lore')['items'], [])

    def test_thread_ledger_reviews_evidence_and_applies_only_selected_changes_on_phone(self):
        import shapes
        import test_thread_ledger as ledger_fixture
        import test_transcript_classifier as sorting

        ident = ledger_fixture.IDENT
        document = ledger_fixture.recording()
        document['passages'][3].update(kind='banter', confirmed=True)
        campaign_core.write_doc('prep/s1', shapes.PREP.new(n=1, title='First session'))
        campaign_core.write_doc(
            'threads/church', shapes.THREAD.new(id='church', title='Church of Auril')
        )
        campaign_core.write_doc(
            'codex/auril-church',
            shapes.CODEX_ENTRY.new(id='auril-church', type='place', name='Church of Auril'),
        )
        campaign_core.write_doc(
            'codex/mira', shapes.CODEX_ENTRY.new(id='mira', type='pc', name='Mira')
        )
        campaign_core.write_doc('transcripts/' + ident, document)
        self.addCleanup(patch.stopall)
        patch('ai_provider.command', return_value=['claude', '-p']).start()
        runner = sorting.FakeClaude()
        patch.object(campaign_core.JOBS_SERVICE, 'runner', runner).start()

        self.page.set_viewport_size(NARROW)
        self.open('#/recordings')
        self.page.get_by_role('button', name='Open the thread ledger of Session one').click()
        expect(self.page.get_by_role('button', name='Draft thread ledger')).to_be_visible()
        self.assert_accessible('ledger start phone')
        with self.page.expect_response(lambda response: '/ledger/start' in response.url):
            self.page.get_by_role('button', name='Draft thread ledger').click()
        job, cmd, stdin = campaign_core.LANES['claude'].get_nowait()
        draft = ledger_fixture.proposal()
        draft['events'][1]['text'] = 'The smuggler <b>escaped</b> towards the harbour.'
        runner.reply = draft
        campaign_core.execute_job(job, cmd, stdin)
        self.assertEqual(self.stored('threads/church')['status'], 'open')

        self.page.reload()
        expect(self.page.locator('#main h1').first).to_be_visible()
        self.page.get_by_role('button', name='Open the thread ledger of Session one').click()
        expect(self.page.get_by_text('4 proposed changes.', exact=False)).to_be_visible()
        self.assertEqual(self.page.locator('.ledger-event b').count(), 0)
        self.assert_accessible('ledger review phone')
        self.page.set_viewport_size(DESKTOP)
        self.assert_accessible('ledger review desktop')
        self.page.once('dialog', lambda dialog: dialog.accept())
        with self.page.expect_response(lambda response: '/ledger/start' in response.url):
            self.page.get_by_role('button', name='Redraft ledger').click()
        job, cmd, stdin = campaign_core.LANES['claude'].get_nowait()
        campaign_core.execute_job(job, cmd, stdin)
        self.page.reload()
        expect(self.page.locator('#main h1').first).to_be_visible()
        self.page.get_by_role('button', name='Open the thread ledger of Session one').click()
        expect(self.page.get_by_text('4 proposed changes.', exact=False)).to_be_visible()
        self.page.locator('.ledger-event').filter(has_text='Codex note').get_by_role(
            'checkbox'
        ).uncheck()
        with self.page.expect_response(lambda response: '/ledger/apply' in response.url):
            self.page.get_by_role('button', name='Apply selected changes').click()
        self.assertEqual(self.stored('threads/church')['status'], 'resolved')
        self.assertEqual(self.stored('codex/auril-church')['notes'], '')
        self.assertEqual(len(self.stored('prep/s1')['log']['outcomes']), 1)
        self.assertEqual(len(self.stored('ledger/' + ident)['selected']), 3)

    def test_story_arcs_propose_review_apply_one_option_and_seed_the_next_pitch(self):
        import shapes
        import test_arc_options as arcs
        import test_transcript_classifier as sorting

        campaign_core.write_doc('prep/s1', shapes.PREP.new(n=1, title='First session'))
        campaign_core.write_doc(
            'threads/church', shapes.THREAD.new(id='church', title='Church of Auril')
        )
        campaign_core.write_doc(
            'threads/smuggler',
            shapes.THREAD.new(id='smuggler', title='The escaped smuggler', status='foreshadowed'),
        )
        for ident, kind, name in (
            ('mira', 'pc', 'Mira'),
            ('auril-church', 'place', 'Church of Auril'),
            ('harbour-master', 'npc', 'Harbour Master Brae'),
        ):
            campaign_core.write_doc(
                'codex/' + ident, shapes.CODEX_ENTRY.new(id=ident, type=kind, name=name)
            )
        self.addCleanup(patch.stopall)
        patch('ai_provider.command', return_value=['claude', '-p']).start()
        runner = sorting.FakeClaude()
        draft = arcs.proposal()
        draft['options'][0]['summary'] = 'The <b>cult</b> rebuilds the church.'
        runner.reply = draft
        patch.object(campaign_core.JOBS_SERVICE, 'runner', runner).start()

        self.page.set_viewport_size(NARROW)
        self.open('#/arcs')
        expect(self.page.get_by_role('button', name='Propose arcs')).to_be_disabled()
        self.page.get_by_label('Church of Auril').check()
        self.page.get_by_label('The escaped smuggler').check()
        expect(self.page.get_by_text('2 of 5 threads chosen.')).to_be_visible()
        self.assert_accessible('arcs start phone')
        with self.page.expect_response(lambda response: '/api/arcs/start' in response.url):
            self.page.get_by_role('button', name='Propose arcs').click()
        job, cmd, stdin = campaign_core.LANES['claude'].get_nowait()
        campaign_core.execute_job(job, cmd, stdin)
        self.assertEqual(self.stored('threads/church')['status'], 'open')

        self.page.reload()
        expect(self.page.locator('#main h1').first).to_be_visible()
        self.page.get_by_role('link', name='Review options').click()
        expect(self.page.get_by_role('button', name='Apply chosen options')).to_be_visible()
        self.assertEqual(self.page.locator('.arc-option b').count(), 0)  # model text stays text
        expect(self.page.locator('#arc-o2-summary')).to_have_value(
            'The <b>cult</b> rebuilds the church.'
        )
        self.assert_accessible('arcs review phone')
        self.page.set_viewport_size(DESKTOP)
        self.assert_accessible('arcs review desktop')

        self.page.get_by_role('button', name='Apply chosen options').click()
        expect(self.page.locator('#toast')).to_contain_text('at least one thread')
        self.page.get_by_label('Choose twist: A twist').check()
        self.page.locator('#arc-o2-pitch').fill('The cult trades the church for a harbour favour.')
        with self.page.expect_response(lambda response: '/apply' in response.url):
            self.page.get_by_role('button', name='Apply chosen options').click()
        expect(self.page.get_by_text('Applied 1 of 2 threads', exact=False)).to_be_visible()
        self.assertEqual(self.stored('threads/church')['status'], 'planned')
        self.assertIn('Arc plan (twist)', self.stored('threads/church')['detail'])
        self.assertEqual(self.stored('threads/smuggler')['status'], 'foreshadowed')

        self.open('#/prep/s1')
        self.page.get_by_role('button', name='Plan whole session').click()
        expect(self.page.get_by_text('Seeds from your story arcs')).to_be_visible()
        self.page.get_by_role(
            'button', name='Add to the pitch: The cult trades the church for a harbour favour.'
        ).click()
        pitch = self.page.locator('textarea[placeholder^="e.g. The party follows"]')
        expect(pitch).to_have_value('The cult trades the church for a harbour favour.')

    def test_the_automatic_run_is_planned_started_and_waits_for_the_gm_on_phone_and_desktop(self):
        import shapes
        import test_auto_run as runs

        recordings = self.studio.root / 'Session recordings'
        recordings.mkdir()
        (recordings / 'session &lt;i&gt;one.mp4').write_bytes(b'not really a video')
        campaign_core.write_doc('prep/s1', shapes.PREP.new(n=1, title='First session'))
        campaign_core.write_doc(
            'threads/church', shapes.THREAD.new(id='church', title='Church of Auril')
        )
        self.addCleanup(patch.stopall)
        patch.object(transcription.FasterWhisper, 'problem', return_value='').start()
        patch('ai_provider.command', return_value=['claude', '-p']).start()
        patch.object(campaign_core.JOBS_SERVICE, 'runner', runs.Everything()).start()

        self.page.set_viewport_size(NARROW)
        self.open('#/recordings')
        expect(self.page.get_by_role('heading', name='Automatic run')).to_be_visible()
        self.page.get_by_role('button', name='Plan a run').click()
        # The name would be markup if it were shown as HTML.
        file = self.page.get_by_role('checkbox', name='session &lt;i&gt;one.mp4')
        expect(file).to_be_checked()
        self.assertEqual(self.page.locator('#main i').count(), 0)
        expect(self.page.get_by_label('Session for this run')).to_have_value('s1')
        self.assert_accessible('automatic run plan phone')
        self.page.set_viewport_size(DESKTOP)
        self.assert_accessible('automatic run plan desktop')

        file.uncheck()
        expect(self.page.get_by_role('button', name='Start the automatic run')).to_be_disabled()
        file.check()
        with self.page.expect_response(lambda response: response.url.endswith('/api/auto-run')):
            self.page.get_by_role('button', name='Start the automatic run').click()
        expect(self.page.get_by_text('Run for session s1')).to_be_visible()
        self.assertEqual(campaign_core.LANES['transcribe'].qsize(), 1)
        self.assertEqual(self.stored('prep/s1')['log']['outcomes'], [])  # nothing changes yet

        while not (
            campaign_core.LANES['transcribe'].empty() and campaign_core.LANES['claude'].empty()
        ):
            for lane in ('transcribe', 'claude'):
                if not campaign_core.LANES[lane].empty():
                    job, cmd, stdin = campaign_core.LANES[lane].get_nowait()
                    campaign_core.execute_job(job, cmd, stdin)
        self.page.reload()
        expect(self.page.locator('#main h1').first).to_be_visible()
        expect(self.page.get_by_text('Waiting for you', exact=True).first).to_be_visible()
        expect(self.page.get_by_text('4 passages to decide').first).to_be_visible()
        expect(self.page.get_by_text('1 AI request', exact=False)).to_be_visible()
        self.assertEqual(self.page.locator('#main i').count(), 0)  # file names stay text
        self.assert_accessible('automatic run waiting desktop')
        self.page.set_viewport_size(NARROW)
        self.assert_accessible('automatic run waiting phone')
        self.assertEqual(self.stored('threads/church')['status'], 'open')

        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.get_by_role('button', name='End the run for session s1').click()
        expect(self.page.get_by_text('Ended', exact=True).first).to_be_visible()
        self.assertTrue(campaign_core.LANES['claude'].empty())

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

    def test_session_pitch_review_and_linked_prep_at_phone_width(self):
        map_slug, _ = self.studio.import_map()
        self.studio.seed(
            'codex',
            {
                'entries': [
                    fixtures.shapes.CODEX_ENTRY.new(
                        id='existing-npc', type='npc', name='Old Captain'
                    )
                ]
            },
        )
        self.studio.seed(
            'threads',
            {
                'threads': [
                    fixtures.shapes.THREAD.new(
                        id='old-thread', title='The bell', detail='A church bell fell.'
                    )
                ]
            },
        )
        campaign_core.write_doc('prep/s1', fixtures.shapes.PREP.new(n=1, title='Session 1'))
        self.open('#/prep/s1')
        self.page.get_by_role('button', name='Plan whole session').click()
        self.page.get_by_placeholder('e.g. The party follows the smuggler lead').fill(
            'Follow the smugglers into the flooded quay.'
        )
        self.page.get_by_label('Length (hours)').fill('5')
        self.page.get_by_role('button', name='Save for later').click()
        expect(self.page.locator('#saved')).to_contain_text('Saved')
        item = self.stored('inbox')['items'][0]
        self.assertEqual(item['kind'], 'session')
        self.assertEqual(item['settings']['hours'], 5)
        self.studio.request(
            '/api/requests/' + item['id'] + '/stage',
            {'draft': self.studio.session_proposal(map_slug)},
        )
        self.page.reload()
        self.page.set_viewport_size(NARROW)
        expect(self.page.get_by_text('Ready to review')).to_be_visible()
        expect(self.page.get_by_role('checkbox', name=re.compile('Flooded Quay'))).to_be_checked()
        self.assert_accessible('session proposal review at phone width')
        self.page.get_by_role('button', name='Apply to campaign').click()
        expect(self.page.get_by_text('Flooded Quay').first).to_be_visible()
        self.page.reload()
        expect(self.page.locator('.scene input[placeholder="Scene title"]').nth(1)).to_have_value(
            'At the quay'
        )
        self.assertEqual(self.stored('prep/s1')['scenes'][1]['npcs'], [item['id'] + '-watcher'])
        self.assert_accessible('linked session prep at phone width')
        slug = self.stored('inbox')['items'][0]['created_maps'][0]['slug']
        self.open('#/maps/' + slug)
        expect(self.page.get_by_role('heading', name='Ready for a layout proposal')).to_be_visible()

    def test_request_item_choices_clear_when_proposal_is_restaged(self):
        campaign_core.write_doc('prep/s1', fixtures.shapes.PREP.new(n=1, title='Session 1'))
        campaign_core.write_doc(
            'inbox',
            {
                'items': [
                    {
                        'id': 'req-review',
                        'kind': 'encounter',
                        'text': 'Create a harbour encounter.',
                        'session': 's1',
                        'status': 'new',
                    }
                ]
            },
        )
        self.studio.request(
            '/api/requests/req-review/stage',
            {'draft': self.studio.request_proposal()},
        )
        self.open('#/inbox')
        self.page.set_viewport_size(NARROW)
        watcher = self.page.get_by_role('checkbox', name=re.compile('Harbour Watcher'))
        expect(watcher).to_be_checked()
        self.assert_accessible('item review at phone width')
        watcher.uncheck()
        self.page.get_by_role('button', name='Edit proposal').click()
        self.page.get_by_role('button', name='Validate & review').click()
        expect(
            self.page.get_by_role('checkbox', name=re.compile('Harbour Watcher'))
        ).to_be_checked()
        self.page.get_by_role('checkbox', name=re.compile('Harbour Watcher')).uncheck()
        self.page.get_by_role('button', name='Apply to campaign').click()
        expect(self.page.get_by_role('button', name='New follow-up')).to_be_visible()
        self.assertEqual(self.stored('codex')['entries'], [])
        self.assertEqual(self.stored('prep/s1')['scenes'][0]['npcs'], [])

    def test_session_item_choices_apply_only_the_ticked_items(self):
        map_slug, item = self.studio.seed_session_request()
        self.studio.request(
            '/api/requests/' + item['id'] + '/stage',
            {'draft': self.studio.session_proposal(map_slug)},
        )
        self.open('#/inbox')
        self.page.set_viewport_size(NARROW)
        for label in (
            'Flooded Quay',
            'Harbour Watcher',
            'The smuggler network',
            'Update thread: The bell',
            'Harbour notice',
        ):
            expect(self.page.get_by_role('checkbox', name=re.compile(label))).to_be_checked()
        self.assert_accessible('session item review at phone width')
        self.page.get_by_role('checkbox', name=re.compile('Flooded Quay')).uncheck()
        self.page.get_by_role('checkbox', name=re.compile('Harbour Watcher')).uncheck()
        self.page.get_by_role('button', name='Apply to campaign').click()
        expect(self.page.get_by_role('button', name='New follow-up')).to_be_visible()
        self.assertEqual(self.stored('inbox')['items'][0]['created_maps'], [])
        self.assertEqual([row['id'] for row in self.stored('codex')['entries']], ['existing-npc'])
        prep = self.stored('prep/s1')
        quay = prep['scenes'][1]
        self.assertEqual((quay['title'], quay['map'], quay['npcs']), ('At the quay', '', []))
        self.assertEqual(len(prep['handouts']), 1)

    def test_a_job_log_that_scrolls_can_be_reached_with_the_keyboard(self):
        job = dict(
            id='29991230-000000-aaaa',
            lane='transcribe',
            kind='transcribe',
            status='failed',
            created=time.time(),
            label='Transcribe session-one',
        )
        campaign_core.JOBS_SERVICE.save_job(job)
        long_line = (
            'The transcription program reported a problem with the recording and kept going. '
        )
        with open(campaign_core.JOBS_SERVICE.job_file(job['id'], 'log'), 'w') as log:
            log.write(chr(10).join(f'{n}: {long_line}' for n in range(8)))
        self.page.set_viewport_size(NARROW)
        self.open('#/recordings')
        log = self.page.locator('pre.log')
        expect(log).to_be_visible()
        self.assertTrue(log.evaluate('el => el.scrollHeight > el.clientHeight'))
        self.assert_accessible('long job log at phone width')

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

    def test_thread_map_pin_and_clue_editing_with_hero_order(self):
        slug, _ = self.studio.import_map()
        self.studio.seed(
            'codex',
            {
                'entries': [
                    fixtures.shapes.CODEX_ENTRY.new(id='z', type='pc', name='Zara'),
                    fixtures.shapes.CODEX_ENTRY.new(id='m', type='pc', name='Mira'),
                ]
            },
        )
        self.studio.seed(
            'threads',
            {
                'threads': [
                    fixtures.shapes.THREAD.new(id='z-thread', title='A later hero', pcs=['z']),
                    fixtures.shapes.THREAD.new(id='m-thread', title='The clue', pcs=['m']),
                ]
            },
        )
        self.open('#/threads')
        self.page.get_by_label('Thread order').select_option('hero')
        card = self.page.locator('.card').first
        expect(card.get_by_label('Thread title')).to_have_value('The clue')
        card.locator('details.thread-links summary').click()
        card.get_by_placeholder('Link a map…').fill('Fixture map')
        card.get_by_role('button', name='Fixture map').click()
        card = self.page.locator('.card').first
        card.get_by_label('Choose a map for the pin').select_option(slug)
        expect(card.get_by_label('Choose a map pin')).to_be_enabled()
        card.get_by_label('Choose a map pin').select_option('1')
        card.get_by_role('button', name='+ Link pin').click()
        card = self.page.locator('.card').first
        card.get_by_role('button', name='+ Clue').click()
        card = self.page.locator('.card').first
        card.get_by_label('Clue 1 text').fill('The bell rings at dusk')
        card.get_by_label('Clue 1 location').fill('Landing')
        card.get_by_label('Clue 1 status').select_option('planted')
        expect(self.page.locator('#saved')).to_contain_text('Saved')
        thread = fixtures.records.read(self.studio.dm / 'data', 'threads', 'm-thread')
        self.assertEqual(thread['maps'], [slug])
        self.assertEqual([(link['map'], link['area']) for link in thread['locations']], [(slug, 1)])
        self.assertEqual(
            [(clue['text'], clue['where'], clue['status']) for clue in thread['clues']],
            [('The bell rings at dusk', 'Landing', 'planted')],
        )
        self.page.set_viewport_size(NARROW)
        self.assert_accessible('thread map pins and clues on phone')
        card.get_by_role('link', name='Fixture map · Landing').click()
        expect(self.page.get_by_label('Select map location')).to_have_value('1')

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
