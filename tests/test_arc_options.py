"""Arc and resolution options for loose threads: bounded proposals, a review gate and one recoverable apply."""

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
sys.path.insert(0, str(ROOT / 'tests'))
import arc_options
import campaign
import campaign_core as core
import http_routes
import records
import shapes
from test_transcript_classifier import FakeClaude

LEDGER = 'rec-0123456789abcdef'


def option(thread, kind, **changes):
    return {
        'thread': thread,
        'kind': kind,
        'title': f'A {kind}',
        'summary': f'What the {kind} means for {thread}.',
        'hook': f'How the party meets the {kind}.',
        'pitch': f'The {kind} of {thread} reaches the table.',
        'entries': [],
        'pcs': [],
    } | changes


def proposal():
    return {
        'options': [
            option('church', 'twist'),
            option(
                'church',
                'resolution',
                entries=['auril-church', 'nobody', 'auril-church'],
                pcs=['mira', 'ghost', 'auril-church'],
            ),
            option('smuggler', 'escalation', entries=['harbour-master']),
            option('stranger', 'twist'),  # a thread nobody asked about
        ]
    }


class OptionUnitTests(unittest.TestCase):
    ENTRIES = {'auril-church', 'harbour-master', 'mira'}
    HEROES = {'mira'}

    def validate(self, draft=None, chosen=('church', 'smuggler')):
        draft = proposal() if draft is None else draft
        return arc_options.validate(draft, list(chosen), self.ENTRIES, self.HEROES)

    def test_options_come_back_in_thread_then_kind_order_with_only_known_links(self):
        options = self.validate()
        self.assertEqual(
            [(o['id'], o['thread'], o['kind']) for o in options],
            [
                ('o1', 'church', 'resolution'),
                ('o2', 'church', 'twist'),
                ('o3', 'smuggler', 'escalation'),
            ],
        )
        self.assertEqual(options[0]['entries'], ['auril-church'])  # an invented ID and a repeat go
        self.assertEqual(options[0]['pcs'], ['mira'])  # a non-hero is not a hero
        self.assertEqual(options[2]['entries'], ['harbour-master'])
        self.assertEqual(shapes.ARC_OPTION.problems(options[0]), [])

    def test_a_thread_must_keep_an_option_and_unusable_ones_are_dropped(self):
        draft = proposal()
        draft['options'] += [
            option('church', 'resolution', title='A second resolution'),  # kind already used
            option('smuggler', 'twist', pitch='   '),  # no pitch
            option('smuggler', 'resolution', summary=''),  # no summary
        ]
        self.assertEqual(
            [o['kind'] for o in self.validate(draft)], ['resolution', 'twist', 'escalation']
        )
        self.assertEqual(self.validate(draft)[0]['title'], 'A resolution')

        only_church = {'options': [option('church', 'twist')]}
        with self.assertRaisesRegex(ValueError, 'skipped a thread'):
            self.validate(only_church)
        for bad in (
            {},
            {'options': 'none'},
            {'options': [option('church', 'plot')]},
            {'options': [{'thread': 'church'}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.validate(bad)

    def test_text_is_flattened_and_bounded(self):
        draft = {'options': [option('church', 'twist', title='  Two\n lines ', summary='x' * 5000)]}
        [made] = self.validate(draft, chosen=['church'])
        self.assertEqual(made['title'], 'Two lines')
        self.assertEqual(len(made['summary']), arc_options.TEXT_LIMITS['summary'])

    def arc(self):
        options = self.validate()
        return shapes.ARC.new(
            id='arc-0123abcd', status='review', threads=['church', 'smuggler'], options=options
        )

    def test_the_gm_chooses_one_option_per_thread_and_may_reword_it(self):
        arc = self.arc()
        picked = arc_options.chosen_options(
            arc, [{'option': 'o2', 'title': 'My turn', 'hook': ''}, {'option': 'o3'}]
        )
        self.assertEqual((picked[0]['title'], picked[0]['hook']), ('My turn', ''))
        self.assertEqual(picked[0]['summary'], arc['options'][1]['summary'])
        self.assertEqual(
            arc['options'][1]['title'], 'A twist'
        )  # the stored proposal is not edited here
        for bad, message in (
            ([], 'Choose between'),
            ([{'option': 'o9'}], 'from this proposal'),
            ('o1', 'Choose between|Choose between 1'),
            ([{'option': 'o1'}, {'option': 'o2'}], 'at most one'),
            ([{'option': 'o1', 'title': ' '}], 'title, a summary and a pitch'),
            ([{'option': 'o1', 'summary': 3}], 'must be text'),
            (['o1'], 'from this proposal'),
            ([{'option': 'o1'}] * 6, 'Choose between'),
        ):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                arc_options.chosen_options(arc, bad)

    def test_a_chosen_option_plans_its_thread_once_and_keeps_what_was_there(self):
        arc = self.arc()
        picked = arc_options.chosen_options(arc, [{'option': 'o1'}])
        thread = shapes.THREAD.new(
            id='church',
            title='Church of Auril',
            detail='Burned down.',
            pcs=['ulrick'],
            entries=['zed'],
        )
        [(name, planned)] = arc_options.changes(
            picked, lambda key: dict(thread, pcs=list(thread['pcs']))
        )
        self.assertEqual(name, 'threads/church')
        self.assertEqual(planned['status'], 'planned')
        self.assertTrue(
            planned['detail'].startswith('Burned down.\n\nArc plan (resolution): A resolution.')
        )
        self.assertIn('Hook: How the party meets the resolution.', planned['detail'])
        self.assertEqual(
            (planned['pcs'], planned['entries']), (['ulrick', 'mira'], ['zed', 'auril-church'])
        )

        again = arc_options.changes(picked, lambda key: dict(planned))
        self.assertEqual(again[0][1]['detail'], planned['detail'])  # the plan is not written twice
        with self.assertRaisesRegex(ValueError, 'removed'):
            arc_options.changes(picked, lambda key: None)

    def test_seeds_are_chosen_pitches_of_unresolved_threads_newest_first(self):
        def applied(ident, when, chosen):
            made = self.arc()
            made.update(id=ident, status='applied', applied=when, choices=chosen)
            return made

        old, new = (
            applied('arc-00000001', 100, ['o1', 'o3']),
            applied('arc-00000002', 200, ['o2', 'o3']),
        )
        review = self.arc()  # not applied: never a seed
        threads = [
            shapes.THREAD.new(id='church', title='Church', status='planned'),
            shapes.THREAD.new(id='smuggler', title='Smuggler', status='open'),
        ]
        lines = arc_options.seeds([old, review, new], threads)
        self.assertEqual(
            [(x['arc'], x['kind']) for x in lines],
            [
                ('arc-00000002', 'twist'),
                ('arc-00000002', 'escalation'),
                ('arc-00000001', 'resolution'),
            ],
        )
        threads[1]['status'] = 'resolved'
        self.assertEqual(
            [x['thread'] for x in arc_options.seeds([old, new], threads)], ['church', 'church']
        )
        self.assertEqual(arc_options.seeds([old], []), [])


class ArcCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous = campaign.activate(campaign.Campaign(self.root / 'Studio'))
        self.addCleanup(campaign.activate, previous)
        self.addCleanup(patch.stopall)
        self.claude = FakeClaude()
        self.claude.reply = proposal()
        patch.object(core.JOBS_SERVICE, 'runner', self.claude).start()
        patch('ai_provider.command', return_value=['claude', '-p']).start()
        self.addCleanup(self.drain)
        core.write_doc('prep/s1', shapes.PREP.new(n=1, title='Session one'))
        core.write_doc(
            'threads/church',
            shapes.THREAD.new(
                id='church',
                title='Church of Auril',
                detail='The church was blown up by the party.',
                pcs=['mira'],
                entries=['auril-church'],
            ),
        )
        core.write_doc(
            'threads/smuggler',
            shapes.THREAD.new(id='smuggler', title='The escaped smuggler', status='foreshadowed'),
        )
        core.write_doc(
            'threads/old', shapes.THREAD.new(id='old', title='An old matter', status='resolved')
        )
        for ident, kind, name in (
            ('mira', 'pc', 'Mira'),
            ('auril-church', 'place', 'Church of Auril'),
            ('harbour-master', 'npc', 'Harbour Master Brae'),
        ):
            core.write_doc('codex/' + ident, shapes.CODEX_ENTRY.new(id=ident, type=kind, name=name))
        core.write_doc(
            'ledger/' + LEDGER,
            shapes.LEDGER.new(
                id=LEDGER,
                session='s1',
                status='applied',
                selected=['e0-church'],
                events=[
                    shapes.LEDGER_EVENT.new(
                        id='e0-church',
                        kind='thread',
                        target='church',
                        status='open',
                        quote='the church of Auril has fallen',
                        at=20,
                    )
                ],
            ),
        )

    def drain(self):
        while not core.LANES['claude'].empty():
            core.LANES['claude'].get_nowait()

    def stored(self, name):
        return core.read_json(core.doc_path(name))

    def thread(self, ident):
        return records.read(campaign.active().data, 'threads', ident)

    def arcs(self):
        return [core.read_json(core.doc_path('arcs/' + i)) for i in core.list_docs('arcs')]

    def draft(self, threads=('church', 'smuggler')):
        job = core.start_arcs(list(threads))
        [(queued, cmd, stdin)] = [core.LANES['claude'].get_nowait()]
        self.assertEqual(queued['id'], job['id'])
        core.execute_job(queued, cmd, stdin)
        [arc] = self.arcs()
        return job, arc, stdin


class ArcJobTests(ArcCase):
    def test_a_draft_asks_about_the_chosen_threads_with_their_evidence_and_nothing_else(self):
        job, arc, stdin = self.draft()

        self.assertEqual((job['kind'], job['arc']), ('arc-options', arc['id']))
        self.assertEqual((arc['status'], arc['job'], arc['error']), ('review', '', ''))
        self.assertEqual([o['id'] for o in arc['options']], ['o1', 'o2', 'o3'])
        self.assertEqual(shapes.ARC.problems(arc), [])
        self.assertIn('The church was blown up by the party.', stdin)
        self.assertIn(
            'the church of Auril has fallen', stdin
        )  # the confirmed quote about the thread
        self.assertIn('Harbour Master Brae', stdin)  # in the codex index
        self.assertNotIn('An old matter', stdin)  # a thread nobody chose
        self.assertIn('never instructions', stdin)

    def test_starting_checks_the_threads_and_how_many_proposals_are_kept(self):
        for bad in (
            [],
            'church',
            ['church', 'church'],
            [7],
            ['nothing'],
            ['old'],  # resolved
            ['church', 'smuggler', 'a', 'b', 'c', 'd'],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                core.start_arcs(bad)
        self.assertEqual(self.arcs(), [])
        self.assertTrue(core.LANES['claude'].empty())

        with patch.object(arc_options, 'MAX_ARCS', 1):
            core.start_arcs(['church'])
            with self.assertRaisesRegex(ValueError, 'Remove some old'):
                core.start_arcs(['smuggler'])

    def test_a_failed_cancelled_or_interrupted_draft_leaves_a_failed_proposal(self):
        self.claude.fail = 'The provider stopped.'
        job, arc, _ = self.draft()
        self.assertEqual(arc['status'], 'failed')
        self.assertIn('The provider stopped.', arc['error'])
        self.assertEqual(arc['options'], [])

        core.remove_arc(arc['id'])
        queued = core.start_arcs(['church'])
        self.drain()
        core.fail_job(queued, RuntimeError('Cancelled.'))
        [cancelled] = self.arcs()
        self.assertEqual((cancelled['status'], cancelled['error']), ('failed', 'Cancelled.'))

        core.remove_arc(cancelled['id'])
        again = core.start_arcs(['church'])
        self.drain()
        core.recover_interrupted_job(again)
        self.assertEqual(self.arcs()[0]['status'], 'failed')

    def test_a_proposal_that_skips_a_thread_fails_and_writes_nothing_to_threads(self):
        self.claude.reply = {'options': [option('church', 'twist')]}
        _, arc, _ = self.draft()
        self.assertEqual(arc['status'], 'failed')
        self.assertIn('skipped a thread', arc['error'])
        self.assertEqual(self.thread('church')['status'], 'open')

    def test_a_proposal_removed_while_drafting_is_not_brought_back(self):
        job = core.start_arcs(['church'])
        [(queued, cmd, stdin)] = [core.LANES['claude'].get_nowait()]
        with self.assertRaisesRegex(ValueError, 'Cancel the drafting job'):
            core.remove_arc(self.arcs()[0]['id'])
        core.commit_docs('Remove', [('arcs/' + queued['arc'], None)])
        core.execute_job(queued, cmd, stdin)
        self.assertEqual(self.arcs(), [])
        self.assertEqual(core.JOBS_SERVICE.iter_jobs().__next__()['status'], 'failed')
        self.assertEqual(job['arc'], queued['arc'])


class ArcApplyTests(ArcCase):
    def test_only_the_chosen_option_changes_its_thread_and_a_retry_changes_nothing_more(self):
        _, arc, _ = self.draft()
        self.assertEqual(self.thread('church')['status'], 'open')  # a draft changes nothing

        applied = core.apply_arc(arc['id'], [{'option': 'o2', 'summary': 'The GM reworded this.'}])

        church = self.thread('church')
        self.assertEqual(church['status'], 'planned')
        self.assertIn('Arc plan (twist): A twist. The GM reworded this.', church['detail'])
        self.assertTrue(church['detail'].startswith('The church was blown up by the party.'))
        self.assertEqual(self.thread('smuggler')['status'], 'foreshadowed')
        self.assertEqual((applied['status'], applied['choices']), ('applied', ['o2']))
        self.assertEqual(applied['base_revs'], {})
        stored = self.stored('arcs/' + arc['id'])
        self.assertEqual(stored['options'][1]['summary'], 'The GM reworded this.')
        self.assertEqual(stored['options'][0]['summary'], arc['options'][0]['summary'])

        before = church['detail']
        again = core.apply_arc(arc['id'], [{'option': 'o2'}])
        self.assertEqual(again['choices'], ['o2'])
        self.assertEqual(self.thread('church')['detail'], before)
        with self.assertRaisesRegex(ValueError, 'different choices'):
            core.apply_arc(arc['id'], [{'option': 'o1'}])

    def test_several_threads_apply_together_and_a_link_to_a_new_entry_is_kept(self):
        _, arc, _ = self.draft()
        core.apply_arc(arc['id'], [{'option': 'o1'}, {'option': 'o3'}])
        self.assertEqual(self.thread('church')['entries'], ['auril-church'])
        self.assertEqual(self.thread('smuggler')['entries'], ['harbour-master'])
        self.assertEqual(self.thread('smuggler')['status'], 'planned')

    def test_a_thread_changed_since_the_proposal_blocks_apply(self):
        _, arc, _ = self.draft()
        church = self.thread('church')
        church['detail'] += ' The GM added a note.'
        core.write_doc('threads/church', church)
        with self.assertRaisesRegex(ValueError, 'changed since'):
            core.apply_arc(arc['id'], [{'option': 'o1'}])
        self.assertEqual(self.stored('arcs/' + arc['id'])['status'], 'review')
        core.apply_arc(arc['id'], [{'option': 'o3'}])  # a thread that did not change is fine
        self.assertEqual(self.thread('smuggler')['status'], 'planned')

    def test_nothing_applies_unless_the_draft_is_ready_and_the_choice_is_valid(self):
        job = core.start_arcs(['church'])
        arc_id = job['arc']
        with self.assertRaisesRegex(ValueError, 'Choose options from this proposal'):
            core.apply_arc(arc_id, [{'option': 'o1'}])  # still drafting: it has no options
        self.drain()
        core.fail_job(job, RuntimeError('Cancelled.'))
        core.remove_arc(arc_id)
        with self.assertRaises(LookupError):
            core.apply_arc(arc_id, [{'option': 'o1'}])
        with self.assertRaises(ValueError):
            core.apply_arc('../etc', [{'option': 'o1'}])

        _, arc, _ = self.draft()
        for bad in ([], [{'option': 'o1'}, {'option': 'o2'}], [{'option': 'o1', 'title': ''}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                core.apply_arc(arc['id'], bad)
        self.assertEqual(self.thread('church')['status'], 'open')

    def test_a_removed_thread_stops_the_apply_and_removing_a_proposal_keeps_its_changes(self):
        _, arc, _ = self.draft()
        core.commit_docs('Remove thread', [('threads/smuggler', None)])
        with self.assertRaises(ValueError):
            core.apply_arc(arc['id'], [{'option': 'o3'}])

        core.apply_arc(arc['id'], [{'option': 'o1'}])
        core.remove_arc(arc['id'])
        self.assertEqual(self.arcs(), [])
        self.assertEqual(self.thread('church')['status'], 'planned')  # the plan stays in the thread
        with self.assertRaises(LookupError):
            core.remove_arc(arc['id'])

    def test_seeds_follow_applied_choices_until_their_thread_is_resolved(self):
        _, arc, _ = self.draft()
        self.assertEqual(core.arc_seeds(), [])  # nothing chosen yet
        core.apply_arc(arc['id'], [{'option': 'o1', 'pitch': 'The church is rebuilt by cultists.'}])
        [seed] = core.arc_seeds()
        self.assertEqual(
            (seed['title'], seed['kind'], seed['pitch']),
            ('Church of Auril', 'resolution', 'The church is rebuilt by cultists.'),
        )
        church = self.thread('church')
        church['status'] = 'resolved'
        core.write_doc('threads/church', church)
        self.assertEqual(core.arc_seeds(), [])

    def test_cards_list_proposals_newest_first_without_their_options(self):
        _, first, _ = self.draft()
        core.write_doc('arcs/' + first['id'], dict(first, created=100))
        self.claude.reply = {'options': [option('smuggler', 'twist')]}
        core.start_arcs(['smuggler'])
        cards = core.arc_cards()
        self.assertEqual([c['status'] for c in cards], ['running', 'review'])
        self.assertEqual(cards[1]['options'], 3)
        self.assertEqual(cards[0]['threads'], [{'id': 'smuggler', 'title': 'The escaped smuggler'}])
        self.assertNotIn('base_revs', cards[0])


class ArcRouteTests(ArcCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), http_routes.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def call(self, path, method='GET', body=None):
        data = json.dumps(body or {}).encode() if method != 'GET' else None
        headers = {'X-DM-Site': '1', 'Content-Type': 'application/json'} if data else {}
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def test_a_proposal_is_drafted_read_applied_and_removed_through_the_routes(self):
        status, job = self.call('/api/arcs/start', 'POST', {'threads': ['church', 'smuggler']})
        self.assertEqual((status, job['kind']), (200, 'arc-options'))
        [(queued, cmd, stdin)] = [core.LANES['claude'].get_nowait()]
        core.execute_job(queued, cmd, stdin)

        _, listing = self.call('/api/arcs')
        [card] = listing['items']
        self.assertEqual(
            (card['id'], card['status'], listing['max_threads']), (job['arc'], 'review', 5)
        )
        status, arc = self.call('/api/arcs/' + card['id'])
        self.assertEqual((status, len(arc['options'])), (200, 3))
        self.assertNotIn('base_revs', arc)

        status, done = self.call(
            f'/api/arcs/{card["id"]}/apply', 'POST', {'choices': [{'option': 'o1'}]}
        )
        self.assertEqual((status, done['choices']), (200, ['o1']))
        self.assertEqual(self.call('/api/arcs/seeds')[1]['items'][0]['kind'], 'resolution')
        self.assertEqual(self.call(f'/api/arcs/{card["id"]}/remove', 'POST')[0], 200)
        self.assertEqual(self.call('/api/arcs')[1]['items'], [])

    def test_bad_requests_are_refused_without_writing(self):
        self.assertEqual(self.call('/api/arcs/start', 'POST', {})[0], 400)
        self.assertEqual(self.call('/api/arcs/start', 'POST', {'threads': ['old']})[0], 400)
        self.assertEqual(self.call('/api/arcs/not-an-id')[0], 400)
        self.assertEqual(self.call('/api/arcs/arc-00000000')[0], 404)
        self.assertEqual(self.call('/api/arcs/arc-00000000/apply', 'POST', {'choices': []})[0], 404)
        self.assertEqual(self.call('/api/arcs/arc-00000000/remove', 'POST')[0], 404)
        self.assertEqual(self.call('/api/arcs/bad/apply', 'POST', {'choices': []})[0], 400)
        self.assertEqual(self.arcs(), [])
        # The generic document routes cannot write proposals.
        self.assertEqual(self.call('/api/doc/arcs/arc-00000000', 'PUT', {})[0], 403)


if __name__ == '__main__':
    unittest.main()
