"""Read existing campaign memory into bounded, reviewable import candidates.

Preview never writes. Apply rebuilds the candidates from their source and compares the
fingerprint, so a browser cannot substitute text or apply a preview after files change.
Imported text is reference data; it is never executed or passed to a provider here.
"""

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

import campaign
import foundry_library
import records
import shapes

MAX_FILES = 5000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_CANDIDATES = 5000
MAX_TEXT = 20000
SESSION_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')
NUMBER = re.compile(r'(?:session[-_ ]?|s)([0-9]+)\Z', re.I)
TYPES = {'pc', 'npc', 'god', 'place', 'faction', 'item', 'monster', 'lore'}
STATUSES = {'open', 'planned', 'foreshadowed', 'resolved'}


def _short(value, limit=MAX_TEXT):
    return value[:limit] if isinstance(value, str) else ''


def _path(value, directory=True):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError('Choose a local source path.')
    original = Path(value).expanduser()
    if original.is_symlink():
        raise ValueError('Linked source paths are not supported.')
    path = original.resolve(strict=True)
    if directory and not path.is_dir() or not directory and not path.is_file():
        raise ValueError('Choose an existing local ' + ('folder.' if directory else 'file.'))
    return path


class Reader:
    def __init__(self):
        self.count = 0
        self.bytes = 0

    def text(self, path):
        if path.is_symlink() or not path.is_file():
            raise ValueError('Linked or missing source file: ' + path.name)
        size = path.stat().st_size
        self.count += 1
        self.bytes += size
        if self.count > MAX_FILES or size > MAX_FILE_BYTES or self.bytes > MAX_TOTAL_BYTES:
            raise ValueError('The source exceeds the 5,000-file / 40 MB preview limit.')
        with path.open('rb') as stream:
            body = stream.read(MAX_FILE_BYTES + 1)
        if len(body) > MAX_FILE_BYTES:
            raise ValueError('A source file grew beyond the 20 MB file limit.')
        self.bytes += len(body) - size
        if self.bytes > MAX_TOTAL_BYTES:
            raise ValueError('The source exceeds the 40 MB preview limit.')
        return body.decode('utf-8-sig')

    def json(self, path):
        try:
            return json.loads(self.text(path))
        except json.JSONDecodeError as error:
            raise ValueError(f'{path.name} is not valid JSON: {error.msg}') from error


def _key(source, kind, identity):
    return hashlib.sha256(f'{source}\n{kind}\n{identity}'.encode()).hexdigest()[:24]


def _candidate(source, kind, identity, title, record, preview):
    key = _key(source, kind, identity)
    return {
        'key': key,
        'kind': kind,
        'source_id': identity,
        'title': _short(title, 300) or identity,
        'preview': _short(preview),
        'record': record,
    }


