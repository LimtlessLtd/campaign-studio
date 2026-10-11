"""How a reviewed proposal changes records that already exist, and the preview the GM reads first.

Applying and previewing share one function per kind of change: the preview runs the same update on a copy
of the record and reports the fields that differ, so it cannot promise something the apply does not do.
Rows that make new records need no preview; the GM reads them whole.
"""

from copy import deepcopy

import item_review

TAIL = 160  # characters of the existing text shown before an addition
# The fields a preview reports: (name, label, appended). Text that is appended is shown as an addition;
# anything else as the old value and the new one.
THREAD_FIELDS = (('status', 'Status', False), ('detail', 'Detail', True))
ENTRY_FIELDS = (
    ('status', 'Status', False),
    ('group', 'Group', False),
    ('notes', 'Notes', True),
)


def joined(old, new):
    """`old` followed by `new` as another paragraph; either may be blank."""
    return '\n\n'.join(x for x in (old, new) if x and x.strip())


def update_thread(thread, row, session, request):
    """Apply one proposed change to an existing thread, and note that this session touched it."""
    thread['status'] = row['status']
    if row['update'].strip():
        thread['detail'] = joined(thread.get('detail', ''), row['update'].strip())
    thread['sessions'] = list(dict.fromkeys([*thread.get('sessions', []), session]))
    thread.setdefault('session_updates', []).append(request)
    return thread


def update_entry(entry, row, session):
    """Apply one proposed change to an existing codex entry: a new status or group, and a dated note."""
    for field in ('status', 'group'):
        if row[field].strip():
            entry[field] = row[field].strip()
    if row['notes'].strip():
        entry['notes'] = joined(
            entry.get('notes', ''), f'{session.upper()}: {row["notes"].strip()}'
        )
    return entry


def changed_fields(before, after, fields):
    """Each of `fields` that differs between two versions of a record, ready for the browser to draw.

    A change is `{label, before, after}`, or `{label, before, added}` when text was appended, where `before`
    is only the end of the old text.
    """
    shown = []
    for name, label, appended in fields:
        old, new = str(before.get(name) or ''), str(after.get(name) or '')
        if old == new:
            continue
        if appended and new.startswith(old):
            tail = ('…' if len(old) > TAIL else '') + old[-TAIL:]
            shown.append({'label': label, 'before': tail, 'added': new[len(old) :].strip()})
        else:
            shown.append({'label': label, 'before': old, 'after': new})
    return shown


def thread_changes(draft, item, read_doc):
    """Preview each proposed change to an existing thread, by the key the GM ticks it with."""
    threads = {t['id']: t for t in (read_doc('threads') or {'threads': []})['threads']}
    return {
        item_review.key('thread_changes', row): changed_fields(
            threads[row['id']],
            update_thread(deepcopy(threads[row['id']]), row, item['session'], item['id']),
            THREAD_FIELDS,
        )
        for row in draft['thread_changes']
    }


def entry_changes(draft, item, read_doc):
    """Preview each proposed change to an existing codex entry, by the key the GM ticks it with."""
    entries = {e['id']: e for e in (read_doc('codex') or {'entries': []})['entries']}
    return {
        item_review.key('codex_changes', row): changed_fields(
            entries[row['id']],
            update_entry(deepcopy(entries[row['id']]), row, item['session']),
            ENTRY_FIELDS,
        )
        for row in draft['codex_changes']
    }
