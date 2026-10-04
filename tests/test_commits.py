"""Crash and conflict recovery for multi-document commits."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import commits
import storage


class Crash(BaseException):
    """Stands in for the process stopping: commit code cannot catch it."""


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.writes = []
        self.journal = commits.Journal(
            lambda: str(self.root / '.commits'), self.path_of, self.write
        )
        storage.atomic_json(self.path_of('codex'), {'entries': [{'id': 'old'}]})
        storage.atomic_json(self.path_of('status'), {'state': 'review'})

    def tearDown(self):
        self.temp.cleanup()

    def path_of(self, name):
        return str(self.root / (name + ('.txt' if name == 'plan' else '.json')))

    def write(self, name, value):
        self.writes.append(name)
        if isinstance(value, str):
            storage.atomic_text(self.path_of(name), value)
        else:
            storage.atomic_json(self.path_of(name), value)

    def read(self, name):
        text = Path(self.path_of(name)).read_text(encoding='utf-8')
        return text if name == 'plan' else json.loads(text)

    def changes(self):
        return [
            ('codex', {'entries': [{'id': 'old'}, {'id': 'new'}]}),
            ('plan', 'name: Fixture\n---\n..\n'),
            ('threads', {'threads': [{'id': 'new-thread'}]}),
            ('status', {'state': 'applied'}),
        ]

    def crash_after(self, count):
        def write(name, value):
            if len(self.writes) == count:
                raise Crash
            self.write(name, value)
            if len(self.writes) == count:
                raise Crash

        return write

    def test_crash_at_each_write_boundary_recovers_the_complete_change(self):
        expected = dict(self.changes())
        for count in range(len(expected) + 1):
            with self.subTest(after_writes=count):
                storage.atomic_json(self.path_of('codex'), {'entries': [{'id': 'old'}]})
                storage.atomic_json(self.path_of('status'), {'state': 'review'})
                for name in ('plan', 'threads'):
                    Path(self.path_of(name)).unlink(missing_ok=True)
                self.writes.clear()
                self.journal.write = self.crash_after(count)
                with self.assertRaises(Crash):
                    self.journal.commit('Fixture change', self.changes())
                self.assertEqual(self.writes, list(expected)[:count])
                self.assertEqual(len(self.journal.entries()), 1)

                self.journal.write = self.write
                report = self.journal.recover()

                self.assertEqual(len(report['completed']), 1)
                self.assertEqual(report['conflicts'], [])
                self.assertEqual(self.journal.entries(), [])
                for name, value in expected.items():
                    self.assertEqual(self.read(name), value)
                # Targets written before the crash are not written a second time.
                self.assertEqual(self.writes, list(expected))
                self.assertEqual(self.journal.recover(), {'completed': [], 'conflicts': []})

    def test_changed_target_is_reported_and_nothing_is_overwritten(self):
        self.journal.write = self.crash_after(1)
        with self.assertRaises(Crash):
            self.journal.commit('Fixture change', self.changes())
        storage.atomic_json(self.path_of('status'), {'state': 'edited elsewhere'})
        self.journal.write = self.write

        report = self.journal.recover()

        self.assertEqual(report['completed'], [])
        states = {t['name']: t['state'] for t in report['conflicts'][0]['targets']}
        self.assertEqual(
            states,
            {'codex': 'written', 'plan': 'pending', 'threads': 'pending', 'status': 'changed'},
        )
        self.assertFalse(Path(self.path_of('plan')).exists())
        self.assertEqual(self.read('status'), {'state': 'edited elsewhere'})
        self.assertEqual(self.journal.entries(), [])
        self.assertEqual(self.journal.conflicts(), report['conflicts'])
        # A conflict no longer blocks new changes.
        self.journal.commit('Next change', [('threads', {'threads': []})])

        self.journal.dismiss(report['conflicts'][0]['id'])
        self.assertEqual(self.journal.conflicts(), [])
        self.assertEqual(len(self.journal.entries(commits.DISMISSED)), 1)
        with self.assertRaises(ValueError):
            self.journal.dismiss('../escape')

    def test_temporary_write_failure_is_retried_once(self):
        failures = [OSError('Synthetic sharing violation')]

        def flaky(name, value):
            if name == 'threads' and failures:
                raise failures.pop()
            self.write(name, value)

        self.journal.write = flaky
        self.journal.commit('Fixture change', self.changes())

        self.assertEqual(self.journal.entries(), [])
        for name, value in self.changes():
            self.assertEqual(self.read(name), value)

    def test_persistent_failure_stays_pending_and_blocks_the_next_commit(self):
        def failing(name, value):
            if name == 'threads':
                raise OSError('Synthetic full disk')
            self.write(name, value)

        self.journal.write = failing
        with self.assertRaises(commits.IncompleteCommit):
            self.journal.commit('Fixture change', self.changes())
        self.assertEqual(len(self.journal.entries()), 1)
        with self.assertRaises(commits.IncompleteCommit):
            self.journal.commit('Another change', [('codex', {'entries': []})])
        with self.assertRaises(OSError):
            self.journal.recover()
        self.assertEqual(len(self.journal.entries()), 1)

        self.journal.write = self.write
        self.assertEqual(len(self.journal.recover()['completed']), 1)
        self.assertEqual(self.read('status'), {'state': 'applied'})

    def test_a_change_writes_each_document_once(self):
        with self.assertRaises(ValueError):
            self.journal.commit('Duplicate', [('codex', {}), ('codex', {})])
        self.assertEqual(self.journal.entries(), [])


if __name__ == '__main__':
    unittest.main()
