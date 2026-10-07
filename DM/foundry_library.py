"""Read-only discovery of local Foundry worlds, media and document snapshots.

Documents come from the world's own database files (`read_world`) or from a snapshot the GM exports
with a macro. Both produce the same validated snapshot, which is stored under `DM/data` and browsed.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import config
import foundry_leveldb
import shapes
import storage

SNAPSHOT_FORMAT = 'campaign-studio-foundry-library'
KINDS = ('scenes', 'journals', 'actors', 'items')
# snapshot kind -> (database name in the world's data folder, Foundry document type)
COLLECTIONS = {
    'scenes': ('scenes', 'Scene'),
    'journals': ('journal', 'JournalEntry'),
    'actors': ('actors', 'Actor'),
    'items': ('items', 'Item'),
}
EMBEDDED = {'journal': ('pages',)}
MAX_DOCUMENTS = 5000
MAX_NEDB_BYTES = 256 * 1024 * 1024
MEDIA = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.mp3': 'audio/mpeg',
    '.ogg': 'audio/ogg',
    '.wav': 'audio/wav',
    '.mp4': 'video/mp4',
    '.webm': 'video/webm',
}
MAX_MEDIA_FILES = 20000


def _root(path):
    candidate = Path(str(path or '')).expanduser().resolve()
    if candidate.name.lower() == 'data':
        candidate = candidate.parent
    elif (
        candidate.parent.name.lower() == 'worlds' and candidate.parent.parent.name.lower() == 'data'
    ):
        candidate = candidate.parent.parent.parent
    if not (candidate / 'Data' / 'worlds').is_dir():
        raise ValueError('Choose the Foundry User Data folder containing Data/worlds.')
    return candidate


def suggested_roots():
    candidates = []
    saved = config.settings().get('world_path')
    if saved:
        candidates.append(saved)
    if os.environ.get('FOUNDRY_DATA'):
        candidates.append(os.environ['FOUNDRY_DATA'])
    if sys.platform == 'win32' and os.environ.get('LOCALAPPDATA'):
        candidates.append(Path(os.environ['LOCALAPPDATA']) / 'FoundryVTT')
    elif sys.platform == 'darwin':
        candidates.append(Path.home() / 'Library' / 'Application Support' / 'FoundryVTT')
    else:
        candidates.extend((Path.home() / '.local/share/FoundryVTT', Path.home() / 'FoundryVTT'))
    roots = []
    for candidate in candidates:
        try:
            root = str(_root(candidate))
        except (ValueError, OSError):
            continue
        if root not in roots:
            roots.append(root)
    return roots


def discover(path=None):
    roots = suggested_roots()
    chosen = _root(path) if path else Path(roots[0]) if roots else None
    worlds = []
    if chosen:
        base = chosen / 'Data' / 'worlds'
        for folder in sorted(base.iterdir(), key=lambda p: p.name.casefold())[:500]:
            manifest = folder / 'world.json'
            if (
                not folder.is_dir()
                or folder.is_symlink()
                or manifest.is_symlink()
                or not manifest.is_file()
            ):
                continue
            try:
                if manifest.stat().st_size > 256 * 1024:
                    continue
                worlds.append(config.world_info(str(folder)))
            except (ValueError, OSError):
                continue
    return {'root': str(chosen) if chosen else '', 'roots': roots, 'worlds': worlds}


def selected_world():
    path = config.settings().get('world_path')
    return config.world_info(path) if path else None


def media_path(relative, world):
    parts = storage.posix_parts(relative, 'Invalid Foundry asset path.')
    if parts[0].lower() in ('systems', 'modules'):
        raise ValueError('System and module files are outside this world library.')
    if parts[0].lower() == 'worlds' and (len(parts) < 3 or parts[1] != world['id']):
        raise ValueError('This asset belongs to another Foundry world.')
    if Path(parts[-1]).suffix.lower() not in MEDIA:
        raise ValueError('Only supported media files can be previewed.')
    base = _root(world['path']) / 'Data'
    candidate = base.joinpath(*parts)
    current = base
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Linked asset paths are not supported.')
    if not candidate.is_file() or not candidate.resolve().is_relative_to(base.resolve()):
        raise ValueError('Foundry asset not found.')
    return candidate


def world_key(world):
    """Stable, opaque identity for one selected world folder (not just its reusable Foundry UUIDs)."""
    source = f'{os.path.normcase(str(Path(world["path"]).resolve()))}\n{world["id"]}'
    return hashlib.sha256(source.encode('utf-8')).hexdigest()[:24]


def media_file(relative, expected_world_key=''):
    world = selected_world()
    if not world:
        raise ValueError('Connect a Foundry world first.')
    if expected_world_key and expected_world_key != world_key(world):
        raise ValueError('This image belongs to a different connected world.')
    path = media_path(relative, world)
    return path, MEDIA[path.suffix.lower()]


def assets(world, query='', offset=0, limit=60):
    base = _root(world['path']) / 'Data'
    query = str(query or '').casefold()
    matches = []
    scanned = 0
    truncated = False
    for folder, dirs, files in os.walk(base, followlinks=False):
        relative = Path(folder).relative_to(base).parts
        dirs[:] = [
            name
            for name in dirs
            if not (Path(folder) / name).is_symlink()
            and not (
                not relative
                and name.lower() in ('systems', 'modules')
                or relative == ('worlds',)
                and name != world['id']
            )
        ]
        for filename in files:
            if Path(filename).suffix.lower() not in MEDIA:
                continue
            scanned += 1
            if scanned > MAX_MEDIA_FILES:
                truncated = True
                break
            file = Path(folder) / filename
            if file.is_symlink():
                continue
            rel = file.relative_to(base).as_posix()
            if query in rel.casefold():
                matches.append({'path': rel, 'name': filename, 'type': MEDIA[file.suffix.lower()]})
        if truncated:
            break
    matches.sort(key=lambda item: item['path'].casefold())
    return {
        'items': matches[offset : offset + limit],
        'total': len(matches),
        'truncated': truncated,
    }


def _short(value, limit):
    return str(value or '')[:limit]


def normalize_snapshot(payload, world, origin='macro', stamp='', omitted=None):
    if not isinstance(payload, dict) or payload.get('format') != SNAPSHOT_FORMAT:
        raise ValueError('Choose a Campaign Studio Foundry library snapshot.')
    if payload.get('schema') != 1 or not isinstance(payload.get('documents'), dict):
        raise ValueError('Unsupported Foundry library snapshot version.')
    source = payload.get('world')
    if not isinstance(source, dict) or source.get('id') != world['id']:
        raise ValueError('This snapshot belongs to a different Foundry world.')
    if source.get('system') != world['system'] or source.get('title') != world['title']:
        raise ValueError('The Foundry world details have changed. Export a fresh snapshot.')
    documents = {}
    for kind in KINDS:
        records = payload['documents'].get(kind, [])
        if not isinstance(records, list):
            raise ValueError('The Foundry snapshot contains an invalid document list.')
        if len(records) > MAX_DOCUMENTS:
            # Keep a stable subset and say so, as the world-folder reader does.
            records = sorted(
                records,
                key=lambda item: (
                    str(item.get('name', '')).casefold() if isinstance(item, dict) else '',
                    str(item.get('id', '')) if isinstance(item, dict) else '',
                ),
            )
            omitted = dict(omitted or {})
            omitted[kind] = omitted.get(kind, 0) + len(records) - MAX_DOCUMENTS
            records = records[:MAX_DOCUMENTS]
        cleaned = []
        seen = set()
        for item in records:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                raise ValueError('The Foundry snapshot contains an invalid document.')
            ident = item['id']
            if not ident or len(ident) > 128 or ident in seen:
                raise ValueError('The Foundry snapshot contains a duplicate or invalid ID.')
            seen.add(ident)
            pages = item.get('pages', [])
            if not isinstance(pages, list) or len(pages) > 500:
                raise ValueError('The Foundry snapshot contains too many journal pages.')
            cleaned.append(
                {
                    'id': ident,
                    'uuid': _short(item.get('uuid'), 256),
                    'name': _short(item.get('name'), 300),
                    'folder': _short(item.get('folder'), 300),
                    'type': _short(item.get('type'), 80),
                    'image': _short(item.get('image'), 1000),
                    'summary': _short(item.get('summary'), 20000),
                    'compendium': item.get('compendium') is True,
                    'pages': [
                        {
                            'id': _short(page.get('id'), 128),
                            'name': _short(page.get('name'), 300),
                            'text': _short(page.get('text'), 100000),
                            'image': _short(page.get('image'), 1000),
                        }
                        for page in pages
                        if isinstance(page, dict)
                    ],
                }
            )
        documents[kind] = cleaned
    return {
        'format': SNAPSHOT_FORMAT,
        'schema': 1,
        'world': {
            'id': world['id'],
            'title': world['title'],
            'system': world['system'],
            'path': world['path'],
            'core_version': _short(source.get('coreVersion'), 80),
        },
        'exported_at': _short(payload.get('exportedAt'), 80),
        'source': origin,
        'stamp': stamp,
        'omitted': omitted or {},
        'documents': documents,
    }


class _PlainText(HTMLParser):
    BLOCKS = frozenset(('p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote'))

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag == 'br':
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        elif tag in self.BLOCKS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain_text(html, limit):
    if not isinstance(html, str):
        return ''
    parser = _PlainText()
    parser.feed(html[: limit * 4])
    parser.close()
    return re.sub(r'\n{3,}', '\n\n', ''.join(parser.parts)).strip()[:limit]


def _dig(document, *paths):
    """The first value present along any of the key paths, like chained `?.` and `??` in the macro."""
    for path in paths:
        value = document
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None:
            return value
    return None


def _database_files(world, name):
    base = Path(world['path']) / 'data'
    folder = base / name
    legacy = base / (name + '.db')
    if folder.is_dir() and not folder.is_symlink():
        return folder
    if legacy.is_file() and not legacy.is_symlink():
        return legacy
    return None


def source_stamp(world):
    """A fingerprint of the world's document databases, or None when it has none to read."""
    digest = hashlib.sha256()
    found = False
    for name in ('folders', *(database for database, _ in COLLECTIONS.values())):
        source = _database_files(world, name)
        if not source:
            continue
        found = found or name != 'folders'
        try:
            files = [source] if source.is_file() else sorted(source.iterdir())
            for file in files:
                if (
                    file.suffix in ('.ldb', '.sst', '.log', '.db')
                    or file.name == 'CURRENT'
                    or file.name.startswith('MANIFEST-')
                ) and not file.is_symlink():
                    info = file.stat()
                    digest.update(
                        f'{name}/{file.name}:{info.st_size}:{info.st_mtime_ns}\n'.encode()
                    )
        except FileNotFoundError:
            digest.update(f'{name}:changing\n'.encode())  # Foundry replaced a file as we looked
    return digest.hexdigest() if found else None


