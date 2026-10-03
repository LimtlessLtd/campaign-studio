"""Integration checks using an isolated campaign and fake Foundry Data directory.

python DM/tools/test_workflows.py
"""

import base64
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
import urllib.request
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import campaign_core
import foundry_backup
import http_routes
import workflow
import request_workflow
import revisions
import maps_io
import forge
import image_worker


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
        self.patches = []
        settings = {
            **config.DEFAULTS,
            'campaign_name': 'Fixture campaign',
            'world_path': str(self.world),
        }
        (self.dm / 'data' / 'settings.json').write_text(json.dumps(settings))
        assignments = {
            campaign_core: {
                'HERE': str(self.dm),
                'CAMPAIGN': str(self.root),
                'DATA': str(self.dm / 'data'),
                'MAPS': str(self.dm / 'maps'),
                'HISTORY': str(self.dm / 'data/.history'),
                'JOBS': str(self.dm / 'data/jobs'),
                'UPLOADS': str(self.dm / 'uploads'),
            },
            http_routes: {
                'HERE': str(self.dm),
                'DATA': str(self.dm / 'data'),
                'MAPS': str(self.dm / 'maps'),
                'UPLOADS': str(self.dm / 'uploads'),
            },
            config: {'HOME': str(self.dm), 'CONFIG_PATH': str(self.dm / 'data/settings.json')},
            workflow: {
                'HERE': str(self.dm),
                'DATA': str(self.dm / 'data'),
                'MAPS': str(self.dm / 'maps'),
            },
            revisions: {'ROOT': str(self.dm), 'MAPS': str(self.dm / 'maps')},
            maps_io: {'ROOT': str(self.dm)},
            forge: {'DM': str(self.dm), 'FOUNDRY_DATA': str(self.root / 'FoundryData')},
        }
        for module, values in assignments.items():
            for key, value in values.items():
                p = patch.object(module, key, value)
                p.start()
                self.patches.append(p)
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
        for p in reversed(self.patches):
            p.stop()
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
            {
                'n': 1,
                'name': 'Landing',
                'kind': 'bridge',
                'at': [1, 1],
                'text': 'Established description',
                'creatures': '',
                'journal': [],
                'events': [],
                'npcs': [],
                'items': [],
                'images': [],
            }
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
        revisions.restore(slug, checkpoint['id'], campaign_core.write_doc)
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

    def test_partial_content_application_can_retry_without_duplicates(self):
        slug, brief = self.import_map()
        value = workflow.stage(workflow.create(slug, brief), self.proposal())

        def failing_save(name, doc):
            if name.startswith('mapkey/'):
                raise OSError('Simulated interrupted write')
            return campaign_core.write_doc(name, doc)

        with self.assertRaises(OSError):
            workflow.apply_content(value, failing_save)
        workflow.apply_content(value, campaign_core.write_doc)
        self.assertEqual(
            len(campaign_core.read_json(campaign_core.doc_path('codex'))['entries']), 2
        )
        self.assertEqual(
            len(campaign_core.read_json(campaign_core.doc_path('threads'))['threads']), 1
        )
        self.assertEqual(len(campaign_core.read_json(campaign_core.doc_path('art'))['items']), 4)

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
        prep = {
            'title': 'Session 1',
            'scenes': [],
            'handouts': [],
            'goals': [],
            'loot': [],
            'checklist': [],
            'notes': 'Existing notes.',
        }
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
        staged = campaign_core.read_json(campaign_core.doc_path('inbox'))['items'][0]

        def interrupted_save(name, doc):
            if name == 'prep/s1':
                raise OSError('Simulated interrupted prep write')
            return campaign_core.write_doc(name, doc)

        with self.assertRaises(OSError):
            request_workflow.apply(staged, campaign_core.request_read, interrupted_save)
        applied = self.request('/api/requests/req-one/apply', {})
        self.assertEqual(applied['status'], 'done')
        self.assertTrue(applied['applied'])
        self.request('/api/requests/req-one/apply', {}, expected=409)
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
        (folder / 'plan.txt').write_text(original)
        campaign_core.write_doc('mapkey/fixture-layout', {'areas': draft['areas']})
        campaign_core.write_doc('mapbrief/fixture-layout', brief)
        forge.forge(str(folder / 'plan.txt'), foundry_copy=False, jobs=1)
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
        revisions.restore('fixture-layout', checkpoint['id'], campaign_core.write_doc)
        self.assertEqual((folder / 'plan.txt').read_text(), original)
        for op in (
            {'type': 'rect', 'row': 19, 'col': 0, 'width': 5, 'height': 5, 'fill': '.'},
            {'type': 'stamp', 'row': 0, 'col': 0, 'rows': ['?']},
            {'type': 'path', 'width': 1, 'char': ':', 'points': [[0, 0], [3, 3]]},
        ):
            with self.assertRaises(ValueError):
                workflow.validate(revision, {'summary': 'Invalid', 'operations': [op], 'areas': []})

    def test_http_write_and_path_guards(self):
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
