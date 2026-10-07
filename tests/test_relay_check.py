"""The relay check catches review lines that git ignores."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import check_relay

GOOD = 'Title\n\nBody.\n\nReviewed-PR: #8 (no issues)\nReviewed-PR: #9 (no issues)\n'
SPLIT = 'Title\n\nBody.\n\nReviewed-PR: #8 (no issues)\n\nCo-Authored-By: A <a@example.com>\n'
SHARED = (
    'Title\n\nBody.\n\nReviewed-PR: #8 (no issues)\nReviewed-PR: #9 (no issues)\n'
    'Co-Authored-By: A <a@example.com>\n'
)


class MessageTests(unittest.TestCase):
    def test_final_paragraph_is_accepted(self):
        self.assertEqual(check_relay.misplaced(GOOD), [])
        self.assertEqual(check_relay.misplaced(SHARED), [])
        self.assertEqual(check_relay.misplaced('Title\n\nNo reviews.\n'), [])

    def test_line_before_another_paragraph_is_reported(self):
        self.assertEqual(check_relay.misplaced(SPLIT), ['Reviewed-PR: #8 (no issues)'])

    def test_line_in_the_body_is_reported(self):
        message = (
            'Title\n\nReviewed-PR: #8 (no issues)\nmore body text\n\nSigned-off-by: A <a@b.c>\n'
        )
        self.assertEqual(check_relay.misplaced(message), ['Reviewed-PR: #8 (no issues)'])


class HistoryTests(unittest.TestCase):
    def test_commit_range_fails_on_a_misplaced_trailer(self):
        with tempfile.TemporaryDirectory() as folder:

            def git(*args):
                subprocess.run(
                    ['git', '-c', 'user.name=T', '-c', 'user.email=t@example.com', *args],
                    cwd=folder,
                    check=True,
                    capture_output=True,
                )

            git('init', '-q')
            git('commit', '-q', '--allow-empty', '-m', 'base')
            git('commit', '-q', '--allow-empty', '-m', GOOD)
            self.assertEqual(check_relay.check('HEAD~1..HEAD', folder), [])
            git('commit', '-q', '--allow-empty', '-m', SPLIT)
            found = check_relay.check('HEAD~1..HEAD', folder)
            self.assertEqual([line for _, line in found], ['Reviewed-PR: #8 (no issues)'])


if __name__ == '__main__':
    unittest.main()
