"""The automatic run: policy tables, then a whole session from a recording to a reviewed draft.

The end-to-end tests stand in for every program a run starts (the transcription worker and Claude Code) with
one fake that answers each request from its prompt. They check the run's promises: it stops at every review,
changes no campaign record before the GM applies, asks for one AI request at a time, never retries a failure
by itself and never starts a recording twice.
"""

import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'DM' / 'tools'))
import auto_run
import auto_run_client
import campaign
import campaign_core as core
import http_routes
import records
import request_workflow
import shapes
import transcription
from test_transcript_classifier import SCRIPT, answer, transcript as sorted_transcript


# ---------- policy ----------


def transcript(**changes):
    return dict(session='s1', sorting='done', sorting_error='', pending=0, play=True) | changes


def ledger(status='review', events=2, error=''):
    return dict(status=status, error=error, events=events)


def recording(ident='rec-0000000000000001', name='part-one.mp4', **changes):
    return (
        dict(
            id=ident,
            name=name,
            transcribing=False,
            transcribe_error='',
            transcript=None,
            ledger=None,
        )
        | changes
    )


def snapshot(*recordings, arc=None, request=None, candidates=('smuggler', 'marshal')):
    return dict(
        session='s1',
        recordings=list(recordings),
        arc=arc,
        request=request,
        candidates=lambda: list(candidates),
    )


def states(decision):
    return {(row['step'], row['recording']): row['state'] for row in decision['steps']}


def finished_recording(**changes):
    return recording(transcript=transcript(), ledger=ledger('applied'), **changes)


class ChoiceTests(unittest.TestCase):
    FILES = [
        dict(id='rec-c', name='c.mp4', modified=300),
        dict(id='rec-b', name='b.mp4', modified=200),
        dict(id='rec-a', name='a.mp4', modified=100),
    ]

    def test_with_a_baseline_a_run_takes_every_newer_recording_oldest_first(self):
        picked = auto_run.new_recordings(self.FILES, {'rec-a'}, since=100)
        self.assertEqual([f['id'] for f in picked], ['rec-b', 'rec-c'])
        self.assertEqual(auto_run.new_recordings(self.FILES, {'rec-a', 'rec-b', 'rec-c'}, 100), [])
        many = [dict(id=f'r{n}', name=f'{n}.mp4', modified=n) for n in range(1, 20)]
        self.assertEqual(
            [f['modified'] for f in auto_run.new_recordings(many, set(), 0, limit=3)], [17, 18, 19]
        )

    def test_without_a_baseline_only_the_newest_is_taken_so_a_back_catalogue_is_safe(self):
        self.assertEqual([f['id'] for f in auto_run.new_recordings(self.FILES, set())], ['rec-c'])
        self.assertEqual(auto_run.new_recordings(self.FILES, {'rec-c'})[0]['id'], 'rec-b')
        self.assertEqual(auto_run.new_recordings([], set()), [])

    def test_the_session_is_the_newest_active_prep_nobody_has_logged_yet(self):
        def prep(n, archived=False, played=False):
            return dict(id=f's{n}', n=n, archived=archived, played=played)

        preps = [prep(1, played=True), prep(2, played=True), prep(3), prep(4, archived=True)]
        self.assertEqual(auto_run.default_session(preps), 's3')
        self.assertEqual(auto_run.default_session(preps[:2]), '')
        self.assertEqual(auto_run.default_session([]), '')

    def test_the_next_session_skips_archived_numbers_and_reuses_a_prep_already_made(self):
        self.assertEqual(auto_run.next_session_number([1, 2], [1, 2], 2), 3)
        self.assertEqual(auto_run.next_session_number([1, 2], [1, 2, 3], 2), 4)  # s3 is archived
        self.assertEqual(auto_run.next_session_number([1, 2, 5], [1, 2, 5], 2), 5)
        self.assertEqual(auto_run.next_session_number([], [], 0), 1)

    def test_arcs_are_asked_for_unplanned_threads_the_session_touched_then_the_stalest(self):
        def row(ident, status, last):
            return dict(id=ident, status=status, last_session=last)

        report = [
            row('forgotten', 'open', ''),
            row('planned', 'planned', 's1'),
            row('old', 'foreshadowed', 's2'),
            row('fresh', 'open', 's5'),
            row('more', 'open', 's3'),
        ]
        self.assertEqual(
            auto_run.arc_candidates(report, 's5', limit=3), ['fresh', 'forgotten', 'old']
        )
        self.assertEqual(auto_run.arc_candidates([], 's5'), [])

    def test_the_pitch_prefers_the_gms_chosen_arcs_and_lists_only_threads_that_exist(self):
        seeds = [
            dict(thread='church', title='Church', kind='twist', pitch='A bell tolls.'),
            dict(thread='gone', title='Gone', kind='twist', pitch='Never mind.'),
        ]
        pitch = auto_run.next_pitch('s4', seeds[:1], ['Church'])
        self.assertIn('S4', pitch)
        self.assertIn('- Church (twist): A bell tolls.', pitch)
        self.assertIn(
            'Move these loose threads forward: A; B.', auto_run.next_pitch('s4', [], ['A', 'B'])
        )
        self.assertEqual(auto_run.next_pitch('s4', [], []), 'Plan the session after S4.')
        self.assertEqual(
            auto_run.request_threads(
                seeds, ['church', 'smuggler', 'marshal'], {'church', 'smuggler'}
            ),
            ['church', 'smuggler'],
        )
        many = [f't{n}' for n in range(30)]
        self.assertEqual(len(auto_run.request_threads([], many, set(many))), auto_run.MAX_THREADS)

    def test_a_failure_that_reads_like_a_limit_says_so_without_hiding_the_message(self):
        note = auto_run.limit_hint('Claude AI usage limit reached|1760000000')
        self.assertIn('Run again after it resets', note)
        self.assertIn('usage limit reached', note)
        self.assertEqual(auto_run.limit_hint('The command failed.'), 'The command failed.')
        self.assertEqual(auto_run.limit_hint(''), '')


