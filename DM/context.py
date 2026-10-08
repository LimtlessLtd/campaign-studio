"""Deterministic, bounded reference data for structured campaign drafts."""

import json
import re


DEFAULT_BUDGET = 64_000  # characters, about 16,000 tokens including the schema
MIN_BUDGET = 16_000
MAX_BUDGET = 200_000
MAX_PINS = 20
ENVELOPE_ALLOWANCE = 2_048

ENTRY_FIELDS = (
    'id',
    'type',
    'name',
    'group',
    'status',
    'public',
    'secrets',
    'notes',
    'tags',
    'related',
    'aka',
)
THREAD_FIELDS = ('id', 'title', 'status', 'detail', 'pcs')
TEXT_FIELDS = ('public', 'secrets', 'notes', 'detail')
OMIT_REFERENCE_FIELDS = {
    'foundry',
    'request',
    'workflow',
    'expanded_by',
    'image',
    'images',
    'files',
    'path',
    'data_path',
    'world_path',
}


def budget(value):
    """Validate a saved prompt budget without silently converting malformed settings."""
    if type(value) is not int or not MIN_BUDGET <= value <= MAX_BUDGET:
        raise ValueError('Set a context budget between 16,000 and 200,000 characters.')
    return value


def clean_pins(value, entries):
    if (
        not isinstance(value, list)
        or len(value) > MAX_PINS
        or any(not isinstance(x, str) for x in value)
    ):
        raise ValueError('Pin at most 20 codex entries.')
    known = {entry.get('id') for entry in entries}
    pins = list(dict.fromkeys(value))
    if any(pin not in known for pin in pins):
        raise ValueError('A pinned codex entry no longer exists.')
    return pins


def _sort(row):
    return (str(row.get('name') or row.get('title') or '').casefold(), str(row.get('id') or ''))


def _brief(row, fields):
    return {field: row[field] for field in fields if field in row}


def _short(value, limit):
    text = str(value or '').strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + '…'


short = _short


def _mentioned(name, text):
    name = str(name or '').strip()
    return (
        len(name) >= 3
        and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I) is not None
    )


def _without_paths(value):
    if isinstance(value, dict):
        return {
            key: _without_paths(item)
            for key, item in value.items()
            if key not in OMIT_REFERENCE_FIELDS
        }
    if isinstance(value, list):
        return [_without_paths(item) for item in value]
    return value


