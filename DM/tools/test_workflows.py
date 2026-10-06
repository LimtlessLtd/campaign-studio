"""Integration checks using an isolated campaign and fake Foundry Data directory.

python DM/tools/test_workflows.py
"""

import base64
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import campaign
import campaign_core
import foundry_backup
import foundry_upgrade
import http_routes
import workflow
import request_workflow
import revisions
import schema
import shapes
import maps_io
import forge
import image_worker


class Crash(BaseException):
    """Stands in for the server stopping: application code cannot catch it."""


class StudioIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='campaign-studio-test-')
        self.root = Path(self.temp.name)
        self.dm = self.root / 'DM'
        (self.dm / 'data').mkdir(parents=True)
        self.world = self.root / 'FoundryData' / 'worlds' / 'fixture-world'
        self.world.mkdir(parents=True)
        (self.world / 'world.json').write_text(
            json.dumps(
                {
                    'id': 'fixture-world',
                    'title': 'Fixture',
                    'system': 'dnd5e',
                    'coreVersion': '12.331',
                }
            )
        )
        settings = {
            **config.DEFAULTS,
            'campaign_name': 'Fixture campaign',
            'world_path': str(self.world),
        }
        (self.dm / 'data' / 'settings.json').write_text(json.dumps(settings))
        previous = campaign.activate(campaign.Campaign(self.dm))
        self.addCleanup(campaign.activate, previous)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), http_routes.Handler)
        self.http_thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.http_thread.start()
        self.url = 'http://127.0.0.1:' + str(self.http.server_address[1])
        picture = Image.new('RGB', (400, 300), '#597052')
        encoded = io.BytesIO()
        picture.save(encoded, 'PNG')
        self.png = encoded.getvalue()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        # TemporaryDirectory checks and owns its absolute test-only path.
        self.temp.cleanup()

    def request(
        self, path, value=None, method=None, mime='application/json', expected=200, writable=True
    ):
        data = (
            value
            if isinstance(value, bytes)
            else json.dumps(value).encode()
            if value is not None
            else None
        )
        headers = {'Content-Type': mime}
        if writable:
            headers['X-DM-Site'] = '1'
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            response = e
        self.assertEqual(
            response.status,
            expected,
            response.read().decode() if response.status != expected else '',
        )
        with response:
            return json.loads(response.read())

    def import_map(self):
        upload = self.request('/api/upload-image', self.png, mime='image/png')
        result = self.request(
            '/api/maps/import', {'name': 'Fixture map', 'cell': 100, 'image': upload['path']}
        )
        slug = result['slug']
        key = campaign_core.read_json(campaign_core.doc_path('mapkey/' + slug))
        key['areas'] = [
            shapes.AREA.new(
                n=1, name='Landing', kind='bridge', at=[1, 1], text='Established description'
            )
        ]
        campaign_core.write_doc('mapkey/' + slug, key)
        brief = campaign_core.read_json(campaign_core.doc_path('mapbrief/' + slug))
        brief['content'] = {
            'npcs': 1,
            'items': 1,
            'journals': 1,
            'events': 1,
            'threads': True,
            'art': True,
        }
        campaign_core.write_doc('mapbrief/' + slug, brief)
        return slug, brief

    def assert_shaped(self):
        """Every stored record the app wrote has each field of its shape in DM/shapes.py."""
        for path, shape in schema.shaped_documents(str(self.dm / 'data'), str(self.dm / 'maps')):
            if os.path.isfile(path):
                self.assertEqual(shape.problems(campaign_core.read_json(path)), [], path)

    def proposal(self):
        return {
            'summary': 'Four linked discoveries.',
            'areas': [{'n': 1, 'text': 'Additional scene detail', 'creatures': 'Two watchmen.'}],
            'npcs': [
                {
                    'id': 'watcher',
                    'name': 'The Watcher',
                    'area': 1,
                    'public': 'A quiet guard.',
                    'secrets': 'A hidden motive.',
                    'notes': 'AC 12; HP 10.',
                    'image_prompt': 'Guard portrait',
                }
            ],
            'items': [
                {
                    'id': 'key',
                    'name': 'Copper key',
                    'area': 1,
                    'public': 'A bent key.',
                    'secrets': '',
                    'notes': 'Opens a gate.',
                    'where': 'Under the bridge',
                    'value': '1 sp',
                    'image_prompt': 'Copper key illustration',
                }
            ],
            'journals': [
                {
                    'id': 'letter',
                    'title': 'A letter',
                    'area': 1,
                    'text': 'Meet at dusk.',
                    'secrets': 'A forgery.',
                    'image_prompt': 'Letter on parchment',
                }
            ],
            'events': [
                {
                    'id': 'patrol',
                    'title': 'Patrol',
                    'area': 1,
                    'trigger': 'After dusk',
                    'effect': 'The watcher returns.',
                    'image_prompt': 'Patrol at dusk',
                }
            ],
            'threads': [
                {
                    'id': 'gate',
                    'title': 'Beyond the gate',
                    'area': 1,
                    'detail': 'Investigate the locked gate.',
                    'status': 'foreshadowed',
                }
            ],
        }

    def test_import_draft_apply_export_and_restore(self):
        slug, brief = self.import_map()
        self.assertTrue((self.root / 'FoundryData/wotg-maps' / f'{slug}.png').exists())
        wf = self.request(f'/api/maps/{slug}/populate', {'run': False})['workflow']
        staged = self.request('/api/workflow/' + wf['id'] + '/stage', {'draft': self.proposal()})
        self.assertEqual(staged['status'], 'review')
        refined = self.request(
            '/api/workflow/' + wf['id'] + '/feedback',
            {'instruction': 'Keep the guard, change the letter.', 'run': False},
        )
        self.assertEqual(refined['workflow']['status'], 'ready')
        pack = self.request('/api/workflow/' + wf['id'] + '/pack')
        self.assertIn('previous_proposal', pack['prompt'])
        self.assertIn('change the letter', pack['prompt'])
        self.request('/api/workflow/' + wf['id'] + '/stage', {'draft': self.proposal()})
        applied = self.request('/api/workflow/' + wf['id'] + '/apply', {})
        self.assertEqual(applied['counts']['npcs'], 1)
        self.assert_shaped()
        key = campaign_core.read_json(campaign_core.doc_path('mapkey/' + slug))
        self.assertEqual(key['areas'][0]['text'], 'Established description')
        self.assertEqual(len(key['areas'][0]['journal']), 2)
        self.assertEqual(len(campaign_core.read_json(campaign_core.doc_path('art'))['items']), 4)
        self.assertEqual(
            campaign_core.read_json(campaign_core.doc_path('threads'))['threads'][0]['status'],
            'foreshadowed',
        )
        self.request('/api/workflow/' + wf['id'] + '/apply', {}, expected=400)
        self.request(f'/api/maps/{slug}/export', {})
        scene = json.loads((self.root / 'FoundryData/wotg-maps' / f'{slug}.json').read_text())
        exported = scene['flags']['world']['wotgForge']
        self.assertEqual(exported['targetWorld'], 'fixture-world')
        self.assertEqual(
            exported['key']['areas'][0]['npcs_detail'][0]['id'], key['areas'][0]['npcs'][0]
        )
        checkpoint = self.request(f'/api/maps/{slug}/checkpoint', {'label': 'Before annotation'})
        key['areas'][0]['name'] = 'Changed location'
        campaign_core.write_doc('mapkey/' + slug, key)
        revisions.restore(slug, checkpoint['id'], campaign_core.commit_docs)
        self.assertEqual(
            campaign_core.read_json(campaign_core.doc_path('mapkey/' + slug))['areas'][0]['name'],
            'Landing',
        )
        self.assertEqual(
            len(campaign_core.read_json(campaign_core.doc_path('codex'))['entries']), 2
        )

    def test_invalid_content_and_stale_drafts_are_refused(self):
        slug, brief = self.import_map()
        value = workflow.create(slug, brief)
        for mutate in (
            lambda d: d['npcs'][0].update(area=7),
            lambda d: d['npcs'].clear(),
            lambda d: d['items'][0].update(id='watcher'),
            lambda d: d['events'][0].update(area=True),
        ):
            draft = self.proposal()
            mutate(draft)
            with self.assertRaises(ValueError):
                workflow.stage(value, draft)
        workflow.stage(value, self.proposal())
        key = campaign_core.read_json(campaign_core.doc_path('mapkey/' + slug))
        key['areas'][0]['at'] = [0, 0]
        campaign_core.write_doc('mapkey/' + slug, key)
        self.request('/api/workflow/' + value['id'] + '/apply', {}, expected=400)
        self.assertFalse(os.path.isfile(campaign_core.doc_path('codex')))

    def crash_after(self, count):
        """Stop the next commit after count document writes, as a killed server would."""
        written = []
        original = campaign_core.JOURNAL.write

        def write(name, value):
            if len(written) == count:
                raise Crash
            original(name, value)
            written.append(name)
            if len(written) == count:
                raise Crash

        return patch.object(campaign_core.JOURNAL, 'write', write)

    def test_content_crash_at_each_write_boundary_recovers_without_dangling_links(self):
        for count in range(6):
            with self.subTest(after_writes=count):
                slug, brief = self.import_map()
                value = workflow.stage(workflow.create(slug, brief), self.proposal())
                with self.crash_after(count), self.assertRaises(Crash):
                    campaign_core.apply_content(value['id'])

                report = campaign_core.recover_commits()  # what the next server start does

                self.assertEqual(len(report['completed']), 1)
                self.assertEqual(workflow.get(value['id'])['status'], 'applied')
                codex = campaign_core.read_json(campaign_core.doc_path('codex'))['entries']
                threads = campaign_core.read_json(campaign_core.doc_path('threads'))['threads']
                art = campaign_core.read_json(campaign_core.doc_path('art'))['items']
                mine = lambda rows: [r for r in rows if r.get('workflow') == value['id']]
                self.assertEqual(len(mine(codex)), 2)
                self.assertEqual(len(mine(threads)), 1)
                self.assertEqual(len(mine(art)), 4)
                area = campaign_core.read_json(campaign_core.doc_path('mapkey/' + slug))['areas'][0]
                ids = {e['id'] for e in codex} | {t['id'] for t in threads}
                for link in area['npcs'] + area['items'] + area['threads']:
                    self.assertIn(link, ids)
                self.assertTrue(all(a['codex'] in ids for a in mine(art) if a['codex']))
                self.assertEqual(len(area['journal']), 2)
                index = campaign_core.read_json(campaign_core.doc_path('maps/index'))
                self.assertTrue(next(m for m in index['items'] if m['slug'] == slug)['stocked'])
                with self.assertRaises(ValueError):
                    workflow.apply_content(workflow.get(value['id']), campaign_core.commit_docs)

    def test_next_change_completes_an_interrupted_one_first(self):
        slug, brief = self.import_map()
        value = workflow.stage(workflow.create(slug, brief), self.proposal())
        codex_rev = campaign_core.rev_of(campaign_core.doc_path('codex'))
        with self.crash_after(0), self.assertRaises(Crash):
            workflow.apply_content(value, campaign_core.commit_docs)
        self.assertFalse(os.path.isfile(campaign_core.doc_path('codex')))
        # A browser tab holding the old codex revision must merge the completed change.
        conflict = urllib.request.Request(
            self.url + '/api/doc/codex',
            data=json.dumps({'entries': []}).encode(),
            headers={'Content-Type': 'application/json', 'X-DM-Site': '1', 'X-Rev': codex_rev},
            method='PUT',
        )
        with self.assertRaises(urllib.error.HTTPError) as stale:
            urllib.request.urlopen(conflict)
        self.assertEqual(stale.exception.code, 409)
        self.assertEqual(len(json.loads(stale.exception.read())['doc']['entries']), 2)
        self.assertEqual(workflow.get(value['id'])['status'], 'applied')
        self.request('/api/workflow/' + value['id'] + '/apply', {}, expected=400)

    def test_interrupted_change_with_an_edited_document_is_reported_not_overwritten(self):
        slug, brief = self.import_map()
        value = workflow.stage(workflow.create(slug, brief), self.proposal())
        with self.crash_after(1), self.assertRaises(Crash):
            workflow.apply_content(value, campaign_core.commit_docs)
        # Someone edits a pending document while the server is stopped.
        campaign_core.write_doc('threads', {'threads': [{'id': 'hand-edited'}]})

        report = campaign_core.recover_commits()

        self.assertEqual(report['completed'], [])
        self.assertEqual(
            campaign_core.read_json(campaign_core.doc_path('threads')),
            {'threads': [{'id': 'hand-edited'}]},
        )
        self.assertEqual(workflow.get(value['id'])['status'], 'review')
        changes = self.request('/api/state')['interrupted_changes']
        self.assertEqual(changes[0]['id'], report['conflicts'][0]['id'])
        states = {t['name']: t['state'] for t in changes[0]['targets']}
        self.assertEqual(states['codex'], 'written')
        self.assertEqual(states['threads'], 'changed')
        self.assertEqual(states['workflows/' + value['id']], 'pending')
        dismissed = self.request('/api/commits/' + changes[0]['id'] + '/dismiss', {})
        self.assertEqual(dismissed['interrupted_changes'], [])
        # The proposal is still awaiting review, and reapplying it does not duplicate the codex.
        self.request('/api/workflow/' + value['id'] + '/apply', {})
        codex = campaign_core.read_json(campaign_core.doc_path('codex'))['entries']
        self.assertEqual(len(codex), 2)

    def test_recovered_layout_still_queues_its_render(self):
        brief = campaign_core.normal_brief(
            {'name': 'Fixture yard', 'type': 'custom', 'width': 20, 'height': 20, 'seed': 3}
        )
        value = workflow.create('fixture-yard', brief, 'layout')
        draft = {
            'summary': 'An open yard.',
            'operations': [
                {'type': 'rect', 'row': 0, 'col': 0, 'width': 20, 'height': 20, 'fill': ','}
            ],
            'areas': [{'n': 1, 'name': 'Yard', 'kind': 'yard', 'at': [5, 5]}],
        }
        workflow.stage(value, draft)
        # The server stops after the plan, key and status are written, before the render is queued.
        with self.crash_after(3), self.assertRaises(Crash):
            campaign_core.apply_layout(value['id'])
        self.assertFalse(any(j.get('slug') == 'fixture-yard' for j in campaign_core.list_jobs()))

        campaign_core.recover_commits()

        renders = [j for j in campaign_core.list_jobs() if j.get('slug') == 'fixture-yard']
        self.assertEqual([(j['kind'], j['populate']) for j in renders], [('forge', True)])
        self.assertEqual(workflow.get(value['id'])['status'], 'applied')

    def test_job_failure_does_not_overwrite_records_that_moved_on(self):
        slug, brief = self.import_map()
        value = workflow.create(slug, brief)
        value.update(status='review', job='newer-job')
        workflow.save(value)
        campaign_core.write_doc(
            'art',
            {
                'items': [
                    {'id': 'art-ready', 'status': 'ready', 'image': 'one.png'},
                    {'id': 'art-busy', 'status': 'generating'},
                ]
            },
        )
        campaign_core.fail_job({'id': 'old-job', 'workflow': value['id']}, 'Late failure')
        campaign_core.fail_job({'id': 'img-1', 'art': 'art-ready'}, 'Late failure')
        campaign_core.recover_interrupted_job({'id': 'img-2', 'art': 'art-busy'})
        self.assertEqual(workflow.get(value['id'])['status'], 'review')
        art = campaign_core.read_json(campaign_core.doc_path('art'))['items']
        self.assertEqual([a['status'] for a in art], ['ready', 'failed'])

    def test_finished_job_keeps_its_result_when_recovery_must_wait(self):
        slug, _ = self.import_map()
        job = {'id': 'forge-1', 'kind': 'forge', 'slug': slug, 'populate': True, 'status': 'done'}
        with (
            patch.object(campaign_core, 'recover_commits', side_effect=TimeoutError('Lock wait')),
            patch.object(sys, 'stderr', io.StringIO()),
        ):
            campaign_core.finish_job(job, 0, '')
        self.assertEqual(job['status'], 'done')
        drafts = [json.loads(p.read_text()) for p in (self.dm / 'data/workflows').glob('*.json')]
        self.assertEqual([(w['map'], w['status']) for w in drafts], [(slug, 'ready')])

    def test_generated_image_links_its_codex_entry_and_location(self):
        slug, _ = self.import_map()
        campaign_core.write_doc('codex', {'entries': [{'id': 'npc-one', 'image': ''}]})
        campaign_core.write_doc(
            'art',
            {
                'items': [
                    {'id': 'art-one', 'map': slug, 'area': 1, 'codex': 'npc-one'},
                    {'id': 'art-two', 'map': slug, 'area': 1, 'codex': ''},
                ]
            },
        )
        done = {'kind': 'image', 'art': 'art-one', 'status': 'done'}
        campaign_core.finish_job(done, 0, '{"path": "one.png"}')
        failed = {'kind': 'image', 'art': 'art-two', 'status': 'done'}
        campaign_core.finish_job(failed, 1, 'Provider refused')
        art = campaign_core.read_json(campaign_core.doc_path('art'))['items']
        self.assertEqual((art[0]['status'], art[0]['image']), ('ready', 'one.png'))
        self.assertEqual((art[1]['status'], failed['status']), ('failed', 'failed'))
        codex = campaign_core.read_json(campaign_core.doc_path('codex'))['entries']
        self.assertEqual(codex[0]['image'], 'one.png')
        key = campaign_core.read_json(campaign_core.doc_path('mapkey/' + slug))
        self.assertEqual(key['areas'][0]['images'], ['one.png'])

    def request_proposal(self):
        return {
            'summary': 'A harbour watch encounter and a sealed notice.',
            'entries': [
                {
                    'id': 'watcher',
                    'type': 'npc',
                    'name': 'Harbour Watcher',
                    'public': 'A vigilant guard.',
                    'secrets': 'Works for a rival.',
                    'notes': 'AC 12; HP 10.',
                    'image_prompt': 'Guard portrait',
                }
            ],
            'threads': [
                {
                    'id': 'rival',
                    'title': 'The rival captain',
                    'detail': 'Find who commands the watcher.',
                    'status': 'open',
                }
            ],
            'scenes': [
                {
                    'id': 'gate',
                    'title': 'At the gate',
                    'where': 'Harbour gate',
                    'encounter': 'The watcher challenges the party.',
                    'notes': 'A cautious conversation.',
                    'npcs': ['watcher'],
                }
            ],
            'handouts': [
                {
                    'id': 'notice',
                    'title': 'Sealed notice',
                    'player_text': 'Report to the harbour gate.',
                    'secrets': 'The seal is forged.',
                }
            ],
            'goals': ['Learn who issued the notice.'],
            'loot': [{'item': 'Copper badge', 'where': 'Watch post', 'value': '2 sp'}],
            'checklist': ['Prepare the watch patrol.'],
            'notes': 'The rival captain is nearby.',
        }

    def test_general_request_review_apply_and_retry(self):
        prep = shapes.PREP.new(n=1, title='Session 1', notes='Existing notes.')
        campaign_core.write_doc('prep/s1', prep)
        item = {
            'id': 'req-one',
            'kind': 'encounter',
            'text': 'Create a harbour encounter.',
            'session': 's1',
            'status': 'new',
        }
        campaign_core.write_doc('inbox', {'items': [item]})
        pack = self.request('/api/requests/req-one/pack')
        self.assertIn('Create a harbour encounter.', pack['prompt'])
        self.assertIn('scenes', pack['schema']['properties'])
        self.request('/api/requests/req-one/stage', {'draft': self.request_proposal()})
        self.assertFalse(os.path.isfile(campaign_core.doc_path('codex')))
        box = campaign_core.read_json(campaign_core.doc_path('inbox'))
        # The server stops after the codex and threads are written, before prep and status.
        with self.crash_after(2), self.assertRaises(Crash):
            request_workflow.apply(
                box['items'][0], campaign_core.request_read, campaign_core.commit_docs, box
            )
        self.assertEqual(
            campaign_core.read_json(campaign_core.doc_path('inbox'))['items'][0]['status'],
            'review',
        )
        self.request('/api/requests/req-one/apply', {}, expected=409)
        applied = campaign_core.read_json(campaign_core.doc_path('inbox'))['items'][0]
        self.assertEqual(applied['status'], 'done')
        self.assertTrue(applied['applied'])
        box = campaign_core.read_json(campaign_core.doc_path('inbox'))
        box['items'][0]['status'] = 'new'
        campaign_core.write_doc('inbox', box)
        self.request(
            '/api/requests/req-one/stage', {'draft': self.request_proposal()}, expected=409
        )
        saved = campaign_core.read_json(campaign_core.doc_path('prep/s1'))
        self.assertEqual(saved['scenes'][0]['npcs'], ['req-one-watcher'])
        self.assertEqual(saved['handouts'][0]['player_text'], 'Report to the harbour gate.')
        self.assertEqual(saved['notes'], 'Existing notes.\n\nThe rival captain is nearby.')
        self.assertEqual(len(campaign_core.read_json(campaign_core.doc_path('art'))['items']), 1)
        self.assertEqual(
            len(campaign_core.read_json(campaign_core.doc_path('codex'))['entries']), 1
        )
        self.assertEqual(
            len(campaign_core.read_json(campaign_core.doc_path('prep/s1'))['goals']), 1
        )
        self.assert_shaped()

    def test_expand_entry_adds_text_links_and_art_once(self):
        campaign_core.write_doc(
            'codex',
            {
                'entries': [
                    shapes.CODEX_ENTRY.new(
                        id='gate', type='place', name='Harbour Gate', public='A gate.'
                    )
                ]
            },
        )
        item = {
            'id': 'req-expand',
            'kind': 'expand',
            'codex': 'gate',
            'text': 'Expand the gate.',
            'status': 'new',
        }
        campaign_core.write_doc('inbox', {'items': [item]})
        pack = self.request('/api/requests/req-expand/pack')
        self.assertIn('Harbour Gate', pack['prompt'])
        draft = {
            'summary': 'Gate lore.',
            'focus': {
                'public': 'Its lamps burn blue.',
                'secrets': 'A smuggler tunnel runs beneath.',
                'image_prompt': 'A lamplit harbour gate at dusk',
                'links': ['keeper'],
            },
            'entries': [
                {
                    'id': 'keeper',
                    'type': 'npc',
                    'name': 'Gate Keeper',
                    'public': 'Sour.',
                    'secrets': '',
                    'notes': '',
                    'image_prompt': '',
                }
            ],
            'threads': [],
            'scenes': [],
            'handouts': [],
            'goals': [],
            'loot': [],
            'checklist': [],
            'notes': '',
        }
        broken = {**draft, 'focus': {**draft['focus'], 'links': ['nobody']}}
        self.request('/api/requests/req-expand/stage', {'draft': broken}, expected=400)
        self.request('/api/requests/req-expand/stage', {'draft': draft})
        self.request('/api/requests/req-expand/apply', {})
        entries = {
            e['id']: e for e in campaign_core.read_json(campaign_core.doc_path('codex'))['entries']
        }
        self.assertEqual(entries['gate']['public'], 'A gate.\n\nIts lamps burn blue.')
        self.assertEqual(entries['gate']['related'], ['req-expand-keeper'])
        self.assertEqual(entries['req-expand-keeper']['related'], ['gate'])
        art = campaign_core.read_json(campaign_core.doc_path('art'))['items']
        self.assertEqual([(a['id'], a['codex']) for a in art], [('art-req-expand-focus', 'gate')])
        # A second pass over the same request, as after a crash, adds nothing further.
        codex = campaign_core.read_json(campaign_core.doc_path('codex'))
        request_workflow.expand_entry(codex, item, draft['focus'], 'req-expand-', {'keeper'})
        self.assertEqual(codex['entries'][0]['public'], 'A gate.\n\nIts lamps burn blue.')
        self.assert_shaped()

    def test_focus_content_needs_an_expand_request(self):
        campaign_core.write_doc(
            'inbox', {'items': [{'id': 'req-n', 'kind': 'npc', 'text': 'x', 'status': 'new'}]}
        )
        draft = self.request_proposal()
        draft['focus'] = {'public': 'x', 'secrets': '', 'image_prompt': '', 'links': []}
        self.request('/api/requests/req-n/stage', {'draft': draft}, expected=400)

    def test_expand_proposal_for_a_deleted_entry_is_rejected(self):
        campaign_core.write_doc('codex', {'entries': []})
        item = {'id': 'req-gone', 'kind': 'expand', 'codex': 'gate', 'text': 'x', 'status': 'new'}
        campaign_core.write_doc('inbox', {'items': [item]})
        draft = self.request_proposal()
        draft['entries'] = []
        draft['focus'] = {'public': '', 'secrets': '', 'image_prompt': 'A gate.', 'links': []}
        self.request('/api/requests/req-gone/stage', {'draft': draft}, expected=400)

    def test_general_request_validation_and_stale_source(self):
        campaign_core.write_doc('prep/s1', {'scenes': [], 'goals': []})
        item = {
            'id': 'req-two',
            'kind': 'npc',
            'text': 'Create a watch officer.',
            'session': 's1',
            'status': 'new',
        }
        campaign_core.write_doc('inbox', {'items': [item]})
        draft = self.request_proposal()
        draft['scenes'][0]['npcs'] = ['unknown']
        self.request('/api/requests/req-two/stage', {'draft': draft}, expected=400)
        self.assertFalse(os.path.isfile(campaign_core.doc_path('codex')))
        self.request('/api/requests/req-two/stage', {'draft': self.request_proposal()})
        box = campaign_core.read_json(campaign_core.doc_path('inbox'))
        box['items'][0]['text'] = 'Changed request.'
        campaign_core.write_doc('inbox', box)
        self.request('/api/requests/req-two/apply', {}, expected=400)
        self.assertFalse(os.path.isfile(campaign_core.doc_path('codex')))
        unsupported = dict(item, id='req-map', kind='battle map')
        campaign_core.write_doc('inbox', {'items': [unsupported]})
        self.request('/api/requests/req-map/pack', expected=403)

    def test_general_request_runner_uses_structured_output_without_tools(self):
        campaign_core.write_doc(
            'inbox',
            {
                'items': [
                    {
                        'id': 'req-run',
                        'kind': 'npc',
                        'text': 'Create a watch officer.',
                        'status': 'new',
                    }
                ]
            },
        )
        captured = {}

        def fake_job(lane, kind, label, cmd, prompt, **extra):
            captured.update(lane=lane, kind=kind, cmd=cmd, prompt=prompt)
            return {'id': 'job-test', 'request': extra['request'], 'source': extra['source']}

        with (
            patch.object(campaign_core.shutil, 'which', return_value='claude'),
            patch.object(campaign_core, 'new_job', side_effect=fake_job),
        ):
            job = self.request('/api/requests/req-run/run', {})
        self.assertEqual(captured['kind'], 'request-draft')
        self.assertEqual(captured['cmd'][captured['cmd'].index('--tools') + 1], '')
        self.assertIn('--restricted', captured['cmd'])
        self.assertIn('--strict-mcp-config', captured['cmd'])
        self.assertNotIn('--allowedTools', captured['cmd'])
        self.assertEqual(
            campaign_core.read_json(campaign_core.doc_path('inbox'))['items'][0]['status'],
            'doing',
        )
        log = Path(campaign_core.job_file(job['id'], 'log'))
        log.parent.mkdir(parents=True, exist_ok=True)
        draft = self.request_proposal()
        for field in ('scenes', 'handouts', 'goals', 'loot', 'checklist'):
            draft[field] = []
        draft['notes'] = ''
        log.write_text(json.dumps({'structured_output': draft}))
        campaign_core.finish_request(job, 0, '')
        saved = campaign_core.read_json(campaign_core.doc_path('inbox'))['items'][0]
        self.assertEqual(saved['status'], 'review')
        self.assertEqual(saved['draft']['entries'][0]['id'], 'watcher')

    def test_layout_operations_and_revision_recover_original_plan(self):
        brief = campaign_core.normal_brief(
            {
                'name': 'Fixture layout',
                'type': 'custom',
                'theme': 'outdoor',
                'width': 20,
                'height': 20,
                'seed': 10,
            }
        )
        brief['cell'] = 50
        value = workflow.create('fixture-layout', brief, 'layout')
        draft = {
            'summary': 'A furnished lodge.',
            'operations': [
                {'type': 'rect', 'row': 0, 'col': 0, 'width': 20, 'height': 20, 'fill': ','},
                {
                    'type': 'rect',
                    'row': 3,
                    'col': 3,
                    'width': 10,
                    'height': 8,
                    'fill': '.',
                    'border': '#',
                },
                {'type': 'stamp', 'row': 3, 'col': 6, 'rows': ['+']},
                {'type': 'stamp', 'row': 5, 'col': 5, 'rows': ['Tc', 'c ']},
                {
                    'type': 'scatter',
                    'row': 13,
                    'col': 0,
                    'width': 20,
                    'height': 7,
                    'char': '&',
                    'replace': ',',
                    'count': 12,
                },
                {'type': 'path', 'points': [[0, 7], [2, 7]], 'width': 1, 'char': ':'},
            ],
            'areas': [{'n': 1, 'name': 'Lodge', 'kind': 'house', 'at': [6, 6]}],
        }
        staged = workflow.stage(value, draft)
        self.assertTrue((self.root / staged['preview']).exists())
        folder = self.dm / 'maps/fixture-layout'
        original = staged['draft']['plan']
        campaign_core.write_doc('mapbrief/fixture-layout', brief)
        job = campaign_core.apply_layout(value['id'])
        self.assertEqual((job['kind'], job['populate']), ('forge', True))
        self.assertEqual((folder / 'plan.txt').read_text(), original)
        self.assertEqual(workflow.get(value['id'])['status'], 'applied')
        key = campaign_core.read_json(campaign_core.doc_path('mapkey/fixture-layout'))
        self.assertEqual(key['areas'][0]['name'], 'Lodge')
        self.assert_shaped()
        self.assertEqual(campaign_core.JOURNAL.entries(), [])
        render_log = io.StringIO()
        with contextlib.redirect_stdout(render_log):
            forge.forge(str(folder / 'plan.txt'), foundry_copy=False, jobs=1)
        self.assertIn('PROGRESS 100% complete', render_log.getvalue())
        self.assertIn('% painting', render_log.getvalue())
        self.assertTrue((folder / 'fixture-layout.webp').is_file())
        scene = json.loads((folder / 'fixture-layout.foundry.json').read_text())
        self.assertTrue(scene['walls'])
        self.assertEqual(scene['grid']['size'], 50)
        checkpoint = revisions.checkpoint('fixture-layout', 'Original')
        revision = workflow.create('fixture-layout', brief, 'revision', 'Add a well.')
        changed = workflow.stage(
            revision,
            {
                'summary': 'Added a well.',
                'operations': [{'type': 'stamp', 'row': 15, 'col': 15, 'rows': ['O']}],
                'areas': [],
            },
        )
        self.assertNotEqual(changed['draft']['plan'], original)
        (folder / 'plan.txt').write_text(changed['draft']['plan'])
        job = campaign_core.restore_revision('fixture-layout', checkpoint['id'])
        self.assertEqual((job['label'], job['populate']), ('Restore fixture-layout', False))
        self.assertEqual((folder / 'plan.txt').read_text(), original)
        for op in (
            {'type': 'rect', 'row': 19, 'col': 0, 'width': 5, 'height': 5, 'fill': '.'},
            {'type': 'stamp', 'row': 0, 'col': 0, 'rows': ['?']},
            {'type': 'path', 'width': 1, 'char': ':', 'points': [[0, 0], [3, 3]]},
        ):
            with self.assertRaises(ValueError):
                workflow.validate(revision, {'summary': 'Invalid', 'operations': [op], 'areas': []})

    def test_http_write_and_path_guards(self):
        self.assertEqual(self.request('/api/shapes', writable=False), shapes.describe())
        self.request('/api/maps/import', {}, expected=403, writable=False)
        self.request(
            '/api/maps/import', {'name': 'Outside', 'image': '../private.png'}, expected=400
        )
        self.request('/api/maps/no-such-map', {}, expected=400)
        self.request('/api/workflow/no-such-workflow', {}, expected=400)
        self.request(
            '/api/settings', {'images': {'endpoint': 'http://example.com/images'}}, expected=400
        )
        self.request('/api/upload-image', b'not a png', mime='image/png', expected=400)

    def test_foundry_backup_routes_use_synthetic_user_data(self):
        user_data = self.root / 'Foundry User Data'
        world = user_data / 'Data' / 'worlds' / 'fixture-world'
        world.mkdir(parents=True)
        (world / 'world.json').write_text((self.world / 'world.json').read_text())
        system = user_data / 'Data' / 'systems' / 'dnd5e'
        system.mkdir(parents=True)
        (system / 'system.json').write_text(json.dumps({'id': 'dnd5e', 'version': '3.0.0'}))
        plutonium = user_data / 'Data' / 'modules' / 'Plutonium'
        plutonium.mkdir(parents=True)
        (plutonium / 'module.json').write_text(json.dumps({'id': 'Plutonium', 'version': '1.0.0'}))
        (user_data / 'Data' / 'assets').mkdir()
        (user_data / 'Data' / 'assets' / 'fixture.png').write_bytes(self.png)
        self.request('/api/settings', {'world_path': str(world)})
        with patch.object(foundry_backup, 'running_foundry', return_value=[]):
            plan = self.request('/api/foundry/backup/plan')
            self.assertEqual(plan['world']['id'], 'fixture-world')
            self.request(
                '/api/foundry/backup/create',
                {'destination': str(self.root / 'copies'), 'confirmed_closed': True},
                expected=403,
                writable=False,
            )
            backup = self.request(
                '/api/foundry/backup/create',
                {'destination': str(self.root / 'copies'), 'confirmed_closed': True},
            )
            self.assertTrue(backup['verified'])
            self.assertTrue(
                self.request('/api/foundry/backup/verify', {'path': backup['path']})['verified']
            )
            restored = self.request(
                '/api/foundry/backup/rehearse',
                {'path': backup['path'], 'destination': str(self.root / 'restore-test')},
            )
            self.assertTrue(restored['verified'])
            self.assertEqual(Path(restored['world_path']).name, 'fixture-world')
            self.assertTrue(Path(restored['receipt_path']).is_file())
            inventory = {
                'format': foundry_upgrade.INVENTORY_FORMAT,
                'schema': 2,
                'world': {
                    'id': 'fixture-world',
                    'title': 'Fixture',
                    'system': 'dnd5e',
                    'coreVersion': '12.331',
                },
                'system': {'id': 'dnd5e', 'version': '3.0.0'},
                'enabledModuleIds': ['Plutonium'],
                'modules': [{'id': 'Plutonium', 'version': '1.0.0', 'enabled': True}],
            }
            compatibility = {'minimum': '13', 'maximum': '13', 'verified': '13'}
            selected = {
                'id': 'dnd5e',
                'version': '4.0.0',
                'manifest': 'https://example.org/dnd5e/system.json',
                'compatibility': compatibility,
                'manifest_compatibility': compatibility,
                'requires': [],
                'systems': [],
            }
            catalog = {
                'source': 'synthetic-integration',
                'builds': ['13.351', '12.331'],
                'packages': {
                    'dnd5e': {'status': 'listed', 'releases': [selected]},
                    'Plutonium': {'status': 'unlisted', 'releases': []},
                },
            }
            with patch.object(foundry_upgrade, 'collect_catalog', return_value=catalog):
                report = self.request(
                    '/api/foundry/upgrade/report',
                    {'inventory': inventory, 'backup_path': backup['path']},
                )
            self.assertEqual(report['recommended_build'], '13.351')
            payload = {
                'report_path': report['report_path'],
                'restore_receipt_path': restored['receipt_path'],
                'destination': str(self.root / 'upgrade-clone'),
            }
            self.request('/api/foundry/upgrade/prepare-clone', payload, expected=400)
            payload.update(confirmed_v12_restore=True, confirmed_report=True)
            prepared = self.request('/api/foundry/upgrade/prepare-clone', payload)
            self.assertEqual(prepared['status'], 'awaiting_v12_module_review')
            self.assertEqual(prepared['disable_in_v12'][0]['id'], 'Plutonium')
            self.assertTrue(Path(prepared['plan_path']).is_file())
            clone_inventory = json.loads(json.dumps(inventory))
            clone_inventory['enabledModuleIds'] = []
            clone_inventory['modules'][0]['enabled'] = False
            review_payload = {
                'plan_path': prepared['plan_path'],
                'inventory': clone_inventory,
                'confirmed_clone': True,
            }
            self.request(
                '/api/foundry/upgrade/review-clone', review_payload, expected=403, writable=False
            )
            review = self.request('/api/foundry/upgrade/review-clone', review_payload)
            self.assertEqual(review['status'], 'v12_modules_reviewed')
            self.assertFalse(review['migration_ready'])
            clone_world_path = Path(prepared['clone_path']) / 'Data/worlds/fixture-world/world.json'
            clone_world = json.loads(clone_world_path.read_text())
            clone_world.update(coreVersion='13.351', systemVersion='4.0.0')
            clone_world_path.write_text(json.dumps(clone_world))
            (Path(prepared['clone_path']) / 'Data/systems/dnd5e/system.json').write_text(
                json.dumps({'id': 'dnd5e', 'version': '4.0.0'})
            )
            migrated_inventory = json.loads(json.dumps(clone_inventory))
            migrated_inventory['phase'] = 'migrated-clone'
            migrated_inventory['world']['coreVersion'] = '13.351'
            migrated_inventory['system']['version'] = '4.0.0'
            self.request(
                '/api/foundry/upgrade/audit-migration',
                {
                    'review_path': review['review_path'],
                    'inventory': migrated_inventory,
                    'confirmed_clone': True,
                },
                expected=403,
                writable=False,
            )
            audit = self.request(
                '/api/foundry/upgrade/audit-migration',
                {
                    'review_path': review['review_path'],
                    'inventory': migrated_inventory,
                    'confirmed_clone': True,
                    'manual_checks': dict.fromkeys(
                        ('launch', 'scenes', 'journals', 'actors_items', 'modules'), True
                    ),
                },
            )
            self.assertEqual(audit['status'], 'reviewed')
            self.assertFalse(audit['cutover_ready'])
            cutover_payload = {'audit_path': audit['audit_path'], 'confirmed_closed': True}
            self.request(
                '/api/foundry/upgrade/review-cutover',
                cutover_payload,
                expected=403,
                writable=False,
            )
            self.request(
                '/api/foundry/upgrade/review-cutover',
                {'audit_path': audit['audit_path']},
                expected=400,
            )
            cutover = self.request('/api/foundry/upgrade/review-cutover', cutover_payload)
            self.assertEqual(cutover['status'], 'ready_for_manual_cutover')
            self.assertTrue(cutover['cutover_ready'])
            self.assertTrue(Path(cutover['review_path']).is_file())

    def test_first_run_world_picker_and_read_only_library(self):
        user_data = self.root / 'Foundry User Data'
        world = user_data / 'Data' / 'worlds' / 'fixture-world'
        world.mkdir(parents=True)
        (world / 'world.json').write_text((self.world / 'world.json').read_text())
        (world / 'maps').mkdir()
        (world / 'maps' / 'bridge.png').write_bytes(self.png)
        Path(campaign.active().settings).unlink()
        self.assertTrue(self.request('/api/state')['onboarding_needed'])
        found = self.request('/api/foundry/worlds?root=' + urllib.parse.quote(str(user_data)))
        self.assertEqual(found['worlds'][0]['title'], 'Fixture')
        self.request(
            '/api/settings', {'campaign_name': 'Fixture campaign', 'world_path': str(world)}
        )
        self.assertFalse(self.request('/api/state')['onboarding_needed'])
        media = self.request('/api/foundry/library?kind=assets')
        self.assertEqual(media['items'][0]['path'], 'worlds/fixture-world/maps/bridge.png')
        asset_url = (
            self.url + '/api/foundry/asset?path=' + urllib.parse.quote(media['items'][0]['path'])
        )
        with urllib.request.urlopen(asset_url) as response:
            self.assertEqual(response.read(), self.png)
        snapshot = {
            'format': 'campaign-studio-foundry-library',
            'schema': 1,
            'world': {'id': 'fixture-world', 'title': 'Fixture', 'system': 'dnd5e'},
            'exportedAt': '2026-10-03T12:00:00Z',
            'documents': {
                'scenes': [{'id': 'scene1', 'name': 'Bridge'}],
                'journals': [
                    {
                        'id': 'journal1',
                        'name': 'Legend',
                        'pages': [{'id': 'page1', 'name': 'Clue', 'text': 'Hidden door'}],
                    }
                ],
                'actors': [{'id': 'actor1', 'name': 'Scout', 'type': 'npc'}],
                'items': [],
            },
        }
        wrong = copy.deepcopy(snapshot)
        wrong['world']['id'] = 'another-world'
        self.request('/api/foundry/library/import', wrong, expected=400)
        self.request('/api/foundry/library/import', snapshot, expected=403, writable=False)
        imported = self.request('/api/foundry/library/import', snapshot)
        self.assertEqual(imported['counts']['journals'], 1)
        live = copy.deepcopy(snapshot)
        live['documents']['actors'][0]['summary'] = 'First live description'
        self.request('/api/foundry/library/live-import', wrong, expected=400)
        self.request('/api/foundry/library/live-import', live, expected=403, writable=False)
        live_report = self.request('/api/foundry/library/live-import', live)
        self.assertEqual(live_report['updated'], 1)
        self.assertEqual(
            self.request('/api/foundry/library?kind=actors')['snapshot']['source'], 'live'
        )
        codex = campaign_core.read_json(campaign_core.doc_path('codex'))
        actor = next(
            entry for entry in codex['entries'] if entry['foundry']['uuid'] == 'Actor.actor1'
        )
        actor['notes'] = 'My Studio edits'
        campaign_core.write_doc('codex', codex)
        live['documents']['actors'][0]['summary'] = 'Changed in Foundry'
        self.assertEqual(self.request('/api/foundry/library/live-import', live)['kept'], 1)
        codex = campaign_core.read_json(campaign_core.doc_path('codex'))
        actor = next(
            entry for entry in codex['entries'] if entry['foundry']['uuid'] == 'Actor.actor1'
        )
        self.assertEqual(actor['notes'], 'My Studio edits')
        journals = self.request('/api/foundry/library?kind=journals&q=legend')
        self.assertEqual(journals['items'][0]['pages'][0]['text'], 'Hidden door')
        self.assertFalse(journals['readable'])
        self.request('/api/foundry/library/read', {}, expected=400)
        (world / 'data').mkdir()
        (world / 'data' / 'actors.db').write_text(
            json.dumps({'_id': 'actor9', 'name': 'Warden', 'type': 'npc'}) + '\n', encoding='utf-8'
        )
        self.request('/api/foundry/library/read', {}, expected=403, writable=False)
        read = self.request('/api/foundry/library/read', {})
        self.assertEqual(read['counts'], {'scenes': 0, 'journals': 0, 'actors': 1, 'items': 0})
        actors = self.request('/api/foundry/library?kind=actors')
        self.assertEqual(actors['items'][0]['uuid'], 'Actor.actor9')
        self.assertEqual(
            (actors['snapshot']['source'], actors['snapshot']['stale']), ('folder', False)
        )

    def test_local_image_provider_contract(self):
        captured = []
        png = self.png

        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                body = json.dumps({'data': [{'b64_json': base64.b64encode(png).decode()}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        try:
            settings = config.settings()
            settings['images'].update(
                endpoint=f'http://127.0.0.1:{provider.server_address[1]}/images',
                model='fixture-model',
            )
            campaign_core.write_doc('settings', settings)
            campaign_core.write_doc(
                'art', {'items': [{'id': 'fixture-image', 'prompt': 'A guard portrait.'}]}
            )
            path = image_worker.generate('fixture-image')
            self.assertTrue((self.root / path).exists())
            self.assertEqual(captured[0]['model'], 'fixture-model')
            self.assertEqual(captured[0]['n'], 1)
        finally:
            provider.shutdown()
            provider.server_close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
