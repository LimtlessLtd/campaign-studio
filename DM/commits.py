"""Write-ahead journal for changes that span several campaign documents.

Before the first write, a commit durably records every target's complete new value and a digest
of the bytes it replaces. If the process stops part way through, replaying the entry completes the
targets that still hold their previous bytes and leaves the targets that already hold the new value.
When a target has changed in any other way, nothing is overwritten: the entry is set aside as a
conflict for the GM to review. Pending entries must be replayed before the next read/modify/write
of campaign documents, so callers recover first and commit values computed afterwards.
"""

import datetime
import hashlib
import json
import os
import re

import storage

PENDING = '.pending.json'
CONFLICT = '.conflict.json'
DISMISSED = '.dismissed.json'
UNREADABLE = '.unreadable'
ENTRY_ID = re.compile(r'^[0-9]{8}-[0-9]{6}-[0-9]{6}-[0-9a-f]{6}$')


class IncompleteCommit(OSError):
    """A change could not finish now; it remains journaled and is completed before the next one."""


def digest(path):
    try:
        with open(path, 'rb') as file:
            return hashlib.sha256(file.read()).hexdigest()
    except FileNotFoundError:
        return None


class Journal:
    def __init__(self, folder, path_of, write, finish=lambda actions: []):
        self.folder = folder  # callable, so tests and relocated campaigns resolve it late
        self.path_of = path_of  # target name -> file path
        # (target name, value) -> None. Each target is replaced atomically and flushed to disk, so a
        # power cut after the entry is deleted cannot lose or truncate it.
        self.write = write
        # Runs an entry's recorded follow-up actions (such as queueing a render) once every target
        # is written, before the entry is cleared. It must be idempotent and must not raise.
        self.finish = finish

    def entries(self, suffix=PENDING):
        try:
            names = sorted(n for n in os.listdir(self.folder()) if n.endswith(suffix))
        except FileNotFoundError:
            return []
        return [os.path.join(self.folder(), name) for name in names]

    def commit(self, label, changes, after=()):
        """Write [(name, value), ...] in order, then run the follow-up actions in after.

        A str value is a text file; anything else is JSON. Put the record that marks the change
        as finished (a workflow or request status) last. Returns the follow-up results.
        """
        if self.entries():
            raise IncompleteCommit(
                'An earlier change is still being completed. Try again in a moment.'
            )
        targets = []
        for name, value in changes:
            if any(target['name'] == name for target in targets):
                raise ValueError('A change may write each document only once.')
            targets.append(
                {
                    'name': name,
                    'text': isinstance(value, str),
                    'before': digest(self.path_of(name)),
                    'value': value,
                }
            )
        now = datetime.datetime.now()
        entry = {
            'id': now.strftime('%Y%m%d-%H%M%S-%f-') + os.urandom(3).hex(),
            'label': str(label)[:200],
            'created': now.isoformat(timespec='seconds'),
            'targets': targets,
            'after': list(after),
        }
        path = os.path.join(self.folder(), entry['id'] + PENDING)
        storage.atomic_json(path, entry, durable=True)
        try:
            for target in targets:
                self.write(target['name'], target['value'])
        except OSError as error:
            # One replay rides out a brief sharing failure. Otherwise the entry stays pending.
            try:
                outcome, summary = self.replay(path)
            except OSError:
                raise IncompleteCommit(
                    f'{entry["label"]} was interrupted ({error}). Campaign Studio will finish '
                    'it before the next change or when it restarts.'
                ) from error
            if outcome == 'conflicts':
                raise IncompleteCommit(
                    f'{entry["label"]} was interrupted and a document changed meanwhile. '
                    'Review the interrupted change on the dashboard.'
                ) from error
            return summary['results']
        results = self.finish(entry['after'])
        storage.remove(path)
        return results

    def state(self, target):
        path = self.path_of(target['name'])
        if digest(path) == target['before']:
            return 'pending'
        try:
            # newline='' compares text exactly as atomic_text wrote it, including any \r.
            with open(path, encoding='utf-8', newline='') as file:
                raw = file.read()
            value = raw if target['text'] else json.loads(raw)
        except (FileNotFoundError, ValueError):
            return 'changed'
        return 'written' if value == target['value'] else 'changed'

    @staticmethod
    def set_aside(path, value):
        """Record a conflict for GM review, then stop replaying the pending entry."""
        storage.atomic_json(path[: -len(PENDING)] + CONFLICT, value, durable=True)
        storage.remove(path)

    def replay(self, path):
        try:
            with open(path, encoding='utf-8') as file:
                entry = json.load(file)
            targets = entry['targets']
            summary = {
                'id': entry['id'],
                'label': entry['label'],
                'created': entry['created'],
                'targets': [{'name': t['name'], 'state': self.state(t)} for t in targets],
            }
        except (ValueError, KeyError, TypeError) as error:
            # A damaged record can never complete; keep its bytes and stop it blocking every change.
            os.replace(path, path[: -len(PENDING)] + UNREADABLE)
            summary = unreadable(path, error)
            self.set_aside(path, {'review': summary})
            return 'conflicts', summary
        states = [target['state'] for target in summary['targets']]
        if 'changed' in states:
            self.set_aside(path, dict(entry, review=summary))
            return 'conflicts', summary
        for target, state in zip(targets, states):
            if state == 'pending':
                self.write(target['name'], target['value'])
        summary['results'] = self.finish(entry.get('after', []))
        storage.remove(path)
        return 'completed', summary

    def recover(self):
        """Replay pending entries in creation order. OSError leaves the rest pending."""
        report = {'completed': [], 'conflicts': []}
        for path in self.entries():
            outcome, summary = self.replay(path)
            report[outcome].append(summary)
        return report

    def conflicts(self):
        result = []
        for path in self.entries(CONFLICT):
            try:
                with open(path, encoding='utf-8') as file:
                    result.append(json.load(file)['review'])
            except (ValueError, KeyError, TypeError) as error:
                result.append(unreadable(path, error))
        return result

    def dismiss(self, entry_id):
        """Keep a reviewed conflict's saved values on disk, but stop reporting it."""
        if not ENTRY_ID.fullmatch(str(entry_id)):
            raise ValueError('Invalid change id.')
        path = os.path.join(self.folder(), entry_id + CONFLICT)
        if not os.path.isfile(path):
            raise ValueError('That interrupted change is no longer awaiting review.')
        storage.atomic_replace(path, path[: -len(CONFLICT)] + DISMISSED)


def unreadable(path, error):
    name = os.path.basename(path).split('.', 1)[0]
    return {
        'id': name,
        'label': f'An unreadable change record ({error})',
        'created': '',
        'targets': [],
    }