def _documents(world, name):
    """Top-level documents by ID and their embedded documents by parent ID, or None if absent."""
    source = _database_files(world, name)
    if not source:
        return None
    documents = {}
    children = {}
    if source.is_dir():
        prefixes = [f'!{name}!'] + [f'!{name}.{sub}!' for sub in EMBEDDED.get(name, ())]
        rows = foundry_leveldb.read(source, tuple(prefix.encode() for prefix in prefixes))
        for key, value in rows.items():
            try:
                document = json.loads(value.decode('utf-8'))
            except ValueError:
                continue
            _, scope, identity = key.decode('utf-8', 'replace').split('!', 2)
            if not isinstance(document, dict):
                continue
            if scope == name:
                documents[identity] = {**document, '_id': identity}
            else:
                parent, _, child = identity.partition('.')
                children.setdefault(parent, {})[child] = {**document, '_id': child}
        return documents, children
    # Foundry 10 and earlier keep one JSON document per line; a later line replaces an earlier one.
    if source.stat().st_size > MAX_NEDB_BYTES:
        raise ValueError('A Foundry database file is unexpectedly large.')
    for line in source.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            document = json.loads(line)
        except ValueError:
            continue
        if not isinstance(document, dict) or not isinstance(document.get('_id'), str):
            continue
        if document.get('$$deleted'):
            documents.pop(document['_id'], None)
        else:
            documents[document['_id']] = document
    return documents, children