def _record_candidates(reader, folder, source, kind):
    field = records.FIELDS[kind]
    per_record = folder / kind
    legacy = folder / (kind + '.json')
    if per_record.is_dir() and not per_record.is_symlink():
        files = sorted(per_record.glob('*.json'))
        rows = [reader.json(path) for path in files]
    elif legacy.is_file():
        payload = reader.json(legacy)
        rows = payload.get(field) if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError(f'{legacy.name} has no {field} list.')
    else:
        rows = []
    result, seen = [], set()
    item_kind = 'codex' if kind == 'codex' else 'thread'
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            raise ValueError(f'A {kind} source record has no ID.')
        identity = row['id']
        if identity in seen or len(identity.encode('utf-8')) > 1024:
            raise ValueError(f'Duplicate or oversized {kind} source ID.')
        seen.add(identity)
        key = _key(source, item_kind, identity)
        provenance = {'source': source, 'id': identity}
        if kind == 'codex':
            entry_type = row.get('type')
            record = shapes.CODEX_ENTRY.new(
                id='memory-' + key,
                type=entry_type if isinstance(entry_type, str) and entry_type in TYPES else 'lore',
                name=_short(row.get('name'), 300) or identity,
                group=_short(row.get('group'), 300),
                status=_short(row.get('status'), 80),
                public=_short(row.get('public')),
                secrets=_short(row.get('secrets')),
                notes=_short(row.get('notes')),
                tags=[
                    _short(tag, 100) for tag in (row.get('tags') or [])[:30] if isinstance(tag, str)
                ]
                if isinstance(row.get('tags'), list)
                else [],
                memory_import=provenance,
            )
            related = row.get('related')
            if isinstance(related, list):
                record['related'] = [rid for rid in related[:100] if isinstance(rid, str)]
            preview = '\n'.join(
                filter(None, (record['public'], record['secrets'], record['notes']))
            )
            title = record['name']
        else:
            status = row.get('status')
            record = shapes.THREAD.new(
                id='memory-' + key,
                title=_short(row.get('title'), 300) or identity,
                status=status if isinstance(status, str) and status in STATUSES else 'open',
                detail=_short(row.get('detail')),
                source='Imported campaign memory',
                memory_import=provenance,
            )
            pcs = row.get('pcs')
            if isinstance(pcs, list):
                record['pcs'] = [rid for rid in pcs[:100] if isinstance(rid, str)]
            preview = record['detail']
            title = record['title']
        result.append(_candidate(source, item_kind, identity, title, record, preview))
    return result


def _session_number(identity, value=None):
    if isinstance(value, dict) and type(value.get('n')) is int and 0 <= value['n'] <= 99999:
        return value['n']
    if not isinstance(identity, str):
        return 0
    matched = NUMBER.fullmatch(identity)
    return int(matched.group(1)) if matched else 0


def _session_candidate(source, identity, title, log, n=0):
    if isinstance(identity, str) and (number := NUMBER.fullmatch(identity.strip())):
        identity = f's{int(number.group(1))}'
    if not isinstance(identity, str) or not SESSION_ID.fullmatch(identity):
        raise ValueError('A session summary needs an ID such as s1 or session-1.')
    if not isinstance(log, dict):
        raise ValueError('A session summary must be an object.')
    outcomes = log.get('outcomes') or []
    if not isinstance(outcomes, list) or any(not isinstance(item, str) for item in outcomes):
        raise ValueError('Session outcomes must be a list of text.')
    clean = {
        'summary': _short(log.get('summary')),
        'notes': _short(log.get('notes')),
        'outcomes': [_short(item, 1000) for item in outcomes[:50]],
    }
    if not clean['summary'] and not clean['notes'] and not clean['outcomes']:
        return None
    record = {'id': identity, 'n': n, 'title': _short(title, 300) or identity, 'log': clean}
    return _candidate(
        source,
        'session',
        identity,
        record['title'],
        record,
        '\n'.join(filter(None, (clean['summary'], clean['notes'], *clean['outcomes']))),
    )


def _legacy(folder):
    root = _path(folder)
    data = root / 'DM' / 'data' if (root / 'DM' / 'data').is_dir() else root / 'data'
    if not data.is_dir() and root.name.lower() == 'data':
        data = root
    if not data.is_dir() or data.is_symlink():
        raise ValueError('Choose a Campaign Studio folder containing DM/data or data.')
    if data.resolve() == Path(campaign.active().data).resolve():
        raise ValueError('Choose an older campaign folder, not the campaign currently open.')
    source = hashlib.sha256(str(data.resolve()).encode()).hexdigest()[:24]
    reader = Reader()
    candidates = _record_candidates(reader, data, source, 'codex')
    candidates += _record_candidates(reader, data, source, 'threads')
    prep = data / 'prep'
    if prep.is_dir() and not prep.is_symlink():
        for path in sorted(prep.glob('*.json')):
            value = reader.json(path)
            if not isinstance(value, dict):
                raise ValueError(f'{path.name} is not a session prep.')
            log = value.get('log')
            if not isinstance(log, dict) and value.get('status') == 'played':
                log = {'summary': value.get('recap') or '', 'notes': value.get('notes') or ''}
            if isinstance(log, dict):
                item = _session_candidate(
                    source,
                    path.stem,
                    value.get('title'),
                    log,
                    _session_number(path.stem, value),
                )
                if item:
                    candidates.append(item)
    if not candidates:
        raise ValueError('No codex, threads or session logs found in that campaign folder.')
    return candidates


