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
import map_trash
import records
import references
import shapes


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


class StalenessTests(unittest.TestCase):
    def test_hero_names_prefer_codex_player_characters_over_the_public_site(self):
        with tempfile.TemporaryDirectory() as folder:
            for entry in (
                shapes.CODEX_ENTRY.new(id='m', type='pc', name='Mira Vale'),
                shapes.CODEX_ENTRY.new(id='guard', type='npc', name='Captain Hale'),
            ):
                path = Path(records.record_path(folder, 'codex', entry['id']))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(entry), encoding='utf-8')
            names = records.hero_names(
                folder, [{'id': 'm', 'name': 'Mira'}, {'id': 'z'}, 'bad', {'name': 'No ID'}]
            )
        self.assertEqual(names, {'m': 'Mira Vale', 'z': 'z'})

    def test_threads_sort_by_display_name_of_first_linked_hero(self):
        with tempfile.TemporaryDirectory() as folder:
            for key, pcs in (
                ('unclaimed', []),
                ('zara', ['z']),
                ('mira', ['m']),
                ('team', ['z', 'm']),
            ):
                path = Path(records.record_path(folder, 'threads', key))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(shapes.THREAD.new(id=key, title=key, pcs=pcs)),
                    encoding='utf-8',
                )
            page = records.page(
                folder, 'threads', sort='hero', hero_names={'m': 'Mira', 'z': 'Zara'}
            )
            self.assertEqual(
                [item['record']['id'] for item in page['items']],
                ['mira', 'team', 'zara', 'unclaimed'],
            )

    def test_threads_sort_by_the_session_they_last_touched(self):
        with tempfile.TemporaryDirectory() as folder:
            for key, title, sessions in (
                ('fresh', 'Fresh', ['s2', 's10']),
                ('old', 'Old', ['s1', 's2']),
                ('never', 'Never', []),
                ('odd', 'Odd', ['notes', 5]),
            ):
                path = records.record_path(folder, 'threads', key)
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(
                    json.dumps(shapes.THREAD.new(id=key, title=title, sessions=sessions)),
                    encoding='utf-8',
                )
            page = records.page(folder, 'threads', sort='stale')
            ids = [row['record']['id'] for row in page['items']]
            self.assertEqual(ids, ['never', 'odd', 'old', 'fresh'])
            self.assertEqual(records.last_session({'sessions': ['s2', 's10']}), 10)
            plain = records.page(folder, 'threads')
            self.assertEqual(
                [row['record']['id'] for row in plain['items']], ['fresh', 'never', 'odd', 'old']
            )


