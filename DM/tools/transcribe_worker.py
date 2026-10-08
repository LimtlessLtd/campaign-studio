"""Transcribe one recording with a local engine.

Reads a JSON request on standard input (see transcription.request), prints PROGRESS lines, and leaves the
engine's segments in the request's output file. The server validates and stores them; this process writes
no campaign document and sends nothing over the network (unless the GM allowed a one-time model download).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # app modules
import storage
import transcription


reported = None


def progress(fraction, label=''):
    """Print a PROGRESS line when the percentage or label changes, so a long run keeps a short log."""
    global reported
    percent = round(100 * min(1, max(0, fraction)))
    if (percent, label) != reported:
        reported = (percent, label)
        print(f'PROGRESS {percent}% {label}'.rstrip(), flush=True)


def main():
    job_request = json.loads(sys.stdin.read())
    result = transcription.transcribe(job_request, progress)
    storage.atomic_json(job_request['out'], result)
    progress(1, 'done')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        sys.exit('Transcription failed: ' + str(error))