def _summary_json(source, name, value):
    if isinstance(value, dict) and isinstance(value.get('sessions'), list):
        return [
            _summary_json(source, f'{name}-{index}', row)
            for index, row in enumerate(value['sessions'])
        ]
    if isinstance(value, list):
        return [_summary_json(source, f'{name}-{index}', row) for index, row in enumerate(value)]
    if not isinstance(value, dict):
        raise ValueError('Each JSON session summary must be an object.')
    identity = value.get('session') or value.get('session_id') or value.get('id') or name
    if type(identity) is int and 0 <= identity <= 99999:
        identity = f's{identity}'
    log = value.get('log') if isinstance(value.get('log'), dict) else value
    item = _session_candidate(
        source,
        identity,
        value.get('title'),
        log,
        _session_number(identity, value),
    )
    return [item] if item else []


def _summaries(path):
    source_path = _path(path, directory=Path(path).is_dir())
    files = (
        sorted(p for p in source_path.iterdir() if p.suffix.lower() in ('.json', '.md'))
        if source_path.is_dir()
        else [source_path]
    )
    if not files:
        raise ValueError('No JSON or Markdown summaries found.')
    source = hashlib.sha256(str(source_path.resolve()).encode()).hexdigest()[:24]
    reader = Reader()
    candidates = []
    for file in files:
        if file.suffix.lower() == '.json':
            candidates.extend(_flatten(_summary_json(source, file.stem, reader.json(file))))
        elif file.suffix.lower() == '.md':
            content = reader.text(file).strip()
            lines = content.splitlines()
            title = (
                lines[0].lstrip('# ').strip() if lines and lines[0].startswith('# ') else file.stem
            )
            body = '\n'.join(lines[1:]).strip() if lines and lines[0].startswith('# ') else content
            item = _session_candidate(
                source, file.stem, title, {'summary': body}, _session_number(file.stem)
            )
            if item:
                candidates.append(item)
        else:
            raise ValueError('Choose JSON or Markdown session summaries.')
    if not candidates:
        raise ValueError('No session summaries with text found.')
    return candidates


def _flatten(items):
    for item in items:
        if isinstance(item, list):
            yield from _flatten(item)
        else:
            yield item


def lore_folders(snapshot):
    if not snapshot:
        return []
    found = {}
    for journal in snapshot.get('documents', {}).get('journals', []):
        key = journal.get('folder_id') or 'path:' + (
            journal.get('folder_path') or journal.get('folder') or ''
        )
        row = found.setdefault(
            key,
            {
                'id': key,
                'name': journal.get('folder_path') or journal.get('folder') or 'Unfiled',
                'count': 0,
            },
        )
        row['count'] += 1
    return sorted(found.values(), key=lambda row: row['name'].casefold())


