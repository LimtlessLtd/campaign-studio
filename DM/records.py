"""Per-record codex and thread storage and bounded list views.

The physical key is the public ID for ordinary IDs. Legacy IDs that are unsafe as
filenames use a deterministic digest; the ID inside the document remains unchanged.
"""

import hashlib
import json
import os
import re


FIELDS = {'codex': 'entries', 'threads': 'threads'}
SAFE_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,127}\Z')


def document_name(kind, ident):
    if kind not in FIELDS:
        raise ValueError('Unknown record type.')
    if not isinstance(ident, str) or not ident or len(ident.encode('utf-8')) > 1024:
        raise ValueError('Invalid record ID.')
    filename = (
        ident if SAFE_ID.fullmatch(ident) else 'z-' + hashlib.sha256(ident.encode()).hexdigest()
    )
    return f'{kind}/{filename}'


def record_path(data, kind, ident):
    name = document_name(kind, ident)
    folder = os.path.join(data, kind)
    if os.path.islink(folder) or (os.path.exists(folder) and not os.path.isdir(folder)):
        raise ValueError(f'Unsafe record folder: {kind}')
    path = os.path.join(data, name + '.json')
    if os.path.islink(path):
        raise ValueError(f'Unsafe record file: {name}')
    return path


def read(data, kind, ident):
    try:
        with open(record_path(data, kind, ident), encoding='utf-8') as file:
            value = json.load(file)
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or value.get('id') != ident:
        raise ValueError(f'Record storage key collision: {kind}/{ident}')
    return value


def all_records(data, kind):
    if kind not in FIELDS:
        raise ValueError('Unknown record type.')
    folder = os.path.join(data, kind)
    if os.path.islink(folder):
        raise ValueError(f'Unsafe record folder: {kind}')
    if not os.path.isdir(folder):
        return []
    result = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith('.json'):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or os.path.islink(path):
            raise ValueError(f'Unsafe record file: {kind}/{name}')
        with open(path, encoding='utf-8') as file:
            value = json.load(file)
        if (
            not isinstance(value, dict)
            or document_name(kind, value.get('id')) != f'{kind}/{name[:-5]}'
        ):
            raise ValueError(f'Invalid record file: {kind}/{name}')
        result.append(value)
    return result


def collection(data, kind):
    return {FIELDS[kind]: all_records(data, kind)}


def changed_documents(data, kind, value):
    """Expand a collection mutation into only its changed journal targets."""
    field = FIELDS[kind]
    if not isinstance(value, dict) or not isinstance(value.get(field), list):
        raise ValueError(f'Invalid {kind} collection.')
    current = {document_name(kind, row['id']): row for row in all_records(data, kind)}
    changes, seen = [], set()
    for record in value[field]:
        if not isinstance(record, dict):
            raise ValueError(f'Invalid {kind} record.')
        ident = record.get('id')
        name = document_name(kind, ident)
        if name in seen:
            raise ValueError(f'Duplicate {kind} record ID: {ident}')
        seen.add(name)
        if current.get(name) != record:
            changes.append((name, record))
    for name in current:
        if name not in seen:
            changes.append((name, None))
    return changes


def source_of(record):
    if record.get('foundry'):
        return 'foundry'
    if record.get('workflow') or record.get('request'):
        return 'ai'
    return 'studio'


def choices(data, kind):
    fields = (
        ('id', 'name', 'type', 'status', 'image')
        if kind == 'codex'
        else ('id', 'title', 'status', 'pcs')
    )
    result = []
    for item in all_records(data, kind):
        brief = {field: item.get(field) for field in fields}
        if kind == 'codex':
            foundry = item.get('foundry') if isinstance(item.get('foundry'), dict) else {}
            brief['foundry'] = {
                'world_key': foundry.get('world_key'),
                'image': foundry.get('image'),
            }
        result.append(brief)
    return result


def last_session(thread):
    """Number of the newest session a thread touches (prep names look like s12); 0 when none."""
    numbers = [
        int(match.group(1))
        for name in thread.get('sessions', [])
        if isinstance(name, str) and (match := re.fullmatch(r's(\d{1,6})', name))
    ]
    return max(numbers, default=0)


def page(
    data,
    kind,
    *,
    offset=0,
    limit=40,
    query='',
    type='',
    tag='',
    source='',
    status='',
    pc='',
    sort='',
):
    """Search the records on the server; return a small, stable page to the browser.

    Threads sort by title, or with sort='stale' by the session they last touched, oldest first.
    """
    if not (0 <= offset <= 1000000 and 1 <= limit <= 100):
        raise ValueError('Invalid page range.')
    query = query.strip().casefold()[:200]
    if kind == 'codex':

        def include(record):
            return (
                (not type or record.get('type') == type)
                and (
                    not tag or tag.casefold() in (str(t).casefold() for t in record.get('tags', []))
                )
                and (not source or source_of(record) == source)
                and (
                    not query
                    or query
                    in ' '.join(
                        str(record.get(k, ''))
                        for k in ('id', 'name', 'public', 'secrets', 'notes', 'group')
                    ).casefold()
                    or query in ' '.join(str(t) for t in record.get('tags', [])).casefold()
                )
            )

        def summary(record):
            foundry = record.get('foundry') if isinstance(record.get('foundry'), dict) else {}
            return {
                key: record.get(key) for key in ('id', 'type', 'name', 'status', 'tags', 'image')
            } | {
                'foundry': {
                    'world_key': foundry.get('world_key'),
                    'image': foundry.get('image'),
                },
                'public': (record.get('public') or record.get('notes') or '')[:240],
                'has_secrets': bool(record.get('secrets')),
                'source': source_of(record),
            }

        sort_key = lambda row: (str(row.get('name', '')).casefold(), row['id'])
    else:

        def include(record):
            return (
                (not status or record.get('status') == status)
                and (not pc or pc in record.get('pcs', []))
                and (
                    not query
                    or query
                    in ' '.join(
                        str(record.get(k, '')) for k in ('id', 'title', 'detail', 'source')
                    ).casefold()
                )
            )

        def summary(record):
            return {
                'record': record,
                'rev': str(os.stat(record_path(data, kind, record['id'])).st_mtime_ns),
            }

        sort_key = lambda row: (str(row.get('title', '')).casefold(), row['id'])
        if sort == 'stale':
            sort_key = lambda row: (
                last_session(row),
                str(row.get('title', '')).casefold(),
                row['id'],
            )
    found = sorted((record for record in all_records(data, kind) if include(record)), key=sort_key)
    return {
        'items': [summary(row) for row in found[offset : offset + limit]],
        'total': len(found),
        'offset': offset,
        'limit': limit,
    }
