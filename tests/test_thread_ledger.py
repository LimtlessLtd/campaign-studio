"""Confirmed-play ledger: exact evidence, review gate, recoverable apply and loose threads."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))

import campaign
import campaign_core as core
import records
import shapes
import thread_ledger

IDENT = 'rec-0123456789abcdef'


def recording():
    words = [
        'Mira says the church of Auril has fallen.',
        'Ulrick has a horse form, neigh, a table joke.',
        'The smuggler escaped towards the harbour.',
        'A whispered rumour mentions the harbour master.',
    ]
    document = shapes.TRANSCRIPT.new(
        id=IDENT,
        title='Session one',
        session='s1',
        segments=[
            shapes.TRANSCRIPT_SEGMENT.new(start=i * 10, end=i * 10 + 8, text=text)
            for i, text in enumerate(words)
        ],
    )
    document['classification'].update(status='done', cursor=len(words))
    document['passages'] = [
        shapes.TRANSCRIPT_PASSAGE.new(id=f'p{i}', first=i, last=i, kind=kind, confirmed=confirmed)
        for i, (kind, confirmed) in enumerate(
            [('play', True), ('banter', True), ('play', True), ('play', False)]
        )
    ]
    return document


def event(ident, kind, target, passage, quote, text, **changes):
    return {
        'id': ident,
        'kind': kind,
        'target': target,
        'title': '',
        'status': '',
        'text': text,
        'pcs': [],
        'passage': passage,
        'quote': quote,
    } | changes


def proposal():
    return {
        'events': [
            event(
                'church',
                'thread',
                'church',
                'p0',
                'church of Auril has fallen',
                'The church thread is resolved.',
                status='resolved',
                pcs=['mira'],
            ),
            event(
                'smuggler',
                'thread',
                'new:smuggler',
                'p2',
                'smuggler escaped',
                'The smuggler remains at large.',
                title='The escaped smuggler',
                status='open',
                pcs=['mira'],
            ),
            event(
                'note',
                'codex',
                'auril-church',
                'p0',
                'church of Auril has fallen',
                'The church fell during the session.',
            ),
            event(
                'outcome',
                'outcome',
                '',
                'p2',
                'smuggler escaped',
                'The smuggler escaped towards the harbour.',
            ),
        ]
    }


class LedgerUnitTests(unittest.TestCase):
    def test_only_confirmed_play_reaches_a_prompt_and_evidence_must_quote_it(self):
        document = recording()
        heard = thread_ledger.lines(document)
        self.assertEqual([row['passage'] for row in heard], ['p0', 'p2'])
        self.assertNotIn('horse form', str(heard))
        self.assertNotIn('harbour master', str(heard))
        ledger = shapes.LEDGER.new(id=IDENT, session='s1')
        prompt, _, first, stop = thread_ledger.prompt(
            document,
            ledger,
            [],
            [],
            shapes.PREP.new(n=1, title='Session one'),
            {},
        )
        self.assertEqual((first, stop), (0, 2))
        self.assertIn('smuggler escaped', prompt)
        self.assertNotIn('horse form', prompt)
        self.assertNotIn('harbour master', prompt)
        valid = thread_ledger.validate_batch(
            document, proposal(), 0, stop, {'church'}, {'auril-church'}, {'mira'}
        )
        self.assertEqual([item['at'] for item in valid], [0, 20, 0, 20])
        self.assertEqual(valid[1]['quote'], 'smuggler escaped')
        forged = proposal()
        forged['events'][1]['quote'] = 'Ulrick has a horse form'
        with self.assertRaisesRegex(ValueError, 'confirmed play'):
            thread_ledger.validate_batch(
                document, forged, 0, stop, {'church'}, {'auril-church'}, {'mira'}
            )

    def test_windowing_covers_a_long_confirmed_transcript_without_silent_truncation(self):
        rows = [{'text': 'x' * 1000} for _ in range(40)]
        edges = [0]
        while edges[-1] < len(rows):
            edges.append(thread_ledger.window_end(rows, edges[-1]))
        self.assertEqual(edges[0], 0)
        self.assertEqual(edges[-1], 40)
        self.assertGreater(len(edges), 3)
        self.assertTrue(all(a < b for a, b in zip(edges, edges[1:])))


class LedgerCampaignTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous = campaign.activate(campaign.Campaign(Path(temporary.name) / 'Studio'))
        self.addCleanup(campaign.activate, previous)
        self.addCleanup(self.drain)
        self.command = patch('ai_provider.command', return_value=['claude', '-p'])
        self.command.start()
        self.addCleanup(self.command.stop)
        core.write_doc('prep/s1', shapes.PREP.new(n=1, title='Session one'))
        core.write_doc('threads/church', shapes.THREAD.new(id='church', title='Church of Auril'))
        core.write_doc(
            'codex/auril-church',
            shapes.CODEX_ENTRY.new(
                id='auril-church', type='place', name='Church of Auril', notes='An old shrine.'
            ),
        )
        core.write_doc('codex/mira', shapes.CODEX_ENTRY.new(id='mira', type='pc', name='Mira'))
        document = recording()
        document['passages'][3].update(kind='banter', confirmed=True)
        core.write_doc('transcripts/' + IDENT, document)

    def drain(self):
        while not core.LANES['claude'].empty():
            core.LANES['claude'].get_nowait()

    def stored(self, name):
        return core.read_json(core.doc_path(name))

    def settle_jobs(self):
        for job in core.JOBS_SERVICE.iter_jobs():
            job['status'] = 'done'
            core.JOBS_SERVICE.save_job(job)

    def draft(self):
        queued = core.start_ledger(IDENT)
        self.assertEqual(queued['kind'], 'thread-ledger')
        self.assertEqual(self.stored('ledger/' + IDENT)['status'], 'running')
        envelope = {'type': 'result', 'is_error': False, 'structured_output': proposal()}
        Path(core.job_file(queued['id'], 'log')).write_text(json.dumps(envelope), encoding='utf-8')
        core.finish_job(queued, 0, '')
        result = self.stored('ledger/' + IDENT)
        self.assertEqual(result['status'], 'review')
        return result

    def test_a_reviewed_subset_updates_records_and_log_once_with_evidence(self):
        ledger = self.draft()
        self.assertEqual(self.stored('threads/church')['status'], 'open')
        self.assertEqual(self.stored('prep/s1')['log']['outcomes'], [])
        selected = [row['id'] for row in ledger['events']]
        core.apply_ledger(IDENT, selected)
        self.assertEqual(self.stored('threads/church')['status'], 'resolved')
        made_id = thread_ledger.thread_id(IDENT, 'new:smuggler')
        made = records.read(campaign.active().data, 'threads', made_id)
        self.assertEqual(
            (made['status'], made['pcs'], made['sessions']), ('open', ['mira'], ['s1'])
        )
        self.assertIn('[0:00:20 · smuggler escaped]', made['detail'])
        self.assertIn('church fell', self.stored('codex/auril-church')['notes'])
        self.assertIn('smuggler escaped', self.stored('prep/s1')['log']['outcomes'][0])
        report = core.loose_threads('mira')
        self.assertEqual([row['id'] for row in report], [made_id])
        self.assertEqual(report[0]['evidence'][0]['at'], 20)
        self.assertEqual(report[0]['evidence'][0]['quote'], 'smuggler escaped')
        before = made['detail']
        core.apply_ledger(IDENT, selected)  # a retried POST is idempotent
        self.assertEqual(records.read(campaign.active().data, 'threads', made_id)['detail'], before)
        self.assertEqual(len(self.stored('prep/s1')['log']['outcomes']), 1)
        with self.assertRaisesRegex(ValueError, 'different choices'):
            core.apply_ledger(IDENT, selected[:-1])

    def test_existing_transcript_can_link_to_a_prep_without_retranscribing(self):
        document = self.stored('transcripts/' + IDENT)
        document['session'] = ''
        core.write_doc('transcripts/' + IDENT, document)
        linked = core.link_transcript_session(IDENT, 's1')
        self.assertEqual(linked['session'], 's1')
        self.assertEqual(self.stored('transcripts/' + IDENT)['segments'], document['segments'])
        with self.assertRaisesRegex(ValueError, 'No such session'):
            core.link_transcript_session(IDENT, 's999')

    def test_rejected_events_do_not_change_their_targets(self):
        ledger = self.draft()
        selected = [row['id'] for row in ledger['events'] if row['kind'] == 'outcome']
        core.apply_ledger(IDENT, selected)
        self.assertEqual(self.stored('threads/church')['status'], 'open')
        self.assertIsNone(
            records.read(
                campaign.active().data, 'threads', thread_ledger.thread_id(IDENT, 'new:smuggler')
            )
        )
        self.assertEqual(len(self.stored('prep/s1')['log']['outcomes']), 1)

    def test_a_long_confirmed_session_advances_window_by_window(self):
        document = self.stored('transcripts/' + IDENT)
        document['segments'] = [
            shapes.TRANSCRIPT_SEGMENT.new(
                start=i * 10, end=i * 10 + 8, text='At the gate ' + 'x' * 900
            )
            for i in range(24)
        ]
        document['passages'] = [
            shapes.TRANSCRIPT_PASSAGE.new(id='p0', first=0, last=23, kind='play', confirmed=True)
        ]
        document['classification']['cursor'] = 24
        core.write_doc('transcripts/' + IDENT, document)
        settings = Path(campaign.active().settings)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({'context_budget_chars': 16_000}))

        job = core.start_ledger(IDENT)
        self.assertEqual(core.LANES['claude'].get_nowait()[0]['id'], job['id'])
        windows = []
        while job:
            windows.append((job['first'], job['stop']))
            envelope = {'type': 'result', 'is_error': False, 'structured_output': {'events': []}}
            Path(core.job_file(job['id'], 'log')).write_text(json.dumps(envelope), encoding='utf-8')
            core.finish_job(job, 0, '')
            ledger = self.stored('ledger/' + IDENT)
            if ledger['status'] == 'running':
                job = core.LANES['claude'].get_nowait()[0]
            else:
                job = None
        self.assertGreater(len(windows), 1)
        self.assertEqual(windows[0][0], 0)
        self.assertEqual(windows[-1][1], 24)
        self.assertTrue(all(a[1] == b[0] for a, b in zip(windows, windows[1:])))
        self.assertEqual(ledger['status'], 'review')
        self.assertEqual(ledger['events'], [])

    def test_a_changed_target_or_reclassified_passage_blocks_apply(self):
        ledger = self.draft()
        selected = [row['id'] for row in ledger['events']]
        church = self.stored('threads/church')
        church['detail'] = 'The GM changed this thread.'
        core.write_doc('threads/church', church)
        with self.assertRaisesRegex(ValueError, 'target changed'):
            core.apply_ledger(IDENT, selected)
        self.assertEqual(self.stored('ledger/' + IDENT)['status'], 'review')
        self.assertEqual(self.stored('prep/s1')['log']['outcomes'], [])
        document = self.stored('transcripts/' + IDENT)
        document['passages'][2]['confirmed'] = False
        core.write_doc('transcripts/' + IDENT, document)
        with self.assertRaisesRegex(ValueError, 'Confirmed play changed'):
            core.apply_ledger(IDENT, selected)

    def test_a_stale_review_can_redraft_against_current_targets_and_play(self):
        old = self.draft()
        self.settle_jobs()
        church = self.stored('threads/church')
        church['detail'] = 'GM revision after the proposal.'
        core.write_doc('threads/church', church)
        with self.assertRaisesRegex(ValueError, 'explicitly redraft'):
            core.start_ledger(IDENT)
        job = core.start_ledger(IDENT, restart=True)
        fresh = self.stored('ledger/' + IDENT)
        self.assertEqual((fresh['cursor'], fresh['events'], fresh['status']), (0, [], 'running'))
        self.assertEqual(
            fresh['base_revs']['threads/church'], core.rev_of(core.doc_path('threads/church'))
        )
        self.assertEqual(job['first'], 0)

        self.settle_jobs()
        fresh['status'] = 'failed'
        core.write_doc('ledger/' + IDENT, fresh)
        document = self.stored('transcripts/' + IDENT)
        document['passages'][2].update(kind='banter', confirmed=True)
        core.write_doc('transcripts/' + IDENT, document)
        core.start_ledger(IDENT, restart=True)
        changed = self.stored('ledger/' + IDENT)
        self.assertNotEqual(changed['source'], old['source'])
        self.assertEqual(changed['events'], [])

    def test_no_unreviewed_or_banter_only_transcript_can_start_a_ledger(self):
        document = self.stored('transcripts/' + IDENT)
        document['passages'][0]['confirmed'] = False
        core.write_doc('transcripts/' + IDENT, document)
        with self.assertRaisesRegex(ValueError, 'Review the unresolved'):
            core.start_ledger(IDENT)
        for passage in document['passages']:
            passage.update(kind='banter', confirmed=True)
        core.write_doc('transcripts/' + IDENT, document)
        with self.assertRaisesRegex(ValueError, 'in-game'):
            core.start_ledger(IDENT)


if __name__ == '__main__':
    unittest.main()