class DecisionTests(unittest.TestCase):
    def decide(self, *recordings, explicit=False, **session):
        return auto_run.decide(snapshot(*recordings, **session), explicit)

    def do(self, decision):
        return [(a['do'], a.get('recording', '')) for a in decision['actions']]

    def test_an_unstarted_recording_waits_for_the_gm_or_the_schedule_to_press_run(self):
        decision = self.decide(recording())
        self.assertEqual((decision['state'], decision['actions']), ('ready', []))
        explicit = self.decide(recording(), explicit=True)
        self.assertEqual(self.do(explicit), [('transcribe', 'rec-0000000000000001')])
        self.assertEqual(explicit['state'], 'running')

    def test_a_failed_transcription_stops_the_run_until_it_is_pressed_again(self):
        broken = recording(transcribe_error='No speech was found in this recording.')
        decision = self.decide(broken)
        self.assertEqual((decision['state'], decision['actions']), ('stopped', []))
        self.assertIn('No speech', decision['steps'][0]['note'])
        self.assertEqual(
            self.do(self.decide(broken, explicit=True)), [('transcribe', broken['id'])]
        )

    def test_a_transcription_in_progress_starts_nothing(self):
        decision = self.decide(recording(transcribing=True), explicit=True)
        self.assertEqual((decision['state'], decision['actions']), ('running', []))

    def test_a_new_transcript_is_sorted_without_pressing_run_but_a_failed_sort_is_not_retried(self):
        fresh = recording(transcript=transcript(sorting=''))
        self.assertEqual(self.do(self.decide(fresh)), [('sort', fresh['id'])])
        failed = recording(transcript=transcript(sorting='failed', sorting_error='Cancelled.'))
        stopped = self.decide(failed)
        self.assertEqual((stopped['state'], stopped['actions']), ('stopped', []))
        self.assertEqual(self.do(self.decide(failed, explicit=True)), [('sort', failed['id'])])
        running = self.decide(recording(transcript=transcript(sorting='running')), explicit=True)
        self.assertEqual((running['state'], running['actions']), ('running', []))

    def test_unreviewed_passages_hold_the_run_for_the_gm(self):
        waiting = self.decide(recording(transcript=transcript(pending=4)), explicit=True)
        self.assertEqual((waiting['state'], waiting['actions']), ('waiting', []))
        self.assertEqual(
            auto_run.waiting(waiting['steps']),
            ['Review the passages: part-one.mp4. 4 passages to decide'],
        )

    def test_a_recording_with_no_play_has_nothing_to_record_and_the_run_moves_on(self):
        silent = recording(transcript=transcript(play=False))
        decision = self.decide(silent)
        self.assertEqual(states(decision)[('ledger', silent['id'])], 'done')
        self.assertEqual(self.do(decision), [('arcs', '')])

    def test_a_reviewed_transcript_is_linked_then_drafted_as_a_ledger(self):
        loose = recording(transcript=transcript(session=''))
        self.assertEqual(
            self.do(self.decide(loose)), [('link', loose['id']), ('ledger', loose['id'])]
        )
        linked = recording(transcript=transcript())
        self.assertEqual(self.do(self.decide(linked)), [('ledger', linked['id'])])
        failed = recording(transcript=transcript(), ledger=ledger('failed', error='boom'))
        self.assertEqual(self.decide(failed)['state'], 'stopped')
        self.assertEqual(self.do(self.decide(failed, explicit=True)), [('ledger', failed['id'])])
        self.assertEqual(
            self.decide(recording(transcript=transcript(), ledger=ledger('running')))['state'],
            'running',
        )

    def test_a_ledger_waits_for_review_unless_it_proposed_nothing(self):
        review = self.decide(recording(transcript=transcript(), ledger=ledger('review', 3)))
        self.assertEqual((review['state'], review['actions']), ('waiting', []))
        empty = self.decide(recording(transcript=transcript(), ledger=ledger('review', 0)))
        self.assertEqual(self.do(empty), [('arcs', '')])  # nothing to apply: do not wait for it

    def test_only_one_ai_request_is_started_so_a_limit_stops_the_run_at_the_first(self):
        first, second = (
            recording('rec-0000000000000001'),
            recording('rec-0000000000000002', 'two.mp4'),
        )
        first['transcript'] = transcript(sorting='')
        second['transcript'] = transcript(sorting='')
        decision = self.decide(first, second)
        self.assertEqual(self.do(decision), [('sort', first['id'])])
        self.assertEqual(states(decision)[('sort', second['id'])], 'queued')
        self.assertIn('Waits for the AI request', decision['steps'][5]['note'])

        busy = self.decide(first, recording(second['id'], transcript=transcript(sorting='running')))
        self.assertEqual(busy['actions'], [])  # the second is already using the one slot

        # a recording that cannot start yet is not linked either
        loose = recording(second['id'], transcript=transcript(session=''))
        self.assertEqual(self.do(self.decide(first, loose)), [('sort', first['id'])])

    def test_a_waiting_review_does_not_hold_up_another_recordings_ai_request(self):
        waiting = recording('rec-0000000000000001', transcript=transcript(pending=2))
        fresh = recording('rec-0000000000000002', 'two.mp4', transcript=transcript(sorting=''))
        decision = self.decide(waiting, fresh)
        self.assertEqual(self.do(decision), [('sort', fresh['id'])])

    def test_arcs_wait_for_every_recording_and_skip_when_no_thread_is_loose(self):
        unfinished = self.decide(finished_recording(), recording('rec-0000000000000002', 'two.mp4'))
        self.assertEqual(states(unfinished)[('arcs', '')], 'todo')
        self.assertEqual(unfinished['actions'], [])

        decision = self.decide(finished_recording())
        self.assertEqual(decision['actions'], [{'do': 'arcs', 'threads': ['smuggler', 'marshal']}])
        none = self.decide(finished_recording(), candidates=())
        self.assertEqual(states(none)[('arcs', '')], 'done')
        self.assertEqual(self.do(none), [('draft', '')])

    def test_arc_states_run_review_apply_remove_and_retry(self):
        def arcs(arc, explicit=False):
            decision = self.decide(finished_recording(), arc=arc, explicit=explicit)
            return decision['state'], self.do(decision)

        self.assertEqual(arcs({'status': 'running', 'error': ''}), ('running', []))
        self.assertEqual(arcs({'status': 'review', 'error': ''}), ('waiting', []))
        self.assertEqual(arcs({'status': 'applied', 'error': ''}), ('running', [('draft', '')]))
        self.assertEqual(arcs('removed'), ('running', [('draft', '')]))
        failed = {'status': 'failed', 'error': 'The proposal skipped a thread.'}
        self.assertEqual(arcs(failed), ('stopped', []))
        self.assertEqual(arcs(failed, explicit=True)[1], [('arcs', '')])

    def test_draft_states_and_a_finished_run(self):
        applied = {'status': 'applied', 'error': ''}

        def draft(request, explicit=False):
            decision = self.decide(
                finished_recording(), arc=applied, request=request, explicit=explicit
            )
            return decision['state'], self.do(decision)

        self.assertEqual(draft(None), ('running', [('draft', '')]))
        self.assertEqual(draft({'status': 'doing', 'error': ''}), ('running', []))
        self.assertEqual(draft({'status': 'review', 'error': ''}), ('waiting', []))
        self.assertEqual(draft({'status': 'done', 'error': ''}), ('done', []))
        self.assertEqual(draft('removed'), ('done', []))
        stopped = {'status': 'new', 'error': 'The AI command failed.'}
        self.assertEqual(draft(stopped), ('stopped', []))
        self.assertEqual(draft(stopped, explicit=True), ('running', [('draft', '')]))

    def test_the_overall_state_puts_work_in_progress_before_failures_before_reviews(self):
        def row(state):
            return {'state': state}

        self.assertEqual(auto_run.overall([row('review'), row('failed'), row('queued')]), 'running')
        self.assertEqual(auto_run.overall([row('review'), row('failed')]), 'stopped')
        self.assertEqual(auto_run.overall([row('review'), row('done')]), 'waiting')
        self.assertEqual(auto_run.overall([row('todo'), row('done')]), 'ready')
        self.assertEqual(auto_run.overall([row('done')]), 'done')