def _journal_pages(document, children):
    pages = document.get('pages')
    if not isinstance(pages, list):
        pages = []
    found = [children.get(page) if isinstance(page, str) else page for page in pages]
    found = [page for page in found if isinstance(page, dict)]
    found.sort(key=lambda page: page.get('sort') if isinstance(page.get('sort'), int) else 0)
    result = []
    for page in found:
        text = page.get('text') if isinstance(page.get('text'), dict) else {}
        result.append(
            {
                'id': page.get('_id'),
                'name': page.get('name'),
                'text': _plain_text(text.get('content') or text.get('markdown'), 100000),
                'image': page.get('src') or '',
            }
        )
    return result


def _record(kind, document, children, folders):
    identity = document.get('_id')
    folder = folders.get(document.get('folder'))
    record = {
        'id': identity,
        'uuid': f'{COLLECTIONS[kind][1]}.{identity}',
        'name': document.get('name'),
        'folder': folder if isinstance(folder, str) else '',
        'type': document.get('type') if isinstance(document.get('type'), str) else '',
        'image': document.get('img') or '',
        'summary': '',
        # Copies of compendium documents (gear, spells, stock monsters) rarely need campaign notes.
        'compendium': bool(
            _dig(document, ('_stats', 'compendiumSource'), ('flags', 'core', 'sourceId'))
        ),
    }
    if kind == 'scenes':
        record['image'] = _dig(document, ('background', 'src'), ('thumb',)) or ''
        record['summary'] = _plain_text(document.get('description'), 20000)
    elif kind == 'journals':
        record['pages'] = _journal_pages(document, children.get(identity, {}))
    elif kind == 'actors':
        record['summary'] = _plain_text(
            _dig(
                document,
                ('system', 'details', 'biography', 'value'),
                ('system', 'description', 'value'),
                ('system', 'details', 'description'),
            ),
            20000,
        )
    else:
        record['summary'] = _plain_text(_dig(document, ('system', 'description', 'value')), 20000)
    return record


