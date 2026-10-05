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


if __name__ == '__main__':
    unittest.main()