# ---------- a whole session ----------


def ledger_answer(prompt):
    lines = dict(
        (ident, text)
        for ident, text in re.findall(r'\[(p\d+) at [\d:]+\] ([^\n"\\]*)', prompt)
        if 'church' in text
    )
    ident, text = next(iter(lines.items()))
    return {
        'events': [
            {
                'id': 'church',
                'kind': 'thread',
                'target': 'church',
                'title': '',
                'status': 'resolved',
                'text': 'The church of Auril was destroyed.',
                'pcs': ['mira'],
                'passage': ident,
                'quote': 'the gate of the church of Auril collapses',
            }
        ]
    }


def arc_answer(prompt):
    chosen = json.loads(re.search(r'"chosen_threads":\s*(\[[^\]]*\])', prompt).group(1))
    return {
        'options': [
            {
                'thread': thread,
                'kind': kind,
                'title': f'A {kind}',
                'summary': f'What the {kind} means for {thread}.',
                'hook': 'The party hears of it.',
                'pitch': f'The {kind} of {thread} reaches the table.',
                'entries': [],
                'pcs': ['mira'],
            }
            for thread in chosen
            for kind in ('resolution', 'twist')
        ]
    }


def session_answer(prompt):
    encounter = dict(
        creatures=[{'name': 'Harbour watcher', 'count': 2}],
        difficulty='moderate',
        terrain='Slippery quay',
        tactics='Delay the party',
        resolution='Talk or fight',
    )
    scenes = [
        dict(
            id=f'scene-{n}',
            title=f'Scene {n}',
            purpose='Move the story on',
            where='The harbour',
            map='',
            area=0,
            npcs=[],
            encounter=encounter,
            clues=[],
            read_aloud='The tide rises.',
            notes='',
        )
        for n in range(1, 4)
    ]
    return dict(
        summary='The party follows the smuggler.',
        recap='Last time, the church fell.',
        goals=['Find the smuggler'],
        maps=[],
        entries=[],
        threads=[],
        thread_changes=[],
        scenes=scenes,
        handouts=[],
        loot=[],
        checklist=['Print the notice'],
    )


