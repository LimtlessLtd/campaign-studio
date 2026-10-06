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
        status, _, headers = self.request('/api/doc/codex', 'PUT', {'entries': []})
        self.assertEqual(status, 200)
        status, _, headers = self.request('/api/doc/codex')
        self.assertEqual(status, 200)
        old_revision = headers['X-Rev']
        self.assertEqual(
            self.request(
                '/api/doc/codex',
                'PUT',
                {'entries': [{'id': 'current'}]},
                {'X-Rev': old_revision},
            )[0],
            200,
        )
        status, conflict, _ = self.request(
            '/api/doc/codex', 'PUT', {'entries': []}, {'X-Rev': old_revision}
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict['doc'], {'entries': [{'id': 'current'}]})
        self.assertNotEqual(conflict['rev'], old_revision)

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
        codex = http_routes.read_json(http_routes.doc_path('codex'))
        self.assertEqual(codex['entries'][0]['notes'], 'Scout')
        codex['entries'][0]['notes'] = 'Studio version'
        http_routes.write_doc('codex', codex)
        snapshot['documents']['actors'][0]['summary'] = 'Foundry version'
        status, report, _ = self.request('/api/foundry/library/import', 'POST', snapshot)
        self.assertEqual((status, report['kept']), (200, 1))
        self.assertEqual(
            http_routes.read_json(http_routes.doc_path('codex'))['entries'][0]['notes'],
            'Studio version',
        )


if __name__ == '__main__':
    unittest.main()