def read_world(world):
    """Read scenes, journals, actors and items from the world's own database files."""
    stamp = source_stamp(world)
    if stamp is None:
        raise ValueError(
            'This world has no readable document databases. Use the export macro instead.'
        )
    try:
        folders = {
            identity: document.get('name')
            for identity, document in (_documents(world, 'folders') or ({}, {}))[0].items()
        }
        documents = {}
        omitted = {}
        for kind, (name, _) in COLLECTIONS.items():
            found, children = _documents(world, name) or ({}, {})
            records = [
                _record(kind, document, children, folders)
                for document in found.values()
                if isinstance(document.get('name'), str)
            ]
            records.sort(key=lambda record: (record['name'].casefold(), record['id']))
            if len(records) > MAX_DOCUMENTS:
                omitted[kind] = len(records) - MAX_DOCUMENTS
            documents[kind] = records[:MAX_DOCUMENTS]
    except OSError as error:
        raise ValueError(
            'The world files could not be read. Close Foundry and try again, or use the '
            'export macro instead.'
        ) from error
    payload = {
        'format': SNAPSHOT_FORMAT,
        'schema': 1,
        'world': {
            'id': world['id'],
            'title': world['title'],
            'system': world['system'],
            'coreVersion': world['foundry_version'],
        },
        'exportedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'documents': documents,
    }
    return normalize_snapshot(payload, world, origin='folder', stamp=stamp, omitted=omitted)


def current_snapshot(saved, world):
    if not isinstance(saved, dict) or not world:
        return None
    source = saved.get('world') or {}
    if not isinstance(source, dict) or not isinstance(saved.get('documents'), dict):
        return None
    return (
        saved if source.get('path') == world['path'] and source.get('id') == world['id'] else None
    )


def library(saved, kind='scenes', query='', offset=0, limit=60):
    world = selected_world()
    if not world:
        return {'world': None, 'snapshot': None, 'counts': {}, 'items': [], 'total': 0}
    snapshot = current_snapshot(saved, world)
    if kind not in (*KINDS, 'assets'):
        raise ValueError('Choose a World Library category.')
    if offset < 0 or limit < 1 or limit > 100:
        raise ValueError('Invalid World Library page.')
    if kind == 'assets':
        listing = assets(world, query, offset, limit)
    else:
        collection = (snapshot or {}).get('documents', {}).get(kind, [])
        matches = [
            item
            for item in collection
            if query.casefold()
            in (item['name'] + ' ' + item['folder'] + ' ' + item['summary']).casefold()
        ]
        listing = {'items': matches[offset : offset + limit], 'total': len(matches)}
    stamp = source_stamp(world)
    return {
        'world': world,
        'readable': stamp is not None,
        'snapshot': {
            'exported_at': snapshot['exported_at'],
            'core_version': snapshot['world']['core_version'],
            'source': snapshot.get('source', 'macro'),
            'stale': snapshot.get('source') == 'folder' and snapshot.get('stamp') != stamp,
            'omitted': snapshot.get('omitted', {}),
        }
        if snapshot
        else None,
        'counts': {
            category: len((snapshot or {}).get('documents', {}).get(category, []))
            for category in KINDS
        },
        'folders': import_folders(snapshot),
        **listing,
    }