class ReferenceTests(unittest.TestCase):
    def test_map_links_unlink_and_restore_thread_maps_and_pins(self):
        docs = sample()
        thread = docs['threads']['threads'][0]
        thread['maps'] = ['docks', 'other']
        thread['locations'] = [
            shapes.THREAD_LOCATION.new(id='pin-1', map='docks', area=1),
            shapes.THREAD_LOCATION.new(id='pin-2', map='other', area=2),
        ]
        uses = references.map_uses(docs, 'docks', 'Docks')
        self.assertEqual(len([use for use in uses if use['doc'] == 'threads']), 2)

        changed, links = references.unlink_map(docs, 'docks', 'Docks')

        self.assertIn('threads', changed)
        self.assertEqual(thread['maps'], ['other'])
        self.assertEqual([location['id'] for location in thread['locations']], ['pin-2'])
        self.assertEqual(references.relink_map(docs, links).count('threads'), 1)
        self.assertEqual(thread['maps'], ['other', 'docks'])
        self.assertEqual({location['id'] for location in thread['locations']}, {'pin-1', 'pin-2'})
        self.assertEqual(references.relink_map(docs, links), [])

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

    def test_thread_entry_links_are_listed_and_unlinked_on_delete(self):
        docs = sample()
        docs['threads']['threads'][0]['entries'] = ['sword']
        self.assertEqual(
            references.scan(docs, 'sword')[0], {'doc': 'threads', 'where': 'Thread: Heist'}
        )
        references.remove(docs, 'sword')
        self.assertEqual(docs['threads']['threads'][0]['entries'], [])

    def test_remove_leaves_unrelated_documents_alone(self):
        docs = sample()
        self.assertEqual(references.remove(docs, 'sword'), ['codex', 'mapkey/docks'])

    def test_remove_many_unlinks_two_entries_without_touching_other_links(self):
        docs = sample()
        changed = references.remove_many(docs, ['mira', 'bram', 'missing'])
        self.assertEqual(changed[0], 'codex')
        self.assertEqual([entry['id'] for entry in docs['codex']['entries']], ['sword'])
        self.assertEqual(docs['threads']['threads'][0]['pcs'], [])
        self.assertEqual(docs['prep/s1']['scenes'][0]['npcs'], [])
        self.assertEqual(docs['mapkey/docks']['areas'][0]['items'], ['sword'])
        self.assertEqual(references.remove_many(docs, ['mira', 'bram']), [])

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

    def call(self, path, method='GET', body=None):
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body or {}).encode() if method == 'POST' else None,
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
                    if name in records.FIELDS:
                        for row in value[records.FIELDS[name]]:
                            campaign_core.write_doc(records.document_name(name, row['id']), row)
                    else:
                        campaign_core.write_doc(name, value)
        status, body = self.call('/api/codex/mira/uses')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['uses']), 5)
        self.assertEqual(self.call('/api/records/codex/item?id=mira', 'DELETE')[0], 400)
        stale_rev = body['rev']
        entry = records.read(campaign.active().data, 'codex', 'mira')
        entry['notes'] = 'A newer edit'
        campaign_core.write_doc('codex/mira', entry)
        self.assertEqual(self.call('/api/codex/mira/delete', 'POST', {'rev': stale_rev})[0], 409)
        body = self.call('/api/codex/mira/uses')[1]
        status, body = self.call('/api/codex/mira/delete', 'POST', {'rev': body['rev']})
        self.assertEqual(status, 200)
        self.assertIn('codex/mira', body['changed'])
        entries = records.all_records(campaign.active().data, 'codex')
        self.assertEqual([e['id'] for e in entries], ['bram', 'sword'])
        self.assertEqual(self.call('/api/codex/mira/uses')[0], 404)
        self.assertEqual(self.call('/api/codex/mira/delete', 'POST')[0], 404)
        self.assertEqual(records.read(campaign.active().data, 'threads', 't1')['pcs'], ['bram'])


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
            campaign_core.write_doc(
                'threads/heist',
                shapes.THREAD.new(
                    id='heist',
                    title='The dock heist',
                    maps=['docks'],
                    locations=[shapes.THREAD_LOCATION.new(id='quay', map='docks', area=1)],
                ),
            )
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
        thread = records.read(campaign.active().data, 'threads', 'heist')
        self.assertEqual((thread['maps'], thread['locations']), ([], []))
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
        thread = records.read(campaign.active().data, 'threads', 'heist')
        self.assertEqual(thread['maps'], ['docks'])
        self.assertEqual(thread['locations'], [{'id': 'quay', 'map': 'docks', 'area': 1}])
        self.assertEqual(self.call('/api/maps/trash')[1]['items'], [])
        self.assertEqual(self.call(f'/api/maps/trash/{body["trash"]}/restore', 'POST')[0], 404)

    def test_discard_removes_a_trashed_map_for_good(self):
        self.seed()
        _, body = self.call('/api/maps/docks/delete', 'POST')
        self.assertEqual(self.call(f'/api/maps/trash/{body["trash"]}/discard', 'POST')[0], 200)
        self.assertEqual(self.call('/api/maps/trash')[1]['items'], [])
        self.assertEqual(self.call(f'/api/maps/trash/{body["trash"]}/discard', 'POST')[0], 404)
        self.assertEqual(self.call(f'/api/maps/trash/{body["trash"]}/restore', 'POST')[0], 404)
        self.assertEqual(self.call('/api/maps/trash/..%2Fx/discard', 'POST')[0], 404)

    def test_interrupted_delete_restores_the_folder_when_the_catalogue_is_unchanged(self):
        folder = self.seed()
        here = campaign.active()
        index = campaign_core.read_json(campaign_core.doc_path('maps/index'))
        trash_id = map_trash.put(here, 'docks', index['items'][0], {}, [])
        self.assertFalse(folder.exists())

        campaign_core.recover_commits()

        self.assertTrue((folder / 'key.json').is_file())
        self.assertFalse(Path(here.map_trash, trash_id).exists())

    def test_interrupted_restore_returns_the_folder_to_trash(self):
        folder = self.seed()
        _, body = self.call('/api/maps/docks/delete', 'POST')
        here = campaign.active()
        map_trash.restore(here, body['trash'], 'docks')
        self.assertTrue(folder.exists())

        campaign_core.recover_commits()

        self.assertFalse(folder.exists())
        self.assertEqual(len(map_trash.listing(here)), 1)
        self.assertEqual(self.call(f'/api/maps/trash/{body["trash"]}/restore', 'POST')[0], 200)


if __name__ == '__main__':
    unittest.main()