class Everything:
    """Stands in for every program a run starts, answering each from its prompt."""

    ANSWERS = {
        'classify': answer,
        'thread-ledger': ledger_answer,
        'arc-options': arc_answer,
        'session': session_answer,
    }

    def __init__(self):
        self.seen = []  # (kind, prompt) in the order they were asked
        self.fail = None  # a message the AI requests exit with
        self.exit = 0

    def kind(self, text):
        for kind, marker in (
            ('transcribe', '{"recording"'),
            ('classify', 'KNOWN TABLE LORE'),
            ('thread-ledger', 'evidence-backed ledger'),
            ('arc-options', 'loose story threads go next'),
            ('session', 'Plan one tabletop session'),
        ):
            if marker in text:
                return kind
        raise AssertionError('an unexpected program was started: ' + text[:80])

    def start(self, cmd, cwd, env, log, has_stdin):
        self.log = log
        return self

    def feed(self, text):
        kind = self.kind(text)
        self.seen.append((kind, text))
        self.exit = 0
        if kind == 'transcribe':
            request = json.loads(text)
            segments = [
                {'start': i * 5, 'end': i * 5 + 4, 'text': line} for i, line in enumerate(SCRIPT)
            ]
            Path(request['out']).write_text(
                json.dumps({'language': 'en', 'duration': 40, 'segments': segments})
            )
            print('PROGRESS 100%', file=self.log)
        elif self.fail:
            self.log.write(self.fail)
            self.exit = 1
        else:
            envelope = {
                'type': 'result',
                'is_error': False,
                'structured_output': self.ANSWERS[kind](text),
                'usage': {'input_tokens': 10, 'output_tokens': 4},
                'total_cost_usd': 0.25,
            }
            self.log.write(json.dumps(envelope))

    def wait(self):
        return self.exit

    def terminate(self):
        pass

    def asked(self):
        return [kind for kind, _ in self.seen]


class RunCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous = campaign.activate(campaign.Campaign(self.root / 'Studio'))
        self.addCleanup(campaign.activate, previous)
        self.addCleanup(patch.stopall)
        self.fake = Everything()
        patch.object(core.JOBS_SERVICE, 'runner', self.fake).start()
        patch('ai_provider.command', return_value=['claude', '-p']).start()
        patch.object(transcription.FasterWhisper, 'problem', return_value='').start()
        self.addCleanup(self.drain)
        self.folder = self.root / 'recordings'
        self.folder.mkdir()
        self.file = self.folder / 'session-one.mp4'
        self.file.write_bytes(b'not really a video' * 100)
        core.write_doc('prep/s1', shapes.PREP.new(n=1, title='Session one'))
        for ident, status in (
            ('church', 'open'),
            ('smuggler', 'foreshadowed'),
            ('marshal', 'open'),
        ):
            core.write_doc(
                f'threads/{ident}',
                shapes.THREAD.new(id=ident, title=ident.title(), status=status, detail='Old.'),
            )
        core.write_doc('codex/mira', shapes.CODEX_ENTRY.new(id='mira', type='pc', name='Mira'))
        self.ident = transcription.recording(str(self.file))['id']

    def drain(self):
        for lane in ('transcribe', 'claude'):
            while not core.LANES[lane].empty():
                core.LANES[lane].get_nowait()

    def work(self):
        """Run every queued job, as the server's workers would, and say which kinds ran."""
        ran = []
        while True:
            for lane in ('transcribe', 'claude'):
                if not core.LANES[lane].empty():
                    job, cmd, stdin = core.LANES[lane].get_nowait()
                    core.execute_job(job, cmd, stdin)
                    ran.append(job['kind'])
                    break
            else:
                return ran

    def press(self, **choice):
        return core.start_auto_run(str(self.folder), **choice)

    def report(self):
        return core.auto_run_current()

    def stored(self, name):
        return core.read_json(core.doc_path(name))

    def thread(self, ident):
        return records.read(campaign.active().data, 'threads', ident)

    def play(self, banter='remember the horse'):
        """The GM's review of the sorted passages: the play is confirmed and the horse form is banter."""
        decisions = [
            {'id': 'p0', 'confirmed': True},
            {'id': 'p2', 'confirmed': True, 'remember': banter},
            {'id': 'p4', 'kind': 'banter', 'confirmed': True},
            {'id': 'p5', 'confirmed': True},
        ]
        return core.review_passages(self.ident, decisions)

    def apply_request(self):
        box = core.read_json(core.doc_path('inbox'))
        item = box['items'][0]
        request_workflow.apply(
            item, core.request_read, core.commit_docs, box, [], core.party_level()
        )


class WholeSessionTests(RunCase):
    def test_a_recording_becomes_a_reviewed_draft_stopping_at_every_review(self):
        started = self.press()
        self.assertEqual((started['state'], started['session']), ('running', 's1'))
        self.assertEqual(self.work(), ['transcribe', 'classify'])

        # 1. The passages wait for the GM; nothing past sorting has been asked.
        report = self.report()
        self.assertEqual(report['state'], 'waiting')
        self.assertEqual(
            report['waiting'], ['Review the passages: session-one.mp4. 4 passages to decide']
        )
        self.assertEqual(self.fake.asked(), ['transcribe', 'classify'])
        self.assertEqual(self.stored('transcripts/' + self.ident)['session'], 's1')

        # 2. Reviewing them carries the run on to the ledger by itself.
        self.play()
        self.assertEqual(self.work(), ['thread-ledger'])
        report = self.report()
        self.assertEqual(report['state'], 'waiting')
        self.assertEqual(
            report['waiting'], ['Thread ledger: session-one.mp4. 1 proposed change to review']
        )
        self.assertEqual(self.thread('church')['status'], 'open')  # a draft changes nothing
        self.assertNotIn('arc-options', self.fake.asked())
        ledger = self.stored('ledger/' + self.ident)
        self.assertEqual((ledger['status'], len(ledger['events'])), ('review', 1))

        # 3. Applying the ledger asks for arc options on the loose threads, not the resolved one.
        core.apply_ledger(self.ident, [ledger['events'][0]['id']])
        self.assertEqual(self.thread('church')['status'], 'resolved')
        self.assertEqual(self.work(), ['arc-options'])
        [arc] = [core.read_json(core.doc_path('arcs/' + i)) for i in core.list_docs('arcs')]
        self.assertEqual(
            (arc['status'], sorted(arc['threads'])), ('review', ['marshal', 'smuggler'])
        )
        self.assertEqual(self.thread('marshal')['status'], 'open')  # still unplanned
        self.assertEqual(self.report()['state'], 'waiting')
        self.assertNotIn('session', self.fake.asked())

        # 4. Applying the arcs drafts the next session, in a prep the run made.
        core.apply_arc(arc['id'], [{'option': 'o1'}, {'option': 'o3'}])
        self.assertEqual(self.thread('marshal')['status'], 'planned')
        self.assertEqual(self.work(), ['request-draft'])
        report = self.report()
        self.assertEqual((report['state'], report['next_session']), ('waiting', 's2'))
        prep = self.stored('prep/s2')
        self.assertEqual(
            (prep['title'], prep['scenes']), ('Session 2', [])
        )  # a draft is not applied
        [item] = core.read_json(core.doc_path('inbox'))['items']
        self.assertEqual(
            (item['kind'], item['status'], item['session']), ('session', 'review', 's2')
        )
        self.assertIn('The resolution of marshal reaches the table.', item['text'])
        self.assertEqual(sorted(item['settings']['threads']), ['marshal', 'smuggler'])

        # 5. Applying the draft finishes the run.
        self.apply_request()
        self.assertEqual(len(self.stored('prep/s2')['scenes']), 3)
        core.continue_auto_run()
        report = self.report()
        self.assertEqual((report['state'], bool(report['finished'])), ('done', True))
        self.assertEqual(report['waiting'], [])
        self.assertEqual(
            self.fake.asked(),
            ['transcribe', 'classify', 'thread-ledger', 'arc-options', 'session'],
        )
        self.assertEqual(report['usage']['requests'], 4)
        self.assertEqual(report['usage']['input_tokens'], 40)
        self.assertEqual(report['usage']['cost_usd'], 1.0)

    def test_the_banter_a_gm_confirmed_is_never_shown_to_the_ledger(self):
        self.press()
        self.work()
        self.play()
        self.work()
        ledger_prompt = next(text for kind, text in self.fake.seen if kind == 'thread-ledger')
        self.assertIn('church of Auril collapses', ledger_prompt)
        self.assertNotIn('horse form', ledger_prompt)
        self.assertNotIn('pizza', ledger_prompt)

    def test_a_finished_run_leaves_the_recording_alone_and_the_next_press_starts_fresh_work(self):
        self.press()
        self.work()
        self.play()
        self.work()
        events = self.stored('ledger/' + self.ident)['events']
        core.apply_ledger(self.ident, [events[0]['id']])
        self.work()
        [arc_id] = core.list_docs('arcs')
        core.apply_arc(arc_id, [{'option': 'o1'}])
        self.work()
        self.apply_request()
        core.continue_auto_run()
        asked = list(self.fake.asked())

        with self.assertRaisesRegex(ValueError, 'no new recordings'):
            self.press()  # the same file is processed: nothing is transcribed or asked twice
        self.assertEqual(self.fake.asked(), asked)

        newer = self.folder / 'session-two.mp4'
        newer.write_bytes(b'another recording' * 100)
        os.utime(newer, (self.file.stat().st_atime + 100, self.file.stat().st_mtime + 100))
        plan = core.auto_run_plan(str(self.folder))
        self.assertEqual(plan['session'], 's2')  # the newest prep nobody has logged
        self.assertEqual([f['name'] for f in plan['files'] if f['chosen']], ['session-two.mp4'])
        self.assertEqual(
            [f['name'] for f in plan['files'] if f['transcribed']], ['session-one.mp4']
        )
        second = self.press()
        self.assertEqual((second['session'], second['state']), ('s2', 'running'))
        self.assertEqual(len(core.read_auto_runs()), 2)


