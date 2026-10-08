"""Sorting a transcript into play and table banter: proposals, the GM's decisions and table lore.

The model is faked by a runner that answers each window by keyword, the way a careful reader would. No
`claude` program runs and nothing is sent anywhere.
"""

import json
import re
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
import campaign
import campaign_core as core
import http_routes
import shapes
import transcript_classifier as sorter

HORSE = "Ulrick's invented horse form, a table joke"
SCRIPT = [
    'Welcome back. The party stands at the gate of Muttonham.',
    'Mira casts a spell and the gate of the church of Auril collapses.',
    'Ulrick can turn into a horse form now, hehe, neigh.',
    'Who ordered pizza?',
    'Rolling for initiative.',
    'The church of Auril burns.',
]


def transcript(ident='rec-0123456789abcdef', texts=SCRIPT, **fields):
    segments = [
        shapes.TRANSCRIPT_SEGMENT.new(start=i * 5, end=i * 5 + 4, text=text)
        for i, text in enumerate(texts)
    ]
    return shapes.TRANSCRIPT.new(id=ident, title='session-one', segments=segments, **fields)


def answer(prompt):
    """A window's passages, grouped by what each line is about (the stand-in for the model)."""
    body = prompt.split('TRANSCRIPT:\n', 1)[1]
    known = json.loads(re.search(r'KNOWN TABLE LORE: (.*)', prompt).group(1))
    groups = []
    for line in body.splitlines():
        index, text = re.match(r'\[(\d+)\] \S+ (.*)', line).groups()
        kind = (
            'banter'
            if re.search('horse form|pizza', text)
            else 'play'
            if re.search('Auril|gate|Mira', text)
            else 'unclear'
        )
        if groups and groups[-1]['kind'] == kind:
            groups[-1]['last'] = int(index)
            groups[-1]['text'] += ' ' + text
        else:
            groups.append(dict(first=int(index), last=int(index), kind=kind, text=text))
    passages = []
    for group in groups:
        horse = 'horse form' in group['text']
        passages.append(
            dict(
                first=group['first'],
                last=group['last'],
                kind=group['kind'],
                gist=group['kind'] + ': ' + group['text'][:40],
                remember=HORSE if horse and not known else '',
                lore=known[0]['id'] if horse and known else '',
            )
        )
    return {'passages': passages}


class FakeClaude:
    """Stands in for the Claude Code command: it records each prompt and answers it, or fails."""

    def __init__(self):
        self.prompts = []
        self.fail = None  # a message to exit with
        self.reply = None  # an answer to give instead of reading the prompt

    def start(self, cmd, cwd, env, log, has_stdin):
        self.log = log
        return self

    def feed(self, text):
        self.prompts.append(text)
        if self.fail:
            self.log.write(self.fail)
            return
        draft = self.reply if self.reply is not None else answer(text)
        envelope = {'type': 'result', 'is_error': False, 'structured_output': draft}
        self.log.write(json.dumps(envelope | {'usage': {'input_tokens': 10}}))

    def wait(self):
        return 1 if self.fail else 0

    def terminate(self):
        pass