def build(
    prefix,
    base,
    schema,
    entries,
    threads,
    task_text,
    linked_ids=(),
    linked_threads=(),
    pins=(),
    budget_chars=DEFAULT_BUDGET,
):
    """Return a bounded prompt and an inspectable selection summary.

    Linked and pinned records always keep identity. Their long text is shortened
    before any lower-priority record is admitted. Unknown links remain in the
    task's key/prep but cannot consume reference space.
    """
    budget_chars = budget(budget_chars)
    entries = sorted((e for e in entries if isinstance(e, dict) and e.get('id')), key=_sort)
    threads = sorted((t for t in threads if isinstance(t, dict) and t.get('id')), key=_sort)
    by_id = {e['id']: e for e in entries}
    threads_by_id = {t['id']: t for t in threads}
    stored_pins = list(pins)
    missing_pins = [pin for pin in stored_pins if pin not in by_id]
    pins = clean_pins([pin for pin in stored_pins if pin in by_id], entries)
    linked_ids = set(linked_ids) | set(pins)
    linked_threads = set(linked_threads)
    limit = (
        budget_chars
        - len(json.dumps(schema, ensure_ascii=False, separators=(',', ':')))
        - ENVELOPE_ALLOWANCE
    )
    data = dict(base)
    for section in ('campaign', 'key', 'prep'):
        if section in data:
            data[section] = _without_paths(data[section])
    data.update(codex=[], codex_index=[], threads=[], thread_index=[])

    def serialise():
        return json.dumps(data, ensure_ascii=False, separators=(',', ':'))

    def fits():
        return len(prefix) + len(serialise()) <= limit

    if not fits():
        raise ValueError(
            'The task brief, map or session already exceeds the context budget. Increase it in Settings or narrow the task.'
        )

    full_entries = [e for e in entries if e['id'] in linked_ids]
    full_threads = [t for t in threads if t['id'] in linked_threads]
    for entry in full_entries:
        data['codex'].append(_brief(entry, ('id', 'type', 'name')))
    for thread in full_threads:
        data['threads'].append(_brief(thread, ('id', 'title', 'status')))
    if not fits():
        raise ValueError(
            'Linked records exceed the context budget even with their notes shortened.'
        )

    # Thread status is useful for every draft. Linked identities were reserved first.
    for thread in threads:
        if thread['id'] in linked_threads or thread.get('status') not in (
            'open',
            'planned',
            'foreshadowed',
        ):
            continue
        line = _brief(thread, ('id', 'title', 'status'))
        line['detail'] = _short(thread.get('detail'), 120)
        data['thread_index'].append(line)
        if not fits():
            data['thread_index'].pop()
            break

    # Share the remaining room fairly so one long linked note cannot crowd out
    # all other linked records. The identity in each record is never removed.
    full = [(entry, data['codex'][i], ENTRY_FIELDS) for i, entry in enumerate(full_entries)] + [
        (thread, data['threads'][i], THREAD_FIELDS) for i, thread in enumerate(full_threads)
    ]
    share = max(0, (limit - len(prefix) - len(serialise())) // max(1, len(full)) - 100)
    for source, target, fields in full:
        text_fields = [field for field in fields if field in TEXT_FIELDS and field in source]
        text_cap = min(1_200, share // max(1, len(text_fields)))
        for field in fields:
            if field in target or field not in source:
                continue
            value = source[field]
            if field in TEXT_FIELDS:
                if text_cap < 2:
                    continue
                value = _short(value, text_cap)
            elif field in ('tags', 'related', 'aka', 'pcs') and isinstance(value, list):
                value = value[:30]
            target[field] = value
            if not fits():
                if field in TEXT_FIELDS:
                    lo, hi = 0, len(value)
                    while lo < hi:
                        middle = (lo + hi + 1) // 2
                        target[field] = _short(value, middle)
                        if fits():
                            lo = middle
                        else:
                            hi = middle - 1
                    if lo >= 2:
                        target[field] = _short(value, lo)
                        continue
                target.pop(field)

    matched = []
    for entry in entries:
        if entry['id'] in linked_ids or not _mentioned(entry.get('name'), task_text):
            continue
        short = _brief(entry, ENTRY_FIELDS)
        for field in TEXT_FIELDS:
            if field in short:
                short[field] = _short(short[field], 900)
        data['codex'].append(short)
        if not fits():
            data['codex'].pop()
            continue
        matched.append(entry['id'])

    selected = linked_ids | set(matched)
    for entry in entries:
        if entry['id'] in selected:
            continue
        sentence = re.split(
            r'(?<=[.!?])\s+', str(entry.get('public') or entry.get('notes') or ''), maxsplit=1
        )[0]
        line = ' · '.join(
            str(x)
            for x in (
                entry['id'],
                entry.get('type', ''),
                entry.get('name', ''),
                _short(sentence, 100),
            )
        )
        data['codex_index'].append(line)
        if not fits():
            data['codex_index'].pop()
            break

    prompt = prefix + serialise()
    preview = {
        'budget_chars': budget_chars,
        'prompt_chars': len(prompt),
        'schema_chars': len(json.dumps(schema, ensure_ascii=False, separators=(',', ':'))),
        'estimated_tokens': (len(prompt) + 3) // 4,
        'sections': [
            {'name': 'Linked and pinned entries', 'count': len(full_entries)},
            {'name': 'Named in task', 'count': len(matched)},
            {'name': 'Codex index', 'count': len(data['codex_index'])},
            {'name': 'Linked threads', 'count': len(full_threads)},
            {'name': 'Open thread summaries', 'count': len(data['thread_index'])},
        ],
        'full_entries': [_brief(e, ('id', 'name', 'type')) for e in full_entries],
        'omitted_entries': len(entries)
        - len(full_entries)
        - len(matched)
        - len(data['codex_index']),
        'pinned': pins,
        'missing_pins': missing_pins,
    }
    return prompt, preview