class StopTests(RunCase):
    def test_a_failure_stops_the_run_and_nothing_retries_it_until_run_is_pressed_again(self):
        self.fake.fail = 'Claude AI usage limit reached|1760000000'
        self.press()
        self.assertEqual(self.work(), ['transcribe', 'classify'])  # the window failed; nothing else
        report = self.report()
        self.assertEqual(report['state'], 'stopped')
        sort = next(row for row in report['steps'] if row['step'] == 'sort')
        self.assertEqual(sort['state'], 'failed')
        self.assertIn('Run again after it resets', sort['note'])
        self.assertTrue(core.LANES['claude'].empty())
        core.continue_auto_run()  # a hook never retries a failure
        self.assertTrue(core.LANES['claude'].empty())

        self.fake.fail = None
        resumed = self.press()
        self.assertEqual(resumed['state'], 'running')
        self.assertEqual(self.work(), ['classify'])
        self.assertEqual(self.report()['state'], 'waiting')
        self.assertEqual(self.fake.asked().count('transcribe'), 1)  # the recording was not redone

    def test_a_failed_transcription_is_reported_and_retried_only_on_request(self):
        self.fake.exit = 0
        with patch.object(
            self.fake, 'feed', side_effect=lambda text: setattr(self.fake, 'exit', 1)
        ):
            self.press()
            self.assertEqual(self.work(), ['transcribe'])
        report = self.report()
        self.assertEqual(report['state'], 'stopped')
        self.assertEqual(
            next(r for r in report['steps'] if r['step'] == 'transcribe')['state'], 'failed'
        )
        core.continue_auto_run()
        self.assertTrue(core.LANES['transcribe'].empty())
        self.press()
        self.assertEqual(self.work(), ['transcribe', 'classify'])

    def test_only_one_ai_request_runs_at_a_time_across_recordings(self):
        second = self.folder / 'session-one-part-two.mp4'
        second.write_bytes(b'the second half' * 100)
        os.utime(second, (self.file.stat().st_atime + 50, self.file.stat().st_mtime + 50))
        ids = [transcription.recording(str(p))['id'] for p in (self.file, second)]
        self.press(ids=ids)
        job, cmd, stdin = core.LANES['transcribe'].get_nowait()
        core.execute_job(job, cmd, stdin)  # the first recording is transcribed
        self.assertEqual(core.LANES['claude'].qsize(), 1)  # and its sorting is queued
        job, cmd, stdin = core.LANES['transcribe'].get_nowait()
        core.execute_job(job, cmd, stdin)  # the second: its sorting waits for the first
        self.assertEqual(core.LANES['claude'].qsize(), 1)
        steps = {(r['subject'], r['step']): r for r in self.report()['steps']}
        waits = steps[('session-one-part-two.mp4', 'sort')]
        self.assertEqual((waits['state'], waits['note']), ('queued', auto_run.BUSY_NOTE))
        job, cmd, stdin = core.LANES['claude'].get_nowait()
        core.execute_job(job, cmd, stdin)  # the first finishes and the second is started
        self.assertEqual(core.LANES['claude'].qsize(), 1)

    def test_a_hook_that_cannot_start_a_step_says_why_and_never_fails_the_job(self):
        self.press()
        with patch.object(core, 'start_classification', side_effect=ValueError('No engine today.')):
            self.assertEqual(self.work(), ['transcribe'])
        job = core.JOBS_SERVICE.iter_jobs().__next__()
        self.assertEqual(job['status'], 'done')
        self.assertEqual(self.report()['note'], 'No engine today.')
        with patch.object(core, 'active_auto_run', side_effect=RuntimeError('broken')):
            core.continue_auto_run()  # an unexpected error is logged, never raised
        self.press()  # pressing Run clears the note and carries on
        self.assertEqual(self.report()['note'], '')
        self.assertEqual(self.work(), ['classify'])

    def test_ending_a_run_stops_following_it_and_frees_the_next_one(self):
        started = self.press()
        ended = core.end_auto_run(started['id'])
        self.assertEqual(ended['state'], 'ended')
        self.assertEqual(self.work(), ['transcribe'])  # what it started still finishes
        self.assertTrue(core.LANES['claude'].empty())  # but nothing follows it
        self.assertEqual(core.end_auto_run(started['id'])['state'], 'ended')
        with self.assertRaises(LookupError):
            core.end_auto_run('run-00000000')
        self.assertIsNone(core.active_auto_run())