def _lore(snapshot, folders):
    if not snapshot:
        raise ValueError('Read or import a Foundry World Library snapshot first.')
    if not isinstance(folders, list) or not folders or any(not isinstance(x, str) for x in folders):
        raise ValueError('Choose one or more Foundry journal folders.')
    available = {row['id'] for row in lore_folders(snapshot)}
    if not set(folders) <= available:
        raise ValueError('The chosen Foundry journal folder is no longer available.')
    world = snapshot['world']
    source = foundry_library.world_key(world)
    candidates = []
    for journal in snapshot['documents'].get('journals', []):
        folder = journal.get('folder_id') or 'path:' + (
            journal.get('folder_path') or journal.get('folder') or ''
        )
        if folder not in folders:
            continue
        identity = 'JournalEntry.' + journal['id']
        pieces = []
        if journal.get('summary'):
            pieces.append(journal['summary'])
        for page in journal.get('pages', []):
            if page.get('text'):
                pieces.append(page.get('name', '') + '\n' + page['text'])
        notes = _short('\n\n'.join(pieces))
        if not notes:
            continue
        key = _key(source, 'lore', identity)
        record = shapes.CODEX_ENTRY.new(
            id='memory-' + key,
            type='lore',
            name=_short(journal.get('name'), 300) or identity,
            group=_short(journal.get('folder_path') or journal.get('folder'), 300),
            notes=notes,
            foundry={'uuid': identity, 'world_key': source},
            memory_import={'source': source, 'id': identity},
        )
        candidates.append(_candidate(source, 'codex', identity, record['name'], record, notes))
    if not candidates:
        raise ValueError('The selected folders have no journal text to import.')
    return candidates


def proposal(kind, path='', folders=None, snapshot=None):
    if kind == 'folder':
        candidates = _legacy(path)
    elif kind == 'summaries':
        candidates = _summaries(path)
    elif kind == 'lore':
        candidates = _lore(snapshot, folders)
    else:
        raise ValueError('Choose a campaign folder, session summaries or Foundry lore.')
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError(
            'The source has more than 5,000 import candidates. Choose a smaller folder.'
        )
    keys = [item['key'] for item in candidates]
    if len(keys) != len(set(keys)):
        raise ValueError('The source contains duplicate record or session IDs.')
    fingerprint = hashlib.sha256(
        json.dumps(candidates, sort_keys=True, ensure_ascii=False).encode('utf-8')
    ).hexdigest()
    return {'fingerprint': fingerprint, 'items': candidates}


def changes(proposal_value, selected, read_doc, data):
    """Build a journal-ready, collision-safe change from selected candidates."""
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(key, str) for key in selected)
        or len(selected) != len(set(selected))
    ):
        raise ValueError('Select at least one distinct candidate to import.')
    selected_keys = set(selected)
    available = {item['key']: item for item in proposal_value['items']}
    if not selected_keys <= available.keys():
        raise ValueError('The selection does not belong to this preview.')
    chosen = [available[key] for key in selected]
    source_ids = {
        (item['kind'], item['source_id']): item['record']['id']
        for item in proposal_value['items']
        if item['kind'] in ('codex', 'thread')
        and (
            item['key'] in selected_keys
            or records.read(
                data, 'codex' if item['kind'] == 'codex' else 'threads', item['record']['id']
            )
        )
    }
    result = []
    report = {'added': 0, 'filled': 0, 'skipped': 0}
    for item in chosen:
        kind = item['kind']
        row = deepcopy(item['record'])
        if kind in ('codex', 'thread'):
            storage_kind = 'codex' if kind == 'codex' else 'threads'
            if records.read(data, storage_kind, row['id']) is not None:
                report['skipped'] += 1
                continue
            if kind == 'codex' and isinstance(row.get('related'), list):
                row['related'] = [
                    source_ids[('codex', ident)]
                    for ident in row['related']
                    if ('codex', ident) in source_ids
                ]
            if kind == 'thread':
                row['pcs'] = [
                    source_ids[('codex', ident)]
                    for ident in row['pcs']
                    if ('codex', ident) in source_ids
                ]
            result.append((records.document_name(storage_kind, row['id']), row))
            report['added'] += 1
        else:
            name = 'prep/' + row['id']
            current = read_doc(name)
            if current is None:
                current = shapes.PREP.new(n=row['n'], title=row['title'])
                report['added'] += 1
            else:
                previous = current.get('log') or {}
                if previous.get('summary') or previous.get('notes') or previous.get('outcomes'):
                    report['skipped'] += 1
                    continue
                current = deepcopy(current)
                report['filled'] += 1
            current['log'] = row['log']
            result.append((name, current))
    return result, report