# Foundry documents Studio turns into codex entries: (snapshot kind, codex type for a given type)
CODEX_IMPORTS = (
    ('actors', lambda document: 'pc' if document['type'] == 'character' else 'npc'),
    ('items', lambda document: 'item'),
    ('scenes', lambda document: 'place'),
)
IMPORTED_FIELDS = ('name', 'group', 'notes', 'image')


def import_folders(snapshot):
    """Folders of importable documents for the picker: name, document count and how many are compendium copies.

    `suggested` matches what an import without a folder list brings in: folders holding any document that
    is not a compendium copy.
    """
    found = {}
    for kind, _ in CODEX_IMPORTS:
        for document in (snapshot or {}).get('documents', {}).get(kind, []):
            row = found.setdefault(
                document['folder'], {'name': document['folder'], 'count': 0, 'compendium': 0}
            )
            row['count'] += 1
            row['compendium'] += 1 if document.get('compendium') is True else 0
    return [
        {**row, 'suggested': row['compendium'] < row['count']}
        for row in sorted(found.values(), key=lambda row: row['name'].casefold())
    ]


def import_into_codex(snapshot, codex, folders=None):
    """Add or refresh codex entries for the snapshot's actors, items and scenes.

    `folders` is the list of folder names to import ('' is the unfiled documents). Without it, everything
    except documents copied from a compendium is imported. Skipped documents stay in the snapshot, which
    is the searchable reference library, and existing entries for them are left alone.

    Each entry remembers its source world, canonical Foundry UUID and last imported values. A later import
    refreshes an entry only while it still holds those values, so anything edited in Studio is kept.
    Legacy entries without a source world are left alone rather than guessed from a reusable UUID.
    """
    entries = codex.setdefault('entries', [])
    source_key = world_key(snapshot['world'])
    known = {
        entry['foundry']['uuid']: entry
        for entry in entries
        if isinstance(entry.get('foundry'), dict)
        and entry['foundry'].get('world_key') == source_key
        and isinstance(entry['foundry'].get('uuid'), str)
    }
    taken = {entry.get('id') for entry in entries}
    chosen = None if folders is None else set(folders)
    report = {'added': 0, 'updated': 0, 'kept': 0, 'unchanged': 0, 'skipped': 0}
    for kind, entry_type in CODEX_IMPORTS:
        for document in snapshot['documents'].get(kind, []):
            if chosen is None:
                selected = document.get('compendium') is not True
            else:
                selected = document['folder'] in chosen
            if not selected:
                report['skipped'] += 1
                continue
            image = document['image']
            if image:
                try:
                    media_path(image, snapshot['world'])
                except (OSError, ValueError):
                    image = ''  # Remote, missing and unsupported Foundry assets cannot be served.
            values = {
                'name': document['name'],
                'group': document['folder'],
                'notes': document['summary'],
                'image': image,
            }
            uuid = f'{COLLECTIONS[kind][1]}.{document["id"]}'
            entry = known.get(uuid)
            if entry is None:
                base = re.sub(r'[^a-z0-9]+', '-', ('fvtt-' + document['id']).lower()).strip('-')
                identity = base
                while identity in taken:
                    identity += '-x'
                taken.add(identity)
                entry = shapes.CODEX_ENTRY.new(
                    id=identity,
                    type=entry_type(document),
                    **values,
                    foundry={'uuid': uuid, 'world_key': source_key, 'imported': values},
                )
                entries.append(entry)
                known[uuid] = entry
                report['added'] += 1
                continue
            last = entry['foundry'].get('imported') or {}
            if values == last:
                report['unchanged'] += 1
            elif all(entry.get(field) == last.get(field) for field in IMPORTED_FIELDS):
                entry.update(values)
                entry['foundry']['imported'] = values
                report['updated'] += 1
            else:
                report['kept'] += 1
    return report