class StartTests(RunCase):
    def test_a_run_needs_new_recordings_a_session_and_a_working_engine(self):
        with self.assertRaisesRegex(ValueError, 'Choose the folder'):
            core.start_auto_run('')
        with self.assertRaises(ValueError):
            core.start_auto_run(str(self.root / 'nowhere'))
        with self.assertRaisesRegex(ValueError, 'Choose the session prep'):
            self.press(session='s9')
        core.write_doc(
            'prep/s1',
            dict(self.stored('prep/s1'), log=dict(summary='Played.', notes='', outcomes=[])),
        )
        with self.assertRaisesRegex(ValueError, 'Choose the session prep'):
            self.press()  # every prep has been logged: nothing to guess
        self.assertEqual(self.press(session='s1')['session'], 's1')

    def test_the_engine_is_checked_before_a_run_is_made(self):
        with patch.object(transcription.FasterWhisper, 'problem', return_value='Install it.'):
            with self.assertRaisesRegex(ValueError, 'Install it'):
                self.press()
        self.assertEqual(core.read_auto_runs(), [])

    def test_recordings_can_be_chosen_but_only_from_the_folder_and_within_the_limit(self):
        with self.assertRaisesRegex(ValueError, 'Choose between 1 and'):
            self.press(ids=['rec-ffffffffffffffff'])
        with self.assertRaisesRegex(ValueError, 'Choose between 1 and'):
            self.press(ids=[])
        self.assertEqual(self.press(ids=[self.ident])['state'], 'running')

    def test_a_second_press_carries_the_run_on_and_never_adds_a_second_run(self):
        first = self.press()
        again = self.press()
        self.assertEqual(again['id'], first['id'])
        self.assertEqual(
            core.LANES['transcribe'].qsize(), 1
        )  # the transcription is not queued twice
        self.assertEqual(len(core.read_auto_runs()), 1)

    def test_a_recording_that_changed_after_the_run_began_is_refused(self):
        stale = shapes.AUTO_RUN_RECORDING.new(
            id='rec-0000000000000000', name=self.file.name, path=str(self.file)
        )
        run = shapes.AUTO_RUN.new(id='run-0123abcd', session='s1', recordings=[stale], created=1)
        core.write_doc('auto-runs/run-0123abcd', run)
        with self.assertRaisesRegex(ValueError, 'changed since the run began'):
            core.advance_auto_run(run, explicit=True)
        self.assertTrue(core.LANES['transcribe'].empty())

    def test_a_transcript_made_by_hand_is_taken_over_at_the_step_it_has_reached(self):
        core.start_transcription(transcription.recording(str(self.file)), 's1')
        core.start_classification  # the GM sorts and reviews it without a run
        self.work()
        core.start_classification(self.ident)
        self.work()
        self.play()
        self.assertEqual(self.fake.asked(), ['transcribe', 'classify'])
        report = self.press(ids=[self.ident])  # already transcribed, sorted and reviewed
        self.assertEqual(report['state'], 'running')
        self.assertEqual(self.work(), ['thread-ledger'])
        self.assertEqual(self.fake.asked().count('transcribe'), 1)

    def test_the_plan_counts_the_sorting_requests_a_transcript_still_needs(self):
        core.write_doc(
            'transcripts/' + self.ident, sorted_transcript(ident=self.ident, session='s1')
        )
        plan = core.auto_run_plan(str(self.folder), ids=[self.ident])
        self.assertEqual((plan['sorting_requests'], plan['chosen']), (1, [self.ident]))


