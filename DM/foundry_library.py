"""Read-only discovery of local Foundry worlds, media and GM-exported document snapshots."""

import os
import sys
from pathlib import Path, PurePosixPath

import config

SNAPSHOT_FORMAT = 'campaign-studio-foundry-library'
KINDS = ('scenes', 'journals', 'actors', 'items')
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


def _media_path(relative, world):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise ValueError('Invalid Foundry asset path.')
    parts = PurePosixPath(relative).parts
    if not parts or any(part in ('', '.', '..') for part in parts) or relative.startswith('/'):
        raise ValueError('Invalid Foundry asset path.')
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


def media_file(relative):
    world = selected_world()
    if not world:
        raise ValueError('Connect a Foundry world first.')
    path = _media_path(relative, world)
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


def normalize_snapshot(payload, world):
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
        if not isinstance(records, list) or len(records) > 5000:
            raise ValueError('The Foundry snapshot has too many documents.')
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
        'documents': documents,
    }


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
    return {
        'world': world,
        'snapshot': {
            'exported_at': snapshot['exported_at'],
            'core_version': snapshot['world']['core_version'],
        }
        if snapshot
        else None,
        'counts': {
            category: len((snapshot or {}).get('documents', {}).get(category, []))
            for category in KINDS
        },
        **listing,
    }
