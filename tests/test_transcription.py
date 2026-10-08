"""Local transcription: settings, recording checks, bounded transcripts and the job lifecycle.

Engines are faked: a stand-in `faster_whisper` package, and stand-in whisper.cpp and ffmpeg programs.
No model is downloaded and no real recording is read.
"""

import hashlib
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import campaign
import campaign_core as core
import config
import http_routes
import shapes
import transcription

FAKE_FASTER_WHISPER = """
import os, time

class Info:
    language = 'en'
    duration = 12.0

class Segment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text

class WhisperModel:
    def __init__(self, model, local_files_only=True):
        if model == 'remote-only' and local_files_only:
            raise RuntimeError('not found in the local cache')

    def transcribe(self, path, language=None, vad_filter=False):
        with open(path, 'rb') as stream:  # the recording is read where it lies
            stream.read(1)
        return iter([Segment(0.0, 4.5, '  The party reaches\\n the gate. '), Segment(4.5, 12, 'Mira opens it.')]), Info()
"""

FAKE_FFMPEG = """
import sys
with open(sys.argv[-1], 'wb') as audio:
    audio.write(b'RIFF')
"""

FAKE_WHISPER_CPP = """
import json, os, sys, time
args = sys.argv[1:]
prefix = args[args.index('-of') + 1]
if os.environ.get('FAKE_WHISPER_HOLD'):
    with open(os.environ['FAKE_WHISPER_HOLD'], 'w') as marker:
        marker.write(str(os.getpid()))
    time.sleep(60)
print('whisper_print_progress_callback: progress =  50%', flush=True)
rows = [
    {'offsets': {'from': 0, 'to': 4500}, 'text': ' The party reaches the gate.'},
    {'offsets': {'from': 4500, 'to': 9000}, 'text': ' Mira opens it.'},
]
with open(prefix + '.json', 'w') as out:
    json.dump({'result': {'language': 'en'}, 'transcription': rows}, out)
"""


def launcher(folder, name, script):
    """A program that runs a Python script, with the right kind of file for this platform."""
    script_path = folder / (name + '.py')
    script_path.write_text(script, encoding='utf-8')
    if os.name == 'nt':
        path = folder / (name + '.cmd')
        path.write_text(f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n')
    else:
        path = folder / name
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script_path}" "$@"\n')
        path.chmod(0o755)
    return str(path)


def wait_for(path, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return
        time.sleep(0.05)
    raise AssertionError(f'{path} never appeared')


class TranscriptionCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous = campaign.activate(campaign.Campaign(self.root / 'Studio'))
        self.addCleanup(campaign.activate, previous)
        self.recordings = self.root / 'Session recordings'
        self.recordings.mkdir()
        self.recording = self.recordings / 'session-one.mp4'
        self.recording.write_bytes(b'not really a video' * 100)
        self.tools = self.root / 'tools'
        (self.tools / 'faster_whisper').mkdir(parents=True)
        (self.tools / 'faster_whisper' / '__init__.py').write_text(FAKE_FASTER_WHISPER)
        patched = {
            'PYTHONPATH': os.pathsep.join([str(self.tools), os.environ.get('PYTHONPATH', '')])
        }
        patch.dict(os.environ, patched).start()
        patch.object(sys, 'path', [str(self.tools)] + sys.path).start()
        self.addCleanup(patch.stopall)
        importlib.invalidate_caches()
        self.addCleanup(self.drain)
        self.configure()

    def drain(self):
        while not core.LANES['transcribe'].empty():
            core.LANES['transcribe'].get_nowait()

    def configure(self, **changes):
        options = {**config.DEFAULTS['transcription'], **changes}
        path = Path(campaign.active().settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'transcription': options}))
        return options

    def queue(self, session=''):
        rec = transcription.recording(str(self.recording))
        return core.start_transcription(rec, session)

    def run_next(self):
        job, cmd, stdin = core.LANES['transcribe'].get_nowait()
        core.execute_job(job, cmd, stdin)
        return self.job(job['id'])

    def job(self, job_id):
        return json.loads(Path(core.job_file(job_id)).read_text(encoding='utf-8'))

    def stored(self, transcript_id):
        return core.read_json(core.doc_path('transcripts/' + transcript_id))

    def scratch(self, transcript_id):
        return transcription.staging(campaign.active().jobs, transcript_id)


