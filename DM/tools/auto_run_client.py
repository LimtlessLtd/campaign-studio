"""Press Run for the automatic run on a Campaign Studio server running on this computer.

    python DM/tools/auto_run_client.py --folder "<folder of recordings>"
    python DM/tools/auto_run_client.py --status

A scheduled task runs this between sessions. Each press starts a run for the new recordings in the folder,
or carries on the run in progress, and then prints where it stands: what is working, what waits for you
and why a run stopped. It asks the server to do everything; it reads no campaign file and no key.

Exit status: 0 when the run is working, waiting for a review, done or has nothing new to take; 2 when it
stopped and needs attention; 3 when the server cannot be reached; 4 when the server refused the request.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_URL = 'http://127.0.0.1:8766'
TIMEOUT = 60  # a press only queues work


class Unreachable(Exception):
    pass


class Refused(Exception):
    pass


def call(url, path, body=None):
    """GET `path`, or POST `body` to it, and return the server's JSON answer."""
    data = None if body is None else json.dumps(body).encode('utf-8')
    headers = {'X-DM-Site': '1', 'Content-Type': 'application/json'} if data else {}
    request = urllib.request.Request(url.rstrip('/') + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            try:
                message = json.load(error).get('error') or error.reason
            except ValueError:
                message = error.reason
        raise Refused(message) from error
    except (urllib.error.URLError, OSError) as error:
        raise Unreachable(str(getattr(error, 'reason', error))) from error


def describe(run, url):
    """The report of a run as lines a person (or a scheduled task's summary) can read."""
    if run is None or run.get('state') == 'idle':
        return [(run or {}).get('message') or 'There is no automatic run yet.']
    lines = [f'Run for session {run["session"]}: {run["state"]}.']
    if run['note']:
        lines.append('Note: ' + run['note'])
    for row in run['steps']:
        if row['state'] in ('failed', 'running', 'queued'):
            subject = f' ({row["subject"]})' if row['subject'] else ''
            lines.append(f'  {row["state"]}: {row["label"]}{subject}. {row["note"]}'.rstrip())
    if run['waiting']:
        lines.append('Waiting for you:')
        lines += ['  ' + text for text in run['waiting']]
        lines.append(f'Review it at {url.rstrip("/")}/#/recordings')
    if run.get('next_session'):
        lines.append(f'The next session is being drafted in {run["next_session"]}.')
    used = run['usage']
    lines.append(f'AI requests so far: {used["requests"]}.')
    return lines


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--url', default=DEFAULT_URL, help='the Campaign Studio server')
    parser.add_argument(
        '--folder', default='', help="folder of recordings (default: the campaign's own)"
    )
    parser.add_argument(
        '--session', default='', help='session prep the recordings belong to, such as s12'
    )
    parser.add_argument('--status', action='store_true', help='only report; start nothing')
    parser.add_argument('--json', action='store_true', help="print the server's answer as JSON")
    args = parser.parse_args(argv)
    try:
        if args.status:
            answer = call(args.url, '/api/auto-run')['run']
        else:
            answer = call(args.url, '/api/auto-run', {'path': args.folder, 'session': args.session})
    except Unreachable as error:
        print(
            f'Campaign Studio is not running at {args.url} ({error}). Start it, then run this again.',
            file=out,
        )
        return 3
    except Refused as error:
        print(f'The server refused the request: {error}', file=out)
        return 4
    print(
        json.dumps(answer, indent=1) if args.json else '\n'.join(describe(answer, args.url)),
        file=out,
    )
    return 2 if answer and answer.get('state') == 'stopped' else 0


if __name__ == '__main__':
    sys.exit(main())