class PassageTests(unittest.TestCase):
    def passages(self, rows, first=0, stop=6, lore=()):
        return sorter.passages_from({'passages': rows}, first, stop, set(lore))

    @staticmethod
    def row(first, last, kind='play', **fields):
        return dict(first=first, last=last, kind=kind, gist='', remember='', lore='') | fields

    def test_proposals_cover_every_segment_and_a_gap_is_unclear_never_play(self):
        found = self.passages([self.row(1, 2), self.row(4, 4, 'banter')])
        self.assertEqual(
            [(p['first'], p['last'], p['kind']) for p in found],
            [
                (0, 0, 'unclear'),
                (1, 2, 'play'),
                (3, 3, 'unclear'),
                (4, 4, 'banter'),
                (5, 5, 'unclear'),
            ],
        )
        self.assertFalse(any(p['confirmed'] for p in found))
        self.assertEqual([p['id'] for p in found], ['p0', 'p1', 'p3', 'p4', 'p5'])

    def test_overlaps_and_out_of_range_numbers_are_clipped_and_bad_rows_skipped(self):
        found = self.passages(
            [
                self.row(3, 9),
                self.row(0, 2),
                self.row(1, 4, 'banter'),  # starts inside the first: only what is left of it counts
                self.row(-5, -1),
                self.row('0', 1),
                self.row(0, 1, 'shouting'),
                'nonsense',
            ]
        )
        self.assertEqual(
            [(p['first'], p['last'], p['kind']) for p in found],
            [(0, 2, 'play'), (3, 4, 'banter'), (5, 5, 'play')],
        )

    def test_an_answer_with_nothing_usable_is_an_error(self):
        for draft in ({}, {'passages': []}, {'passages': [self.row(9, 12)]}, [], None):
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                sorter.passages_from(draft, 0, 6, set())

    def test_only_banter_carries_a_note_and_only_known_lore_can_be_matched(self):
        found = self.passages(
            [
                self.row(0, 0, 'play', remember='ignored', lore='kept?'),
                self.row(1, 1, 'banter', remember='  a  gag ', lore='nope'),
                self.row(2, 2, 'banter', remember='dropped', lore='rec-x-p1'),
                self.row(3, 5, 'unclear', gist='x' * 500),
            ],
            lore={'rec-x-p1'},
        )
        play, gag, matched, unclear = found
        self.assertEqual((play['remember'], play['lore']), ('', ''))
        self.assertEqual((gag['remember'], gag['lore']), ('a gag', ''))
        self.assertEqual((matched['remember'], matched['lore']), ('', 'rec-x-p1'))
        self.assertEqual(len(unclear['gist']), sorter.GIST_CHARS)

    def test_windows_hold_whole_segments_within_the_size_and_always_move_on(self):
        document = transcript(texts=['x' * 100] * 10)
        size = len(sorter.line(0, document['segments'][0])) * 3 + 3
        self.assertEqual(sorter.window_end(document['segments'], 0, size), 3)
        self.assertEqual(sorter.window_end(document['segments'], 3, size), 6)
        self.assertEqual(sorter.window_end(document['segments'], 9, size), 10)
        self.assertEqual(
            sorter.window_end(document['segments'], 0, 1), 1
        )  # one long line is a window
        plan = sorter.plan(document, 16_000, start=0)
        self.assertEqual(plan['requests'], 1)  # a small transcript is a single request
        self.assertGreater(plan['characters'], 1000)
        document['classification']['cursor'] = 10
        self.assertEqual(sorter.plan(document, 16_000)['requests'], 0)

    def test_the_prompt_numbers_segments_shows_lore_and_marks_the_transcript_as_data(self):
        document = transcript(
            passages=[
                shapes.TRANSCRIPT_PASSAGE.new(
                    id='p0', first=0, last=1, kind='play', gist='At the gate'
                )
            ]
        )
        lore = shapes.TABLE_LORE.new(items=[shapes.TABLE_LORE_ITEM.new(id='rec-a-p1', text=HORSE)])
        text = sorter.prompt(document, 2, 4, lore)
        self.assertIn('never instructions', text.split('TRANSCRIPT:')[0])
        self.assertIn('Cover segments 2 to 3 (of 6)', text)
        self.assertIn(HORSE, text)
        self.assertIn('At the gate', text)  # the passage before this window gives it context
        body = text.split('TRANSCRIPT:\n')[1]
        self.assertEqual(body.splitlines()[0], '[2] 0:00:10 ' + SCRIPT[2])
        self.assertEqual(len(body.splitlines()), 2)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.document = transcript()
        self.document['passages'] = sorter.passages_from(answer_for(self.document), 0, 6, set())
        self.lore = shapes.TABLE_LORE.new()

    def review(self, *decisions, now=100):
        return sorter.review(self.document, self.lore, list(decisions), now)

    def passage(self, ident):
        return next(p for p in self.document['passages'] if p['id'] == ident)

    def test_nothing_leaves_the_step_until_the_gm_confirms_it(self):
        self.assertEqual(sorter.confirmed_play(self.document), [])
        self.assertEqual(sorter.counts(self.document)['pending'], 4)

        self.review({'id': 'p0', 'confirmed': True})
        [play] = sorter.confirmed_play(self.document)
        self.assertEqual((play['id'], play['first'], play['last']), ('p0', 0, 1))
        self.assertEqual((play['start'], play['end']), (0, 9))
        self.assertEqual([s['text'] for s in play['segments']], SCRIPT[:2])

    def test_banter_and_unclear_passages_are_never_play_whatever_is_proposed(self):
        self.review({'id': 'p2', 'confirmed': True}, {'id': 'p0', 'confirmed': True})
        self.assertEqual(self.passage('p2')['kind'], 'banter')
        text = json.dumps(sorter.confirmed_play(self.document))
        self.assertNotIn('horse form', text)
        self.assertNotIn('pizza', text)
        self.assertNotIn('initiative', text)  # an unclear passage is not play either

    def test_an_unclear_passage_must_be_decided_before_it_can_be_confirmed(self):
        with self.assertRaisesRegex(ValueError, 'Choose in-game or table banter'):
            self.review({'id': 'p4', 'confirmed': True})
        self.review({'id': 'p4', 'kind': 'play', 'confirmed': True})
        self.assertEqual([p['id'] for p in sorter.confirmed_play(self.document)], ['p4'])

    def test_confirmed_banter_with_a_note_becomes_table_lore_once(self):
        decision = {'id': 'p2', 'confirmed': True, 'remember': HORSE}
        self.review(decision)
        self.review(decision, now=200)  # repeating a decision adds nothing

        [item] = self.lore['items']
        self.assertEqual(
            (item['id'], item['text'], item['transcript'], item['passage'], item['added']),
            ('rec-0123456789abcdef-p2', HORSE, 'rec-0123456789abcdef', 'p2', 100),
        )
        self.assertEqual(self.passage('p2')['lore'], item['id'])

    def test_banter_confirmed_without_a_note_is_skipped_but_not_remembered(self):
        self.review({'id': 'p2', 'confirmed': True, 'remember': ''})
        self.assertEqual(self.lore['items'], [])
        self.assertTrue(self.passage('p2')['confirmed'])

    def test_undoing_banter_or_calling_it_play_removes_the_note_it_saved(self):
        self.review({'id': 'p2', 'confirmed': True, 'remember': HORSE})
        self.review({'id': 'p2', 'confirmed': False})
        self.assertEqual((self.lore['items'], self.passage('p2')['lore']), ([], ''))

        self.review({'id': 'p2', 'confirmed': True, 'remember': HORSE})
        self.review({'id': 'p2', 'kind': 'play', 'confirmed': True})
        self.assertEqual((self.lore['items'], self.passage('p2')['remember']), ([], ''))

    def test_a_note_can_be_edited_and_clearing_it_forgets_the_gag(self):
        self.review({'id': 'p2', 'confirmed': True, 'remember': HORSE})
        self.review({'id': 'p2', 'remember': 'Ulrick neighs'})
        self.assertEqual([i['text'] for i in self.lore['items']], ['Ulrick neighs'])
        self.review({'id': 'p2', 'remember': ''})
        self.assertEqual(self.lore['items'], [])

    def test_play_overrides_a_match_with_known_lore(self):
        self.passage('p2').update(lore='rec-aaaaaaaaaaaaaaaa-p1')
        self.assertEqual(sorter.counts(self.document)['known'], 1)
        self.review({'id': 'p2', 'kind': 'play', 'confirmed': True})
        self.assertEqual(self.passage('p2')['lore'], '')
        self.assertEqual(sorter.counts(self.document)['known'], 0)

    def test_table_lore_is_bounded_and_decisions_are_validated(self):
        self.lore['items'] = [
            shapes.TABLE_LORE_ITEM.new(id=f'rec-{i:016x}-p1', text='gag')
            for i in range(sorter.MAX_LORE)
        ]
        with self.assertRaisesRegex(ValueError, 'Table lore is full'):
            self.review({'id': 'p2', 'confirmed': True, 'remember': HORSE})
        for bad in (
            [],
            'p0',
            [{'id': 'p99'}],
            [{'id': 'p0', 'kind': 'shout'}],
            [{'id': 'p0', 'confirmed': 'yes'}],
            ['p0'],
            [{'id': 'p0'}] * (sorter.MAX_DECISIONS + 1),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sorter.review(self.document, self.lore, bad)

    def test_the_review_list_pages_filters_and_excerpts_what_was_said(self):
        page = sorter.listing(self.document, 'pending', 0, 2)
        self.assertEqual((page['total'], len(page['items'])), (4, 2))
        first = page['items'][0]
        self.assertEqual((first['start'], first['end'], first['segment_count']), (0, 9, 2))
        self.assertIn('church of Auril', first['excerpt'])

        self.review({'id': 'p0', 'confirmed': True})
        self.passage('p2').update(lore='rec-aaaaaaaaaaaaaaaa-p1')
        counts = lambda show: [p['id'] for p in sorter.listing(self.document, show)['items']]
        self.assertEqual(counts('confirmed'), ['p0'])
        self.assertEqual(counts('known'), ['p2'])
        self.assertEqual(counts('pending'), ['p4', 'p5'])
        self.assertEqual(len(counts('all')), 4)
        with self.assertRaises(ValueError):
            sorter.listing(self.document, 'everything')


def answer_for(document):
    prompt = sorter.prompt(document, 0, len(document['segments']), shapes.TABLE_LORE.new())
    return answer(prompt)


class SortingCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous = campaign.activate(campaign.Campaign(self.root / 'Studio'))
        self.addCleanup(campaign.activate, previous)
        self.claude = FakeClaude()
        self.addCleanup(patch.stopall)
        patch.object(core.JOBS_SERVICE, 'runner', self.claude).start()
        self.command = patch('ai_provider.command', return_value=['claude', '-p']).start()
        self.addCleanup(self.drain)
        self.settings(context_budget_chars=16_000)

    def drain(self):
        while not core.LANES['claude'].empty():
            core.LANES['claude'].get_nowait()

    def settings(self, **changes):
        path = Path(campaign.active().settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(changes))

    def store(self, **kwargs):
        document = transcript(**kwargs)
        core.write_doc('transcripts/' + document['id'], document)
        return document['id']

    def stored(self, ident='rec-0123456789abcdef'):
        return core.read_json(core.doc_path('transcripts/' + ident))

    def lore(self):
        return core.read_json(core.doc_path('table-lore'), shapes.TABLE_LORE.new())['items']

    def run_next(self):
        job, cmd, stdin = core.LANES['claude'].get_nowait()
        core.execute_job(job, cmd, stdin)
        return json.loads(Path(core.job_file(job['id'])).read_text(encoding='utf-8'))

    def run_all(self):
        done = []
        while not core.LANES['claude'].empty():
            done.append(self.run_next())
        return done

    def long_script(self, count=150):
        return [
            'Ulrick makes another horse form joke, neigh neigh.'
            if i % 10 == 0
            else f'Mira fights at the gate of Auril, round {i}, with some more words here.'
            for i in range(count)
        ]


class SortingJobTests(SortingCase):
    def test_sorting_queues_a_window_and_stores_its_passages_as_proposals(self):
        ident = self.store()
        job = core.start_classification(ident)

        self.assertEqual(
            (job['kind'], job['classify'], job['first'], job['stop']), ('classify', ident, 0, 6)
        )
        self.assertEqual(self.stored()['classification']['status'], 'running')
        self.assertEqual(self.run_next()['status'], 'done')

        document = self.stored()
        self.assertEqual(
            document['classification'], dict(status='done', cursor=6, job='', error='')
        )
        self.assertEqual(
            [(p['id'], p['kind'], p['confirmed']) for p in document['passages']],
            [
                ('p0', 'play', False),
                ('p2', 'banter', False),
                ('p4', 'unclear', False),
                ('p5', 'play', False),
            ],
        )
        self.assertEqual(self.stored()['passages'][1]['remember'], HORSE)
        self.assertEqual(sorter.confirmed_play(document), [])  # a proposal is not yet play
        self.assertIn(
            'input_tokens', json.loads(Path(core.job_file(job['id'])).read_text())['usage']
        )

    def test_a_long_transcript_is_sorted_window_by_window_and_covered_exactly(self):
        ident = self.store(texts=self.long_script())
        core.start_classification(ident)
        jobs = self.run_all()

        self.assertGreater(len(jobs), 2)
        self.assertEqual([(j['first'] > 0) for j in jobs][0], False)
        for job, after in zip(jobs, jobs[1:]):
            self.assertEqual(after['first'], job['stop'])  # windows meet, none repeated
        document = self.stored()
        self.assertEqual(document['classification']['status'], 'done')
        edges = [(p['first'], p['last']) for p in document['passages']]
        self.assertEqual(edges[0][0], 0)
        self.assertEqual(edges[-1][1], 149)
        self.assertTrue(all(a[1] + 1 == b[0] for a, b in zip(edges, edges[1:])))
        for prompt in self.claude.prompts:
            self.assertLess(len(prompt), 9_000)  # each request stays small

    def test_a_failed_window_stops_there_keeps_earlier_work_and_resumes(self):
        ident = self.store(texts=self.long_script())
        core.start_classification(ident)
        self.run_next()  # the first window works
        window = self.stored()['classification']['cursor']
        self.assertGreater(window, 0)
        decided = self.stored()['passages'][0]['id']
        core.review_passages(ident, [{'id': decided, 'confirmed': True}])

        self.claude.fail = 'Claude Code could not reach the service.'
        self.run_next()
        document = self.stored()
        self.assertEqual(document['classification']['status'], 'failed')
        self.assertEqual(document['classification']['cursor'], window)
        self.assertIn('could not reach', document['classification']['error'])
        self.assertEqual(document['classification']['job'], '')

        self.claude.fail = None
        core.start_classification(ident)  # continues from the failed window
        self.run_all()
        document = self.stored()
        self.assertEqual(document['classification']['status'], 'done')
        self.assertTrue(next(p for p in document['passages'] if p['id'] == decided)['confirmed'])
        self.assertEqual(self.claude.prompts[-1].count('Cover segments'), 1)

    def test_a_window_already_paid_for_is_kept_when_the_next_cannot_be_queued(self):
        ident = self.store(texts=self.long_script())
        core.start_classification(ident)
        self.command.side_effect = ValueError('Claude Code is not installed.')
        self.assertEqual(self.run_next()['status'], 'done')

        document = self.stored()
        self.assertGreater(len(document['passages']), 0)
        self.assertEqual(document['classification']['status'], 'failed')
        self.assertEqual(document['classification']['cursor'], document['passages'][-1]['last'] + 1)
        self.assertIn('not installed', document['classification']['error'])
        self.command.side_effect = None
        core.start_classification(ident)  # continues after the kept window
        self.run_all()
        self.assertEqual(self.stored()['classification']['status'], 'done')

    def test_an_answer_that_covers_nothing_fails_the_window_and_stores_nothing(self):
        ident = self.store()
        core.start_classification(ident)
        self.claude.reply = {'passages': []}
        self.assertEqual(self.run_next()['status'], 'failed')
        document = self.stored()
        self.assertEqual(
            (document['passages'], document['classification']['status']), ([], 'failed')
        )
        self.assertIn('covered none', document['classification']['error'])

    def test_cancelling_a_queued_window_stops_sorting_and_it_can_continue(self):
        ident = self.store()
        job = core.start_classification(ident)
        core.cancel_job(job['id'])
        self.assertEqual(self.stored()['classification']['status'], 'failed')
        self.drain()
        core.start_classification(ident)
        self.run_all()
        self.assertEqual(self.stored()['classification']['status'], 'done')

    def test_a_server_restart_fails_a_running_window_and_sorting_resumes(self):
        ident = self.store()
        job = core.start_classification(ident)
        self.drain()
        saved = json.loads(Path(core.job_file(job['id'])).read_text())
        saved['status'] = 'running'
        Path(core.job_file(job['id'])).write_text(json.dumps(saved))
        core.JOBS_SERVICE.recover_unfinished()
        self.assertEqual(self.stored()['classification']['status'], 'failed')
        self.assertIn('stopped', self.stored()['classification']['error'])
        core.start_classification(ident)
        self.run_all()
        self.assertEqual(self.stored()['classification']['status'], 'done')

    def test_sorting_again_forgets_passages_but_not_table_lore(self):
        ident = self.store()
        core.start_classification(ident)
        self.run_all()
        core.review_passages(ident, [{'id': 'p2', 'confirmed': True, 'remember': HORSE}])
        with self.assertRaisesRegex(ValueError, 'Sort it again'):
            core.start_classification(ident)

        core.start_classification(ident, restart=True)
        self.assertEqual(self.stored()['passages'], [])
        self.assertEqual(len(self.lore()), 1)
        self.run_all()
        self.assertEqual(self.stored()['classification']['status'], 'done')

    def test_a_transcript_removed_or_changed_mid_run_is_not_resurrected(self):
        ident = self.store()
        core.start_classification(ident)
        core.commit_docs('Remove', [('transcripts/' + ident, None)])
        self.assertEqual(self.run_next()['status'], 'failed')
        self.assertIsNone(self.stored())

    def test_known_table_lore_reaches_the_prompt_and_later_matches_are_skipped(self):
        first = self.store()
        core.start_classification(first)
        self.run_all()
        core.review_passages(first, [{'id': 'p2', 'confirmed': True, 'remember': HORSE}])

        later = self.store(ident='rec-fedcba9876543210')
        core.start_classification(later)
        self.run_all()
        self.assertIn(HORSE, self.claude.prompts[-1])
        document = self.stored('rec-fedcba9876543210')
        gag = next(p for p in document['passages'] if p['kind'] == 'banter')
        self.assertEqual(gag['lore'], 'rec-0123456789abcdef-p2')
        self.assertEqual(gag['remember'], '')  # not offered for saving a second time
        counts = sorter.counts(document)
        self.assertEqual((counts['known'], counts['pending']), (1, 3))
        self.assertEqual(sorter.listing(document, 'pending')['total'], 3)
        self.assertEqual(sorter.confirmed_play(document), [])
        self.assertEqual(len(self.lore()), 1)

    def test_removing_table_lore_unlinks_the_passages_that_matched_it(self):
        first = self.store()
        core.start_classification(first)
        self.run_all()
        core.review_passages(first, [{'id': 'p2', 'confirmed': True, 'remember': HORSE}])
        later = self.store(ident='rec-fedcba9876543210')
        core.start_classification(later)
        self.run_all()

        core.remove_lore('rec-0123456789abcdef-p2')
        self.assertEqual(self.lore(), [])
        self.assertTrue(
            all(p['lore'] == '' for p in self.stored('rec-fedcba9876543210')['passages'])
        )
        self.assertEqual(self.stored()['passages'][1]['lore'], '')
        self.assertTrue(self.stored()['passages'][1]['confirmed'])  # the decision itself stays
        with self.assertRaises(LookupError):
            core.remove_lore('rec-0123456789abcdef-p2')

    def test_undoing_the_banter_that_saved_a_note_unlinks_later_matches(self):
        first = self.store()
        core.start_classification(first)
        self.run_all()
        core.review_passages(first, [{'id': 'p2', 'confirmed': True, 'remember': HORSE}])
        later = self.store(ident='rec-fedcba9876543210')
        core.start_classification(later)
        self.run_all()

        core.review_passages(first, [{'id': 'p2', 'confirmed': False}])
        self.assertEqual(self.lore(), [])
        self.assertTrue(
            all(p['lore'] == '' for p in self.stored('rec-fedcba9876543210')['passages'])
        )

    def test_the_budget_bounds_every_request(self):
        self.settings(context_budget_chars=16_000)
        ident = self.store(texts=['word ' * 300] * 40)
        core.start_classification(ident)
        self.run_all()
        for prompt in self.claude.prompts:
            self.assertLess(len(prompt), 16_000)


class SortingRouteTests(SortingCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), http_routes.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.ident = self.store()

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

    def sort(self):
        self.assertEqual(self.call(f'/api/transcripts/{self.ident}/classify', 'POST')[0], 200)
        self.run_all()

    def test_a_transcript_card_says_what_sorting_will_ask_before_it_starts(self):
        _, listing = self.call('/api/transcripts')
        [card] = listing['items']
        self.assertEqual(card['review']['passages'], 0)
        self.assertEqual(card['plan']['requests'], 1)
        self.assertEqual(card['whole'], card['plan'])
        self.assertEqual(card['classification']['status'], '')
        self.assertNotIn('passages', card)
        self.assertNotIn('segments', card)

    def test_sorting_is_one_request_at_a_time_and_unknown_transcripts_are_refused(self):
        self.assertEqual(self.call(f'/api/transcripts/{self.ident}/classify', 'POST')[0], 200)
        status, body = self.call(f'/api/transcripts/{self.ident}/classify', 'POST')
        self.assertEqual(status, 409)
        self.assertIn('already being', body['error'])
        self.assertEqual(self.call(f'/api/transcripts/{self.ident}/remove', 'POST')[0], 409)
        self.assertEqual(
            self.call('/api/transcripts/rec-0000000000000000/classify', 'POST')[0], 404
        )
        self.assertEqual(self.call('/api/transcripts/rec-nothing/classify', 'POST')[0], 400)
        self.run_all()
        self.assertEqual(self.call(f'/api/transcripts/{self.ident}/classify', 'POST')[0], 400)
        self.assertEqual(
            self.call(f'/api/transcripts/{self.ident}/classify', 'POST', {'restart': True})[0], 200
        )

    def test_passages_page_through_the_review_list_by_filter(self):
        self.sort()
        status, page = self.call(f'/api/transcripts/{self.ident}/passages?limit=2')
        self.assertEqual((status, page['total'], len(page['items'])), (200, 4, 2))
        self.assertEqual(page['counts']['pending'], 4)
        _, rest = self.call(f'/api/transcripts/{self.ident}/passages?limit=2&offset=2')
        self.assertEqual([p['id'] for p in rest['items']], ['p4', 'p5'])
        self.assertEqual(self.call(f'/api/transcripts/{self.ident}/passages?show=nope')[0], 400)
        self.assertEqual(self.call('/api/transcripts/rec-0000000000000000/passages')[0], 404)
        self.assertEqual(self.call(f'/api/transcripts/{self.ident}/passages?limit=x')[0], 400)

    def test_decisions_are_saved_and_banter_notes_become_table_lore(self):
        self.sort()
        status, saved = self.call(
            f'/api/transcripts/{self.ident}/review',
            'POST',
            {
                'decisions': [
                    {'id': 'p0', 'confirmed': True},
                    {'id': 'p2', 'confirmed': True, 'remember': HORSE},
                ]
            },
        )
        self.assertEqual(
            (status, saved['counts']['confirmed'], saved['counts']['pending']), (200, 2, 2)
        )
        _, lore = self.call('/api/table-lore')
        self.assertEqual([i['text'] for i in lore['items']], [HORSE])
        self.assertEqual(lore['max'], sorter.MAX_LORE)

        self.assertEqual(self.call(f'/api/transcripts/{self.ident}/review', 'POST', {})[0], 400)
        bad = {'decisions': [{'id': 'p4', 'confirmed': True}]}
        self.assertEqual(self.call(f'/api/transcripts/{self.ident}/review', 'POST', bad)[0], 400)
        self.assertEqual(
            self.call('/api/transcripts/rec-0000000000000000/review', 'POST', bad)[0], 404
        )
        self.assertEqual(
            self.stored()['passages'][3]['confirmed'], False
        )  # the bad request wrote nothing

    def test_table_lore_is_removed_by_id_and_only_through_its_route(self):
        self.sort()
        self.call(
            f'/api/transcripts/{self.ident}/review',
            'POST',
            {'decisions': [{'id': 'p2', 'confirmed': True, 'remember': HORSE}]},
        )
        lore_id = f'{self.ident}-p2'
        self.assertEqual(self.call('/api/table-lore/not-an-id/remove', 'POST')[0], 400)
        self.assertEqual(
            self.call('/api/table-lore/rec-0000000000000000-p1/remove', 'POST')[0], 404
        )
        self.assertEqual(self.call(f'/api/table-lore/{lore_id}/remove', 'POST')[0], 200)
        self.assertEqual(self.call('/api/table-lore')[1]['items'], [])
        # The generic document routes cannot edit it.
        self.assertEqual(self.call('/api/doc/table-lore', 'PUT', {'items': []})[0], 403)

    def test_transcribing_a_sorted_recording_again_needs_an_explicit_replace(self):
        recording = self.root / 'session-one.mp4'
        recording.write_bytes(b'not really a video' * 100)
        import transcription

        rec = transcription.recording(str(recording))
        document = transcript(ident=rec['id'])
        document['passages'] = [shapes.TRANSCRIPT_PASSAGE.new(id='p0', last=5, kind='play')]
        core.write_doc('transcripts/' + rec['id'], document)
        patch.object(transcription.FasterWhisper, 'problem', return_value='').start()

        status, body = self.call('/api/transcripts/start', 'POST', {'path': str(recording)})
        self.assertEqual(status, 409)
        self.assertIn('discards its passages', body['error'])
        status, _ = self.call(
            '/api/transcripts/start', 'POST', {'path': str(recording), 'replace': True}
        )
        self.assertEqual(status, 200)
        while not core.LANES['transcribe'].empty():
            core.LANES['transcribe'].get_nowait()


if __name__ == '__main__':
    unittest.main()