class SettingsAndRecordingTests(TranscriptionCase):
    def test_settings_choose_an_engine_and_a_model_never_a_key(self):
        cleaned = transcription.clean_settings({'provider': 'whisper-cpp', 'model': ' m.bin '})
        self.assertEqual(
            cleaned,
            {
                'provider': 'whisper-cpp',
                'model': 'm.bin',
                'language': '',
                'executable': '',
                'ffmpeg': '',
                'allow_download': False,
            },
        )
        self.assertTrue(
            transcription.clean_settings({'allow_download': True, 'language': 'EN'})[
                'allow_download'
            ]
        )
        self.assertEqual(transcription.clean_settings({'language': 'EN'})['language'], 'en')
        for bad in (
            {'provider': 'cloud'},
            {'language': 'english'},
            {'model': 'a\x00b'},
            {'model': 'x' * 1025},
            'small',
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                transcription.clean_settings(bad)
        self.assertEqual(
            transcription.clean_settings({'allow_download': 'yes'})['allow_download'], False
        )

    def test_only_an_existing_non_empty_media_file_is_a_recording(self):
        found = transcription.recording(str(self.recording))
        self.assertEqual((found['name'], found['size']), ('session-one.mp4', 1800))
        notes = self.recordings / 'notes.txt'
        notes.write_text('words')
        empty = self.recordings / 'empty.mp4'
        empty.write_bytes(b'')
        for bad in (
            str(notes),
            str(empty),
            str(self.recordings),
            str(self.recordings / 'no.mp4'),
            '',
            None,
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                transcription.recording(bad)

    def test_a_hand_edited_unknown_engine_is_reported_not_raised(self):
        found = transcription.status({**config.DEFAULTS['transcription'], 'provider': 'cloud'})
        self.assertFalse(found['available'])
        self.assertIn('supported', found['problem'])

    def test_a_file_keeps_its_id_and_a_changed_file_gets_a_new_one(self):
        first = transcription.recording(str(self.recording))
        self.assertEqual(first['id'], transcription.recording(str(self.recording))['id'])
        self.recording.write_bytes(b'longer than before' * 100)
        self.assertNotEqual(first['id'], transcription.recording(str(self.recording))['id'])
        self.assertRegex(first['id'], transcription.ID)

    def test_a_folder_lists_only_recordings_newest_first(self):
        older = self.recordings / 'older.m4a'
        older.write_bytes(b'x')
        os.utime(older, (1_700_000_000, 1_700_000_000))
        (self.recordings / 'notes.txt').write_text('skip')
        (self.recordings / 'nested').mkdir()
        (self.recordings / 'nested' / 'inner.mp4').write_bytes(b'x')
        names = [item['name'] for item in transcription.recordings(str(self.recordings))]
        self.assertEqual(names, ['session-one.mp4', 'older.m4a'])
        self.assertEqual(
            [item['name'] for item in transcription.recordings(str(self.recording))],
            ['session-one.mp4'],
        )
        with self.assertRaisesRegex(ValueError, 'existing folder'):
            transcription.recordings(str(self.root / 'missing'))

    def test_segments_are_cleaned_ordered_and_bounded(self):
        raw = [
            {'start': 5, 'end': 6, 'text': ' later  words '},
            {'start': 0, 'end': 1, 'text': 'first'},
            {'start': 2, 'end': 1, 'text': 'backwards end'},
            {'start': -1, 'end': 1, 'text': 'negative'},
            {'start': 1, 'end': 2, 'text': '   '},
            {'start': True, 'end': 2, 'text': 'boolean'},
            {'start': float('nan'), 'end': 2, 'text': 'nan'},
            'not a row',
        ]
        kept, truncated = transcription.clean_segments(raw)
        self.assertEqual(
            [(s['start'], s['end'], s['text']) for s in kept],
            [(0, 1, 'first'), (2, 2, 'backwards end'), (5, 6, 'later words')],
        )
        self.assertFalse(truncated)
        for segment in kept:
            self.assertEqual(shapes.TRANSCRIPT_SEGMENT.problems(segment), [])
        with patch.object(transcription, 'MAX_SEGMENTS', 2):
            kept, truncated = transcription.clean_segments(raw)
        self.assertEqual((len(kept), truncated), (2, True))
        words = [{'start': i, 'end': i + 1, 'text': 'word'} for i in range(3)]
        with patch.object(transcription, 'MAX_TEXT_CHARS', 10):
            kept, truncated = transcription.clean_segments(words)
        self.assertEqual((len(kept), truncated), (2, True))
        with self.assertRaises(ValueError):
            transcription.clean_segments({'segments': []})

    def test_a_result_without_speech_is_an_error_not_an_empty_transcript(self):
        job = {
            'transcript': 'rec-0123456789abcdef',
            'recording': transcription.recording(str(self.recording)),
            'engine': {'provider': 'faster-whisper', 'model': 'small'},
        }
        with self.assertRaisesRegex(ValueError, 'No speech'):
            transcription.build(job, {'segments': [{'start': 0, 'end': 1, 'text': ' '}]})

    def test_whisper_cpp_needs_its_program_ffmpeg_and_a_model(self):
        engine = transcription.PROVIDERS['whisper-cpp']
        base = {**config.DEFAULTS['transcription'], 'provider': 'whisper-cpp'}
        self.assertIn(
            'whisper.cpp', engine.problem({**base, 'executable': str(self.root / 'none')})
        )
        program = launcher(self.tools, 'whisper-cli', FAKE_WHISPER_CPP)
        ffmpeg = launcher(self.tools, 'ffmpeg', FAKE_FFMPEG)
        options = {**base, 'executable': program, 'ffmpeg': str(self.root / 'none')}
        self.assertIn('ffmpeg', engine.problem(options))
        options['ffmpeg'] = ffmpeg
        self.assertIn('model file', engine.problem(options))
        model = self.root / 'ggml-small.bin'
        model.write_bytes(b'x')
        self.assertEqual(engine.problem({**options, 'model': str(model)}), '')


class WhisperCppEngineTests(TranscriptionCase):
    def test_audio_is_extracted_then_transcribed_and_timestamps_become_seconds(self):
        model = self.root / 'ggml-small.bin'
        model.write_bytes(b'x')
        options = {
            **config.DEFAULTS['transcription'],
            'provider': 'whisper-cpp',
            'model': str(model),
            'executable': launcher(self.tools, 'whisper-cli', FAKE_WHISPER_CPP),
            'ffmpeg': launcher(self.tools, 'ffmpeg', FAKE_FFMPEG),
        }
        work = self.root / 'scratch'
        work.mkdir()
        reports = []
        result = transcription.PROVIDERS['whisper-cpp'].run(
            str(self.recording),
            options,
            str(work),
            lambda fraction, label='': reports.append(label),
        )
        self.assertEqual(
            [(s['start'], s['end'], s['text'].strip()) for s in result['segments']],
            [(0.0, 4.5, 'The party reaches the gate.'), (4.5, 9.0, 'Mira opens it.')],
        )
        self.assertEqual((result['language'], result['duration']), ('en', 9.0))
        self.assertEqual(reports[0], 'extracting the audio')
        self.assertIn('transcribing', reports)

    def test_a_program_that_fails_is_reported_with_its_own_words(self):
        failing = launcher(self.tools, 'ffmpeg-fails', 'import sys\nsys.exit("no audio stream")')
        model = self.root / 'ggml-small.bin'
        model.write_bytes(b'x')
        options = {
            **config.DEFAULTS['transcription'],
            'provider': 'whisper-cpp',
            'model': str(model),
            'executable': launcher(self.tools, 'whisper-cli', FAKE_WHISPER_CPP),
            'ffmpeg': failing,
        }
        work = self.root / 'scratch'
        work.mkdir()
        with self.assertRaisesRegex(ValueError, 'no audio stream'):
            transcription.PROVIDERS['whisper-cpp'].run(
                str(self.recording), options, str(work), lambda *_: None
            )


class TranscriptionJobTests(TranscriptionCase):
    def test_a_finished_job_stores_the_transcript_and_leaves_the_recording_alone(self):
        before = (self.recording.read_bytes(), self.recording.stat().st_mtime_ns)
        digest = hashlib.sha256(before[0]).hexdigest()
        queued = self.queue()
        self.assertEqual((queued['lane'], queued['kind']), ('transcribe', 'transcribe'))

        done = self.run_next()

        self.assertEqual(done['status'], 'done', done.get('note'))
        document = self.stored(queued['transcript'])
        self.assertEqual(shapes.TRANSCRIPT.problems(document), [])
        self.assertEqual(
            [(s['start'], s['end'], s['text']) for s in document['segments']],
            [(0.0, 4.5, 'The party reaches the gate.'), (4.5, 12.0, 'Mira opens it.')],
        )
        self.assertEqual(
            (document['title'], document['provider'], document['model'], document['duration']),
            ('session-one', 'faster-whisper', 'small', 12.0),
        )
        self.assertEqual(document['source']['name'], 'session-one.mp4')
        self.assertEqual((self.recording.read_bytes(), self.recording.stat().st_mtime_ns), before)
        copies = [
            path
            for path in (self.root / 'Studio').rglob('*')
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        ]
        self.assertEqual(copies, [], 'the recording must never be copied into the campaign')
        result, work = self.scratch(queued['transcript'])
        self.assertFalse(os.path.exists(result) or os.path.exists(work))

    def test_a_missing_model_fails_the_job_and_a_retry_with_permission_succeeds(self):
        self.configure(model='remote-only')
        failed = self.run_next_after_queue()
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('allow the one-time download', failed['note'])
        self.assertEqual(core.list_docs('transcripts'), [])
        self.assertFalse(os.path.exists(self.scratch(failed['transcript'])[1]))

        self.configure(model='remote-only', allow_download=True)
        retried = self.run_next_after_queue()
        self.assertEqual(retried['status'], 'done', retried.get('note'))
        self.assertEqual(
            retried['transcript'], failed['transcript']
        )  # the same recording, one transcript
        self.assertEqual(core.list_docs('transcripts'), [retried['transcript']])

    def run_next_after_queue(self):
        self.queue()
        return self.run_next()

    def test_an_engine_that_is_not_ready_is_refused_before_anything_is_queued(self):
        self.configure(provider='whisper-cpp')
        with self.assertRaisesRegex(ValueError, 'whisper.cpp'):
            self.queue()
        self.assertTrue(core.LANES['transcribe'].empty())

    def test_a_session_link_must_name_a_prep(self):
        with self.assertRaisesRegex(ValueError, 'No such session'):
            self.queue(session='s9')
        core.write_doc('prep/s1', shapes.PREP.new(n=1, title='One'))
        job = self.queue(session='s1')
        self.assertEqual(self.run_next()['status'], 'done')
        self.assertEqual(self.stored(job['transcript'])['session'], 's1')

    def test_cancelling_a_queued_job_settles_it_and_cleans_up(self):
        queued = self.queue()
        result, work = self.scratch(queued['transcript'])
        os.makedirs(work)
        core.cancel_job(queued['id'])
        self.assertEqual(self.job(queued['id'])['note'], 'Cancelled.')
        self.assertFalse(os.path.exists(work))

    def test_cancelling_a_running_job_stops_the_programs_it_started_and_a_restart_works(self):
        model = self.root / 'ggml-small.bin'
        model.write_bytes(b'x')
        marker = self.root / 'whisper.pid'
        self.configure(
            provider='whisper-cpp',
            model=str(model),
            executable=launcher(self.tools, 'whisper-cli', FAKE_WHISPER_CPP),
            ffmpeg=launcher(self.tools, 'ffmpeg', FAKE_FFMPEG),
        )
        with patch.dict(os.environ, {'FAKE_WHISPER_HOLD': str(marker)}):
            queued = self.queue()
            job, cmd, stdin = core.LANES['transcribe'].get_nowait()
            running = threading.Thread(target=core.execute_job, args=(job, cmd, stdin), daemon=True)
            running.start()
            wait_for(marker)
            grandchild = int(marker.read_text())
            self.assertTrue(psutil.pid_exists(grandchild))
            started = time.monotonic()
            core.cancel_job(queued['id'])
            running.join(30)
        self.assertFalse(running.is_alive())
        self.assertLess(time.monotonic() - started, 20)
        settled = self.job(queued['id'])
        self.assertEqual((settled['status'], settled.get('cancelled')), ('failed', True))
        self.assertFalse(psutil.pid_exists(grandchild), 'cancel must reach the engine itself')
        self.assertEqual(core.list_docs('transcripts'), [])
        self.assertFalse(os.path.exists(self.scratch(queued['transcript'])[1]))

        again = self.queue()  # the same recording can be started again
        self.assertEqual(again['transcript'], queued['transcript'])
        self.assertEqual(self.run_next()['status'], 'done')
        self.assertEqual(len(self.stored(again['transcript'])['segments']), 2)

    def test_a_restart_requeues_a_waiting_job_and_fails_one_that_was_running(self):
        waiting = self.queue()
        self.drain()  # the server restarted: its in-memory lanes are gone
        running = core.new_job(
            'transcribe', 'transcribe', 'Running', ['x'], transcript='rec-ffffffffffffffff'
        )
        self.drain()
        running.update(status='running')
        core.save_job(running)
        core.JOBS_SERVICE.drop_launch(running['id'])
        scratch = self.scratch('rec-ffffffffffffffff')[1]
        os.makedirs(scratch)

        core.JOBS_SERVICE.recover_unfinished()

        requeued, cmd, stdin = core.LANES['transcribe'].get_nowait()
        self.assertEqual(requeued['id'], waiting['id'])
        self.assertEqual(json.loads(stdin)['recording'], str(self.recording.resolve()))
        self.assertEqual(self.job(running['id'])['status'], 'failed')
        self.assertFalse(os.path.exists(scratch))
        core.execute_job(requeued, cmd, stdin)
        self.assertEqual(self.job(waiting['id'])['status'], 'done')


class RouteTests(TranscriptionCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), http_routes.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

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

    def test_the_recordings_page_lists_the_default_folder_and_marks_transcribed_files(self):
        status, listing = self.call('/api/recordings')
        self.assertEqual(status, 200)
        self.assertEqual(listing['path'], str(self.recordings))
        self.assertEqual([f['transcript'] for f in listing['files']], [''])
        self.assertTrue(listing['engine']['available'])

        status, job = self.call('/api/transcripts/start', 'POST', {'path': str(self.recording)})
        self.assertEqual(status, 200)
        self.assertEqual(self.run_next()['status'], 'done')
        _, listing = self.call('/api/recordings?path=' + urllib.request.quote(str(self.recordings)))
        self.assertEqual([f['transcript'] for f in listing['files']], [job['transcript']])

    def test_start_rejects_bad_paths_unknown_sessions_and_a_second_run_of_the_same_file(self):
        notes = self.recordings / 'notes.txt'
        notes.write_text('text')
        for body in (
            {'path': str(notes)},
            {'path': ''},
            {},
            {'path': str(self.recording), 'session': 'nope'},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.call('/api/transcripts/start', 'POST', body)[0], 400)
        self.assertEqual(
            self.call('/api/transcripts/start', 'POST', {'path': str(self.recording)})[0], 200
        )
        status, body = self.call('/api/transcripts/start', 'POST', {'path': str(self.recording)})
        self.assertEqual(status, 409)
        self.assertIn('already being transcribed', body['error'])

    def test_a_transcript_is_read_in_windows_and_removed_without_touching_the_recording(self):
        _, job = self.call('/api/transcripts/start', 'POST', {'path': str(self.recording)})
        self.run_next()
        ident = job['transcript']
        _, listing = self.call('/api/transcripts')
        self.assertEqual([i['id'] for i in listing['items']], [ident])
        self.assertEqual(listing['items'][0]['segment_count'], 2)
        self.assertNotIn('segments', listing['items'][0])

        _, page = self.call(f'/api/transcripts/{ident}?offset=1&limit=1')
        self.assertEqual(
            (page['offset'], [s['text'] for s in page['segments']]), (1, ['Mira opens it.'])
        )
        self.assertEqual(self.call('/api/transcripts/rec-nothing')[0], 400)
        self.assertEqual(self.call('/api/transcripts/rec-0000000000000000')[0], 404)

        before = self.recording.read_bytes()
        self.assertEqual(self.call(f'/api/transcripts/{ident}/remove', 'POST')[0], 200)
        self.assertEqual(self.call(f'/api/transcripts/{ident}/remove', 'POST')[0], 404)
        self.assertEqual(core.list_docs('transcripts'), [])
        self.assertEqual(self.recording.read_bytes(), before)

    def test_a_transcript_cannot_be_removed_while_it_is_being_made(self):
        _, job = self.call('/api/transcripts/start', 'POST', {'path': str(self.recording)})
        core.write_doc(
            'transcripts/' + job['transcript'], shapes.TRANSCRIPT.new(id=job['transcript'])
        )
        self.assertEqual(self.call(f'/api/transcripts/{job["transcript"]}/remove', 'POST')[0], 409)

    def test_settings_save_the_transcription_engine_and_reject_a_bad_one(self):
        _, saved = self.call('/api/settings')
        options = saved['settings']['transcription']
        self.assertEqual(options['provider'], 'faster-whisper')
        self.assertIn('available', saved['transcription'])
        body = {
            **saved['settings'],
            'transcription': {**options, 'language': 'en', 'model': 'tiny'},
        }
        self.assertEqual(self.call('/api/settings', 'POST', body)[0], 200)
        self.assertEqual(config.settings()['transcription']['model'], 'tiny')
        body['transcription']['provider'] = 'cloud'
        self.assertEqual(self.call('/api/settings', 'POST', body)[0], 400)
        legacy = {key: value for key, value in saved['settings'].items() if key != 'transcription'}
        self.assertEqual(self.call('/api/settings', 'POST', legacy)[0], 200)
        self.assertEqual(config.settings()['transcription']['model'], 'tiny')  # kept, not reset


if __name__ == '__main__':
    unittest.main()
