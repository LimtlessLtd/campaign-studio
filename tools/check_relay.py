"""Fail when a `Reviewed-PR:` line sits where git reads no trailer.

Git only reads trailers from the final paragraph of a commit message, so a review line above a
separate `Co-Authored-By` paragraph is invisible to the relay's `%(trailers)` query.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_LINE = re.compile(r'^Reviewed-PR[ \t]*:', re.IGNORECASE)


def review_lines(text):
    return [line for line in text.splitlines() if REVIEW_LINE.match(line)]


def misplaced(message):
    """Review lines in `message` that git does not parse as trailers."""
    wanted = review_lines(message)
    if not wanted:
        return []
    parsed = subprocess.run(
        ['git', 'interpret-trailers', '--parse'],
        input=message,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
    ).stdout
    for line in review_lines(parsed):
        wanted.remove(line)
    return wanted


def check(revisions, repo=ROOT):
    log = subprocess.check_output(
        ['git', 'log', '--no-merges', '--format=%H%x1f%B%x1e', revisions],
        cwd=repo,
        text=True,
        encoding='utf-8',
    )
    problems = []
    for record in log.split('\x1e'):
        sha, _, message = record.strip().partition('\x1f')
        problems += [(sha[:9], line) for line in misplaced(message)]
    return problems


if __name__ == '__main__':
    revisions = sys.argv[1] if len(sys.argv) > 1 else 'HEAD^1..HEAD'
    found = check(revisions)
    for sha, line in found:
        print(f'{sha}: not a trailer (put it in the final paragraph): {line}')
    if found:
        sys.exit(1)
    print(f'Relay trailers ok in {revisions}.')
