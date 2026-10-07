"""Where a codex entry is used, and deleting it without dangling links."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'DM'))

import campaign
import campaign_core
import http_routes
import references


def sample():
    return {
        'codex': {
            'entries': [
                {'id': 'mira', 'name': 'Mira', 'related': ['bram', 'mira']},
                {'id': 'bram', 'name': 'Bram', 'related': ['mira']},
                {'id': 'sword', 'name': 'Sword', 'related': []},
            ]
        },
        'threads': {'threads': [{'id': 't1', 'title': 'Heist', 'pcs': ['mira', 'bram']}]},
        'art': {'items': [{'id': 'a1', 'title': 'Portrait', 'codex': 'mira'}]},
        'inbox': {'items': [{'id': 'r1', 'text': 'More detail', 'codex': 'mira'}]},
        'prep/s1': {'n': 1, 'scenes': [{'id': 'sc', 'title': 'Docks', 'npcs': ['mira', 'bram']}]},
        'mapkey/docks': {'areas': [{'n': 1, 'name': 'Quay', 'npcs': ['mira'], 'items': ['sword']}]},
    }


class ReferenceTests(unittest.TestCase):
    def test_scan_lists_each_use_but_not_the_entry_itself(self):
        uses = references.scan(sample(), 'mira')
        self.assertEqual(
            [u['doc'] for u in uses],
            ['codex', 'threads', 'art', 'inbox', 'prep/s1', 'mapkey/docks'],
        )
        self.assertEqual(
            references.scan(sample(), 'sword'),
            [{'doc': 'mapkey/docks', 'where': 'Map docks, area 1: Quay'}],
        )

    def test_remove_unlinks_everything_and_reports_changed_documents(self):
        docs = sample()
        changed = references.remove(docs, 'mira')
        self.assertEqual(changed[0], 'codex')
        self.assertEqual(set(changed), set(docs))
        self.assertEqual([e['id'] for e in docs['codex']['entries']], ['bram', 'sword'])
        self.assertEqual(references.scan(docs, 'mira'), [])
        self.assertEqual(docs['threads']['threads'][0]['pcs'], ['bram'])
        self.assertEqual(docs['art']['items'][0]['codex'], '')
        self.assertEqual(docs['mapkey/docks']['areas'][0]['items'], ['sword'])

    def test_remove_leaves_unrelated_documents_alone(self):
        docs = sample()
        self.assertEqual(references.remove(docs, 'sword'), ['codex', 'mapkey/docks'])

    def test_unknown_entry_and_malformed_documents(self):
        with self.assertRaises(KeyError):
            references.remove(sample(), 'nobody')
        docs = {'codex': {'entries': [{'id': 'x'}, 'junk']}, 'threads': {'threads': 'bad'}}
        self.assertEqual(references.remove(docs, 'x'), ['codex'])


class DeleteRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous = campaign.activate(campaign.Campaign(Path(temporary.name) / 'Studio'))
        self.addCleanup(campaign.activate, previous)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), http_routes.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def call(self, path, method='GET'):
        request = urllib.request.Request(
            self.url + path,
            data=b'{}' if method == 'POST' else None,
            method=method,
            headers={'X-DM-Site': '1', 'Content-Type': 'application/json'},
        )
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def test_delete_removes_the_entry_and_every_link_in_one_commit(self):
        with campaign_core.LOCK:
            for name, value in sample().items():
                if name != 'mapkey/docks':
                    campaign_core.write_doc(name, value)
        status, body = self.call('/api/codex/mira/uses')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['uses']), 5)
        status, body = self.call('/api/codex/mira/delete', 'POST')
        self.assertEqual(status, 200)
        self.assertEqual(body['changed'][0], 'codex')
        entries = campaign_core.read_json(campaign_core.doc_path('codex'))['entries']
        self.assertEqual([e['id'] for e in entries], ['bram', 'sword'])
        self.assertEqual(self.call('/api/codex/mira/uses')[0], 404)
        self.assertEqual(self.call('/api/codex/mira/delete', 'POST')[0], 404)
        threads = campaign_core.read_json(campaign_core.doc_path('threads'))
        self.assertEqual(threads['threads'][0]['pcs'], ['bram'])


class MapTrashTests(DeleteRouteTests):
    test_delete_removes_the_entry_and_every_link_in_one_commit = None

    def seed(self):
        here = campaign.active()
        folder = Path(here.map_folder('docks'))
        folder.mkdir(parents=True)
        (folder / 'key.json').write_text('{"areas": []}', encoding='utf-8')
        with campaign_core.LOCK:
            campaign_core.write_doc(
                'maps/index', {'items': [{'slug': 'docks', 'name': 'Nogratis Docks'}]}
            )
            campaign_core.write_doc('mapbrief/docks', {'name': 'Nogratis Docks'})
            campaign_core.write_doc(
                'prep/s1',
                {
                    'n': 1,
                    'scenes': [
                        {'id': 'a', 'map': 'Nogratis Docks'},
                        {'id': 'b', 'map': 'other'},
                    ],
                },
            )
            campaign_core.write_doc('inbox', {'items': [{'id': 'r', 'map': 'docks'}]})
        return folder

    def test_delete_leaves_no_dangling_name_and_restore_brings_it_back(self):
        folder = self.seed()
        status, body = self.call('/api/maps/docks/delete', 'POST')
        self.assertEqual(status, 200)
        self.assertFalse(folder.exists())
        index = campaign_core.read_json(campaign_core.doc_path('maps/index'))
        self.assertEqual(index['items'], [])
        prep = campaign_core.read_json(campaign_core.doc_path('prep/s1'))
        self.assertEqual([s['map'] for s in prep['scenes']], ['', 'other'])
        self.assertEqual(
            campaign_core.read_json(campaign_core.doc_path('inbox'))['items'][0]['map'], ''
        )
        self.assertEqual(self.call('/api/maps/docks/delete', 'POST')[0], 404)
        status, listing = self.call('/api/maps/trash')
        self.assertEqual([i['slug'] for i in listing['items']], ['docks'])
        status, _ = self.call(f'/api/maps/trash/{body["trash"]}/restore', 'POST')
        self.assertEqual(status, 200)
        self.assertTrue((folder / 'key.json').is_file())
        index = campaign_core.read_json(campaign_core.doc_path('maps/index'))
        self.assertEqual([m['slug'] for m in index['items']], ['docks'])
        prep = campaign_core.read_json(campaign_core.doc_path('prep/s1'))
        self.assertEqual([s['map'] for s in prep['scenes']], ['Nogratis Docks', 'other'])
        self.assertEqual(self.call('/api/maps/trash')[1]['items'], [])
        self.assertEqual(self.call(f'/api/maps/trash/{body["trash"]}/restore', 'POST')[0], 404)


if __name__ == '__main__':
    unittest.main()