class ServerCase(RunCase):
    """A run with the application's HTTP server listening on a free port."""

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


class RouteTests(ServerCase):
    def test_a_run_is_planned_started_read_and_ended_through_the_routes(self):
        status, nothing = self.call('/api/auto-run')
        self.assertEqual((status, nothing['run']), (200, None))
        self.assertIn('available', nothing['engine'])

        query = '?path=' + urllib.parse.quote(str(self.folder))
        status, plan = self.call('/api/auto-run/plan' + query)
        self.assertEqual((status, plan['session'], plan['chosen']), (200, 's1', [self.ident]))
        self.assertEqual(plan['sorting_requests'], 0)  # known only once it has been transcribed
        self.assertEqual(plan['files'][0]['chosen'], True)
        self.assertEqual(core.read_auto_runs(), [])  # a plan starts nothing

        status, run = self.call('/api/auto-run', 'POST', {'path': str(self.folder)})
        self.assertEqual((status, run['state'], run['session']), (200, 'running', 's1'))
        self.work()
        status, current = self.call('/api/auto-run')
        self.assertEqual((current['run']['id'], current['run']['state']), (run['id'], 'waiting'))

        status, ended = self.call(f'/api/auto-run/{run["id"]}/end', 'POST')
        self.assertEqual((status, ended['state']), (200, 'ended'))
        self.assertEqual(self.call('/api/auto-run/run-00000000/end', 'POST')[0], 404)
        self.assertEqual(self.call('/api/auto-run/not-a-run/end', 'POST')[0], 400)

    def test_bad_requests_are_refused_without_starting_anything(self):
        for body in (
            {},
            {'path': 7},
            {'path': str(self.folder), 'session': ['s1']},
            {'path': str(self.folder), 'session': 's' * 80},
            {'path': str(self.folder), 'files': 'all'},
            {'path': str(self.folder), 'files': [1]},
            {'path': str(self.folder), 'files': ['x'] * 20},
            {'path': str(self.folder), 'session': 's4'},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.call('/api/auto-run', 'POST', body)[0], 400)
        self.assertEqual(core.read_auto_runs(), [])
        self.assertTrue(core.LANES['transcribe'].empty())
        # Runs belong to the application, not to the generic document save.
        self.assertEqual(self.call('/api/doc/auto-runs/run-00000000', 'PUT', {})[0], 403)


class ClientTests(ServerCase):
    def client(self, *argv):
        out = io.StringIO()
        code = auto_run_client.main(['--url', self.url, *argv], out)
        return code, out.getvalue()

    def test_the_command_line_presses_run_and_says_what_waits_for_the_gm(self):
        code, text = self.client('--status')
        self.assertEqual((code, text.strip()), (0, 'There is no automatic run yet.'))

        code, text = self.client('--folder', str(self.folder))
        self.assertEqual(code, 0)
        self.assertIn('Run for session s1: running.', text)
        self.work()
        code, text = self.client('--status')
        self.assertEqual(code, 0)
        self.assertIn('Run for session s1: waiting.', text)
        self.assertIn('Review the passages: session-one.mp4. 4 passages to decide', text)
        self.assertIn(f'Review it at {self.url}/#/recordings', text)

        code, text = self.client('--status', '--json')
        self.assertEqual(json.loads(text)['state'], 'waiting')

        core.end_auto_run(core.active_auto_run()['id'])
        code, text = self.client('--folder', str(self.folder))
        self.assertEqual((code, text.strip()), (0, 'There are no new recordings in this folder.'))

    def test_a_stopped_run_exits_with_a_status_a_schedule_can_notice(self):
        self.fake.fail = 'Claude AI usage limit reached|1760000000'
        self.client('--folder', str(self.folder))
        self.work()
        code, text = self.client('--status')
        self.assertEqual(code, 2)
        self.assertIn('stopped.', text)
        self.assertIn('Run again after it resets', text)

    def test_a_missing_server_and_a_refused_request_have_their_own_statuses(self):
        out = io.StringIO()
        self.assertEqual(auto_run_client.main(['--url', 'http://127.0.0.1:9'], out), 3)
        self.assertIn('is not running', out.getvalue())
        code, text = self.client('--folder', str(self.root / 'nowhere'))
        self.assertEqual(code, 4)
        self.assertIn('refused', text)


class StoredRunTests(unittest.TestCase):
    def test_a_run_is_one_small_record_built_from_its_shape(self):
        run = shapes.AUTO_RUN.new(
            id='run-0123abcd',
            session='s1',
            recordings=[shapes.AUTO_RUN_RECORDING.new(id='rec-0123456789abcdef', name='a.mp4')],
        )
        self.assertEqual(shapes.AUTO_RUN.problems(run), [])
        self.assertEqual((run['arc'], run['request'], run['finished']), ('', '', 0))


if __name__ == '__main__':
    unittest.main()
