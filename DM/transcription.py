"""Local speech-to-text for session recordings, and the transcript Studio stores from it.

A recording is read where it lies: Studio never copies or uploads it. An engine runs in a worker process
(tools/transcribe_worker.py) and returns timed segments. This module holds what the server and the worker
share: the engines, the settings that choose one, the description of a recording, and the check that turns
a worker's output into a bounded transcript document. A transcript is what was said at the table. It is
reference data and is never passed to a model as instructions.
"""

import collections
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import campaign
import config
import shapes
import storage

MEDIA = (
    '.mp4',
    '.m4v',
    '.mkv',
    '.mov',
    '.webm',
    '.avi',
    '.mp3',
    '.m4a',
    '.aac',
    '.wav',
    '.flac',
    '.ogg',
    '.opus',
)
MAX_LISTED = 500
MAX_SEGMENTS = 20_000
MAX_SEGMENT_CHARS = 2_000
MAX_TEXT_CHARS = 2_000_000
ID = re.compile(r'rec-[0-9a-f]{16}\Z')
LANGUAGE = re.compile(r'[a-z]{2,3}\Z')
WHISPER_CPP_PROGRESS = re.compile(r'progress\s*=\s*(\d{1,3})%')
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


class FasterWhisper:
    """faster-whisper (a Python package) decodes the recording itself, so it needs no ffmpeg."""

    label = 'faster-whisper'

    def problem(self, options):
        if importlib.util.find_spec('faster_whisper') is None:
            return 'faster-whisper is not installed. Run: python -m pip install faster-whisper'
        return ''

    def run(self, recording, options, work, progress):
        from faster_whisper import (
            WhisperModel,
        )  # imported late: loading it costs seconds and memory

        progress(0, 'loading the model')
        try:
            # Without allow_download a model that is not already on this computer is an error, not a fetch.
            model = WhisperModel(options['model'], local_files_only=not options['allow_download'])
        except Exception as error:  # the library reports a missing model as several error types
            raise ValueError(
                f'Could not load the Whisper model "{options["model"]}": {error}. If it has not '
                'been downloaded yet, allow the one-time download in Settings, or set the folder '
                'of a model you already have.'
            ) from error
        segments, info = model.transcribe(
            recording, language=options['language'] or None, vad_filter=True
        )
        heard = []
        for segment in segments:
            heard.append({'start': segment.start, 'end': segment.end, 'text': segment.text})
            if info.duration:
                progress(segment.end / info.duration, 'transcribing')
        return {'language': info.language, 'duration': info.duration, 'segments': heard}


class WhisperCpp:
    """whisper.cpp reads 16 kHz WAV, so ffmpeg extracts the audio to the job's scratch folder first."""

    label = 'whisper.cpp'

    @staticmethod
    def tools(options):
        return (
            shutil.which(options['executable'] or 'whisper-cli'),
            shutil.which(options['ffmpeg'] or 'ffmpeg'),
        )

    def problem(self, options):
        engine, ffmpeg = self.tools(options)
        if not engine:
            return 'whisper.cpp (whisper-cli) was not found. Set its location in Settings.'
        if not ffmpeg:
            return 'ffmpeg was not found. whisper.cpp needs it to read the recording.'
        if not os.path.isfile(options['model']):
            return 'Set the path of a whisper.cpp model file (ggml-*.bin) in Settings.'
        return ''

    def run(self, recording, options, work, progress):
        engine, ffmpeg = self.tools(options)
        audio = os.path.join(work, 'audio.wav')
        progress(0, 'extracting the audio')
        extract = subprocess.run(
            [ffmpeg, '-nostdin', '-y', '-v', 'error', '-i', recording]
            + ['-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', audio],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=NO_WINDOW,
        )
        if extract.returncode:
            raise ValueError(
                'ffmpeg could not read the recording: ' + extract.stderr.strip()[-500:]
            )
        prefix = os.path.join(work, 'transcript')
        command = [engine, '-m', options['model'], '-f', audio, '-oj', '-of', prefix, '-pp']
        command += ['-l', options['language'] or 'auto']
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=NO_WINDOW,
        ) as process:
            recent = collections.deque(maxlen=5)
            for line in process.stdout:
                recent.append(line.strip())
                found = WHISPER_CPP_PROGRESS.search(line)
                if found:
                    progress(int(found.group(1)) / 100, 'transcribing')
        if process.returncode:
            raise ValueError('whisper.cpp failed: ' + ' | '.join(recent))
        with open(prefix + '.json', encoding='utf-8') as file:
            result = json.load(file)
        heard = [
            {
                'start': row['offsets']['from'] / 1000,
                'end': row['offsets']['to'] / 1000,
                'text': row['text'],
            }
            for row in result['transcription']
        ]
        return {
            'language': (result.get('result') or {}).get('language', ''),
            'duration': max((row['end'] for row in heard), default=0),
            'segments': heard,
        }


PROVIDERS = {'faster-whisper': FasterWhisper(), 'whisper-cpp': WhisperCpp()}


def _setting(raw, key, limit):
    value = str(raw.get(key) or '').strip()
    if len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError(f'The transcription {key.replace("_", " ")} is not valid.')
    return value


def clean_settings(raw):
    """Validate the saved engine choice. It names an engine, a model and local programs, never a key."""
    if not isinstance(raw, dict):
        raise ValueError('Transcription settings must be an object.')
    provider = str(raw.get('provider') or 'faster-whisper')
    if provider not in PROVIDERS:
        raise ValueError('Choose a supported transcription engine.')
    language = _setting(raw, 'language', 3).lower()
    if language and not LANGUAGE.fullmatch(language):
        raise ValueError('Use a language code such as en, or leave it blank to detect it.')
    return {
        'provider': provider,
        'model': _setting(raw, 'model', 1024),
        'language': language,
        'executable': _setting(raw, 'executable', 1024),
        'ffmpeg': _setting(raw, 'ffmpeg', 1024),
        'allow_download': raw.get('allow_download') is True,
    }


