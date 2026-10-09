"""HTTP route dispatch and typed error responses on a disposable campaign."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'DM'))

import campaign
import http_routes
import records
import schema
import shapes
import workflow


class RouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous = campaign.activate(campaign.Campaign(Path(temporary.name) / 'Studio'))
        self.addCleanup(campaign.activate, previous)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), http_routes.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, method='GET', body=None, headers=None):
        data = json.dumps(body or {}).encode('utf-8') if method != 'GET' else None
        request = urllib.request.Request(
            self.url + path,
            data=data,
            method=method,
            headers={
                **({'X-DM-Site': '1', 'Content-Type': 'application/json'} if data else {}),
                **(headers or {}),
            },
        )
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers

    def test_invalid_missing_and_busy_routes_keep_distinct_statuses(self):
        status, _, _ = self.request('/api/plan/INVALID')
        self.assertEqual(status, 400)
        status, _, _ = self.request('/api/doc/missing')
        self.assertEqual(status, 404)
        status, _, _ = self.request('/api/unknown')
        self.assertEqual(status, 404)
        with patch.object(http_routes, 'map_busy', return_value=True):
            status, _, _ = self.request('/api/maps/fixture/export', 'POST')
        self.assertEqual(status, 409)

    def test_context_preview_search_and_pins_match_exported_packs(self):
        settings = http_routes.config.settings()
        settings['context_budget_chars'] = 16_000
        self.assertEqual(self.request('/api/settings', 'POST', settings)[0], 200)
        self.assertEqual(
            self.request('/api/settings', 'POST', {**settings, 'context_budget_chars': 100})[0],
            400,
        )
        http_routes.commit_docs(
            'Synthetic codex',
            [
                (
                    'codex',
                    {
                        'entries': [
                            shapes.CODEX_ENTRY.new(
                                id='mira', name='Mira', type='npc', notes='Keeps the clue'
                            ),
                            shapes.CODEX_ENTRY.new(
                                id='orm', name='Orm', type='npc', notes='Knows the gate'
                            ),
                        ]
                    },
                )
            ],
        )
        http_routes.write_doc(
            'inbox',
            {
                'items': [
                    {'id': 'req-one', 'kind': 'other', 'text': 'Find the gate', 'status': 'new'}
                ]
            },
        )
        status, found, _ = self.request('/api/context/search?q=mir')
        self.assertEqual((status, [entry['id'] for entry in found]), (200, ['mira']))
        status, pack, _ = self.request('/api/requests/req-one/pack')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(pack['prompt'].split('REFERENCE DATA:\n', 1)[1])['codex'], [])
        self.assertEqual(pack['context_preview']['pinned'], [])
        self.assertEqual(
            self.request('/api/requests/req-one/context', 'POST', {'pins': ['mira']})[0],
            200,
        )
        _, pack, _ = self.request('/api/requests/req-one/pack')
        self.assertIn('Keeps the clue', pack['prompt'])
        self.assertEqual(
            json.loads(pack['prompt'].split('REFERENCE DATA:\n', 1)[1])['codex'][0]['id'],
            'mira',
        )
        self.assertEqual(pack['context_preview']['pinned'], ['mira'])
        self.assertEqual(pack['context_preview']['budget_chars'], 16_000)
        self.assertEqual(
            self.request('/api/requests/req-one/context', 'POST', {'pins': ['missing']})[0],
            400,
        )

        value = workflow.create('fixture', {'name': 'Fixture', 'prompt': 'A gate'})
        url = '/api/workflow/' + value['id']
        self.assertEqual(self.request(url + '/context', 'POST', {'pins': ['orm']})[0], 200)
        _, pack, _ = self.request(url + '/pack')
        self.assertIn('Knows the gate', pack['prompt'])
        self.assertEqual(pack['context_preview']['pinned'], ['orm'])
        self.assertLessEqual(
            pack['context_preview']['prompt_chars']
            + pack['context_preview']['schema_chars']
            + 2_048,
            pack['context_preview']['budget_chars'],
        )

    def test_forge_scripts_come_from_the_install_folder_not_the_campaign(self):
        with urllib.request.urlopen(
            self.url + '/forge-scripts/foundry-import-macro.js'
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('javascript', response.headers['Content-Type'])
            self.assertTrue(response.read())
        for bad in ('forge.py', '..%2Fcampaign.py', 'missing.js'):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.url + '/forge-scripts/' + bad)
            self.assertEqual(caught.exception.code, 404)

    def test_ai_settings_only_store_the_environment_variable_name(self):
        settings = {
            'ai': {
                'provider': 'openai',
                'model': 'gpt-4o-mini',
                'key_env': 'STUDIO_TEST_KEY',
                'api_key': 'never-store-this',
            }
        }
        status, saved, _ = self.request('/api/settings', 'POST', settings)
        self.assertEqual(status, 200)
        self.assertEqual(saved['settings']['ai']['provider'], 'openai')
        self.assertNotIn('never-store-this', Path(campaign.active().settings).read_text())
        with patch.dict('os.environ', {'STUDIO_TEST_KEY': 'fixture-secret'}):
            status, state, _ = self.request('/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(state['ai']['label'], 'OpenAI API')
        self.assertTrue(state['ai']['available'])
        self.assertIn('claude', state)
        status, saved, _ = self.request('/api/settings', 'POST', {'ai': {'provider': 'codex'}})
        self.assertEqual((status, saved['settings']['ai']['provider']), (200, 'codex'))
        self.assertEqual(self.request('/api/state')[1]['ai']['label'], 'Codex')
        for ai in ({'provider': 'other'}, {'provider': 'openai', 'model': ''}):
            self.assertEqual(self.request('/api/settings', 'POST', {'ai': ai})[0], 400)

    def test_state_names_the_uploads_folder_and_repeated_calls_keep_sys_path_stable(self):
        import sys

        status, state, _ = self.request('/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(
            state['uploads_dir'], campaign.active().relative(campaign.active().uploads)
        )
        before = list(sys.path)
        for _ in range(3):
            self.request('/api/state')
        self.assertEqual(sys.path, before)

    def test_state_lists_archived_preps_and_old_preps_default_to_active(self):
        for name, archived in (('s1', True), ('s2', False), ('s3', None)):
            doc = {'n': int(name[1:]), 'title': name} | (
                {} if archived is None else {'archived': archived}
            )
            status, _, _ = self.request('/api/doc/prep/' + name, 'PUT', doc)
            self.assertEqual(status, 200)
        _, state, _ = self.request('/api/state')
        self.assertEqual(state['prep'], ['s1', 's2', 's3'])
        self.assertEqual(state['prep_archived'], ['s1'])

    def test_cancel_route_distinguishes_bad_missing_and_finished_jobs(self):
        status, _, _ = self.request('/api/jobs/NOT-HEX/cancel', 'POST')
        self.assertEqual(status, 400)
        status, _, _ = self.request('/api/jobs/abc-123/cancel', 'POST')
        self.assertEqual(status, 404)
        job = http_routes.new_job('art', 'fixture', 'Queued', [])
        status, body, _ = self.request(f'/api/jobs/{job["id"]}/cancel', 'POST')
        self.assertEqual((status, body['status'], body['cancelled']), (200, 'failed', True))
        status, _, _ = self.request(f'/api/jobs/{job["id"]}/cancel', 'POST')
        self.assertEqual(status, 409)

    def test_stale_document_conflict_still_returns_latest_revision_and_document(self):
        path = '/api/records/codex/item?id=current'
        entry = shapes.CODEX_ENTRY.new(id='current', type='npc', name='Current')
        status, _, headers = self.request(path, 'PUT', entry, {'X-Rev': '0'})
        self.assertEqual(status, 200)
        status, _, headers = self.request(path)
        self.assertEqual(status, 200)
        old_revision = headers['X-Rev']
        self.assertEqual(
            self.request(
                path,
                'PUT',
                dict(entry, notes='New notes'),
                {'X-Rev': old_revision},
            )[0],
            200,
        )
        status, conflict, _ = self.request(
            path, 'PUT', dict(entry, notes='Stale notes'), {'X-Rev': old_revision}
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict['doc']['notes'], 'New notes')
        self.assertNotEqual(conflict['rev'], old_revision)

    def test_two_thousand_records_page_and_one_edit_stays_small(self):
        here = campaign.active()
        schema.write_marker(here.data, schema.CURRENT, 'Synthetic large codex')
        folder = Path(here.data) / 'codex'
        folder.mkdir(parents=True)
        for n in range(2000):
            entry = shapes.CODEX_ENTRY.new(
                id=f'entry-{n:04d}',
                type='npc' if n % 2 else 'place',
                name=f'Entry {n:04d}',
                notes=f'Lore for entry {n:04d}',
                tags=['harbour'] if n % 10 == 0 else [],
                **({'foundry': {'world_key': 'fixture'}} if n == 1999 else {}),
            )
            Path(records.record_path(here.data, 'codex', entry['id'])).write_text(
                json.dumps(entry), encoding='utf-8'
            )
        status, page, _ = self.request('/api/records/codex?limit=40&offset=40&type=npc')
        self.assertEqual((status, page['total'], len(page['items'])), (200, 1000, 40))
        self.assertEqual(page['items'][0]['id'], 'entry-0081')
        self.assertNotIn('notes', page['items'][0])
        status, filtered, _ = self.request('/api/records/codex?tag=harbour&q=entry%2001')
        self.assertEqual((status, filtered['total']), (200, 10))
        self.assertEqual(self.request('/api/records/codex?source=foundry')[1]['total'], 1)
        self.assertEqual(self.request('/api/doc/codex', 'PUT', {'entries': []})[0], 403)

        path = '/api/records/codex/item?id=entry-1999'
        status, entry, headers = self.request(path)
        self.assertEqual(status, 200)
        entry['notes'] = 'Edited one record'
        self.assertLess(len(json.dumps(entry).encode('utf-8')), 10_000)
        self.assertEqual(self.request(path, 'PUT', entry, {'X-Rev': headers['X-Rev']})[0], 200)
        self.assertEqual(
            records.read(here.data, 'codex', 'entry-1999')['notes'], 'Edited one record'
        )
        self.assertEqual(
            records.read(here.data, 'codex', 'entry-1998')['notes'], 'Lore for entry 1998'
        )
        self.assertEqual(len(list((Path(here.history) / 'codex' / 'entry-1999').glob('*.json'))), 1)
        self.assertFalse((Path(here.history) / 'codex' / 'entry-1998').exists())

    def test_document_save_refuses_application_owned_documents(self):
        here = campaign.active()
        owned = {
            'settings': Path(here.settings),
            'foundry-library': Path(here.data) / 'foundry-library.json',
            'workflows/fixture': Path(here.data) / 'workflows' / 'fixture.json',
            'jobs/fixture': Path(here.jobs) / 'fixture.json',
        }
        for name, path in owned.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"kept": true}', encoding='utf-8')
            with self.subTest(name=name):
                status, body, _ = self.request('/api/doc/' + name, 'PUT', {'replaced': True})
                self.assertEqual(status, 403)
                self.assertIn('managed by Campaign Studio', body['error'])
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'kept': True})
        # Names that only resemble an owned document remain ordinary campaign documents.
        for name in ('settings-notes', 'prep/jobs'):
            with self.subTest(name=name):
                self.assertEqual(self.request('/api/doc/' + name, 'PUT', {'entries': []})[0], 200)

    def test_macro_fallback_imports_codex_and_keeps_studio_edits(self):
        here = campaign.active()
        world = Path(here.data).parent / 'Foundry' / 'Data' / 'worlds' / 'fixture'
        world.mkdir(parents=True)
        (world / 'world.json').write_text(
            json.dumps({'id': 'fixture', 'title': 'Fixture', 'system': 'dnd5e'}),
            encoding='utf-8',
        )
        settings = http_routes.config.settings()
        settings['world_path'] = str(world)
        Path(here.settings).parent.mkdir(parents=True, exist_ok=True)
        Path(here.settings).write_text(json.dumps(settings), encoding='utf-8')
        snapshot = {
            'format': 'campaign-studio-foundry-library',
            'schema': 1,
            'world': {'id': 'fixture', 'title': 'Fixture', 'system': 'dnd5e'},
            'documents': {
                'actors': [{'id': 'a1', 'name': 'Mira', 'summary': 'Scout'}],
                'items': [],
                'scenes': [],
                'journals': [],
            },
        }
        status, report, _ = self.request('/api/foundry/library/import', 'POST', snapshot)
        self.assertEqual((status, report['added'], report['counts']['actors']), (200, 1, 1))
        codex = records.collection(here.data, 'codex')
        self.assertEqual(codex['entries'][0]['notes'], 'Scout')
        codex['entries'][0]['notes'] = 'Studio version'
        http_routes.commit_docs('Studio edit', [('codex', codex)])
        snapshot['documents']['actors'][0]['summary'] = 'Foundry version'
        status, report, _ = self.request('/api/foundry/library/import', 'POST', snapshot)
        self.assertEqual((status, report['kept']), (200, 1))
        self.assertEqual(
            records.collection(here.data, 'codex')['entries'][0]['notes'],
            'Studio version',
        )

        # Removing the world drops its entries and snapshot, unlinks other entries, and spares the folder.
        own = records.collection(here.data, 'codex')
        key = own['entries'][0]['foundry']['world_key']
        mine = shapes.CODEX_ENTRY.new(id='mine', type='npc', name='Mine', related=['fvtt-a1'])
        own['entries'].append(mine)
        http_routes.commit_docs('Studio edit', [('codex', own)])
        self.assertEqual(self.request('/api/foundry/world/remove', 'POST', {})[0], 400)
        status, result, _ = self.request('/api/foundry/world/remove', 'POST', {'world_key': key})
        self.assertEqual((status, result['removed'], result['snapshot']), (200, 1, True))
        left = records.collection(here.data, 'codex')['entries']
        self.assertEqual([(e['id'], e['related']) for e in left], [('mine', [])])
        self.assertFalse(Path(http_routes.doc_path('foundry-library')).exists())
        self.assertTrue((world / 'world.json').is_file())
        self.assertEqual(
            self.request('/api/foundry/world/remove', 'POST', {'world_key': key})[0], 404
        )


if __name__ == '__main__':
    unittest.main()