def selected(options=None):
    options = options or config.settings()['transcription']
    provider = PROVIDERS.get(options.get('provider'))
    if provider is None:
        raise ValueError('Choose a supported transcription engine in Settings.')
    return provider


def status(options=None):
    """The engine in use and what stops it from running, for the Settings and Recordings pages."""
    options = options or config.settings()['transcription']
    try:
        provider = selected(options)
    except ValueError as error:  # a hand-edited settings file must not break the Settings page
        return {'provider': '', 'label': 'No engine', 'available': False, 'problem': str(error)}
    problem = provider.problem(options)
    return {
        'provider': options['provider'],
        'label': provider.label,
        'available': not problem,
        'problem': problem,
    }


def transcript_id(path, info):
    """A stable ID for one recording file: the same file yields the same ID, so a retry cannot duplicate."""
    key = f'{os.path.normcase(path)}\n{info.st_size}\n{info.st_mtime_ns}'
    return 'rec-' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]


def describe(path):
    info = path.stat()
    if not info.st_size:
        raise ValueError('That recording is empty.')
    return {
        'id': transcript_id(str(path), info),
        'path': str(path),
        'name': path.name,
        'size': info.st_size,
        'modified': int(info.st_mtime),
    }


def recording(value):
    """Describe the recording file a person chose; the file is only read, never changed or copied."""
    path = storage.local_path(value, directory=False)
    if path.suffix.lower() not in MEDIA:
        raise ValueError('Choose a video or audio recording (' + ', '.join(MEDIA) + ').')
    return describe(path)


def recordings(value):
    """The recordings in a folder (not its subfolders), newest first, or the one file named."""
    value = value if isinstance(value, str) else ''
    if not Path(value).expanduser().is_dir():
        return [recording(value)]
    found = []
    with os.scandir(storage.local_path(value)) as entries:
        for entry in entries:
            path = Path(entry.path)
            if (
                path.suffix.lower() in MEDIA
                and entry.is_file(follow_symlinks=False)
                and path.stat().st_size
            ):
                found.append(describe(path))
    found.sort(key=lambda item: (-item['modified'], item['name']))
    return found[:MAX_LISTED]


def staging(jobs, transcript):
    """Where a transcription job leaves its result, and its scratch folder for extracted audio."""
    return (
        os.path.join(jobs, f'{transcript}.transcript'),
        os.path.join(tempfile.gettempdir(), f'campaign-studio-{transcript}'),
    )


def discard(jobs, transcript):
    """Remove a job's staged result and scratch audio. Safe to repeat."""
    result, work = staging(jobs, transcript)
    storage.remove(result)
    shutil.rmtree(work, ignore_errors=True)


def request(rec, options, jobs):
    """The JSON a worker reads from its standard input."""
    result, work = staging(jobs, rec['id'])
    return json.dumps({'recording': rec['path'], 'options': options, 'out': result, 'work': work})


def worker_command():
    return [sys.executable, '-u', os.path.join(campaign.INSTALL, 'tools', 'transcribe_worker.py')]


def transcribe(job_request, progress):
    """Run the engine a worker request names and return its raw segments (used by the worker)."""
    options = job_request['options']
    engine = selected(options)
    problem = engine.problem(options)
    if problem:
        raise ValueError(problem)
    os.makedirs(job_request['work'], exist_ok=True)
    return engine.run(job_request['recording'], options, job_request['work'], progress)


def _seconds(value):
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    return round(float(value), 2) if ok and math.isfinite(value) and value >= 0 else None


def clean_segments(raw):
    """(segments, truncated): an engine's usable segments in time order, within the stored limits."""
    if not isinstance(raw, list):
        raise ValueError('The transcription result has no segments.')
    kept, characters, truncated = [], 0, False
    for row in raw:
        if not isinstance(row, dict):
            continue
        start, end = _seconds(row.get('start')), _seconds(row.get('end'))
        text = ' '.join(str(row.get('text') or '').split())[:MAX_SEGMENT_CHARS]
        if start is None or end is None or not text:
            continue
        if len(kept) >= MAX_SEGMENTS or characters + len(text) > MAX_TEXT_CHARS:
            truncated = True
            break
        kept.append(shapes.TRANSCRIPT_SEGMENT.new(start=start, end=max(start, end), text=text))
        characters += len(text)
    kept.sort(key=lambda segment: (segment['start'], segment['end']))
    return kept, truncated


def build(job, staged):
    """The transcript document for a finished job from the worker's staged output."""
    if not isinstance(staged, dict):
        raise ValueError('The transcription result is not valid.')
    segments, truncated = clean_segments(staged.get('segments'))
    if not segments:
        raise ValueError('No speech was found in this recording.')
    source, engine = job['recording'], job['engine']
    return shapes.TRANSCRIPT.new(
        id=job['transcript'],
        title=os.path.splitext(source['name'])[0][:200],
        session=job.get('session', ''),
        source={key: source[key] for key in ('name', 'path', 'size', 'modified')},
        provider=engine['provider'],
        model=os.path.basename(engine['model'].rstrip('/\\')),
        language=str(staged.get('language') or '')[:16],
        duration=max(_seconds(staged.get('duration')) or 0, segments[-1]['end']),
        created=time.time(),
        truncated=truncated,
        segments=segments,
    )


def summary(document):
    """A transcript without its segments, for lists."""
    return {key: value for key, value in document.items() if key != 'segments'} | {
        'segment_count': len(document.get('segments', []))
    }
