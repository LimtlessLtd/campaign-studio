"""Campaign data schema version, migrations and verified pre-migration backups.

DM/data/.schema.json records the version of the campaign's documents. Campaign data written before
versioning (0.1.x) is version 0. A campaign saved by a newer Campaign Studio is refused, so an older
build never rewrites documents it does not understand. Each migration is idempotent: if it is
interrupted, the version is unchanged and the next start runs it again from a fresh backup.
"""

import contextlib
import datetime
import glob
import json
import os
import re
import shutil

import shapes
import storage
import records

FORMAT = 'campaign-studio-data'
CURRENT = 10
MARKER = '.schema.json'
PENDING = '.migration-pending.json'  # in the backups folder while a migration is writing documents
SKIPPED_DATA = {'.history', '.commits', 'jobs'}  # never rewritten by a migration


class SchemaError(ValueError):
    pass


def marker_path(data):
    return os.path.join(data, MARKER)


def campaign_files(data, maps):
    """Documents a migration may rewrite: JSON under data, plus each map's key."""
    files = []
    if os.path.isdir(data):
        for folder, dirs, names in os.walk(data):
            dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DATA)
            files += [
                os.path.join(folder, name)
                for name in sorted(names)
                if name.endswith('.json') and name != MARKER
            ]
    files += sorted(glob.glob(os.path.join(glob.escape(maps), '*', 'key.json')))
    return files


def version(data, maps):
    """The saved version, 0 for unversioned existing data, or None for a new campaign."""
    try:
        with open(marker_path(data), encoding='utf-8') as file:
            saved = json.load(file)
    except FileNotFoundError:
        return 0 if campaign_files(data, maps) else None
    except ValueError as error:
        raise SchemaError(f'{marker_path(data)} is unreadable: {error}') from error
    if (
        not isinstance(saved, dict)
        or saved.get('format') != FORMAT
        or type(saved.get('version')) is not int
        or saved['version'] < 0
    ):
        raise SchemaError(f'{marker_path(data)} is not a Campaign Studio schema record.')
    return saved['version']


def check(data, maps):
    found = version(data, maps)
    if found is not None and found > CURRENT:
        raise SchemaError(
            f'This campaign uses data schema {found}, but this Campaign Studio supports up to '
            f'schema {CURRENT}. Update Campaign Studio, or restore the backup made before the '
            'newer version migrated it.'
        )
    return found


# ---------- migrations ----------
# Fields of every stored record at CURRENT (shapes.fields_digest()). When a shape changes, add a version
# whose migration is fill_campaign, so stored records gain the new fields, then update this digest.
SHAPES_DIGEST = 'a4216d823ace7a4fd83f0aef5cbe9470d205c83745d3f28c90954557fde12a47'


def shaped_documents(data, maps):
    """(path, shape) for each stored document built from shapes."""
    yield os.path.join(data, 'codex.json'), shapes.CODEX
    yield os.path.join(data, 'threads.json'), shapes.THREADS
    for kind, shape in (('codex', shapes.CODEX_ENTRY), ('threads', shapes.THREAD)):
        for path in sorted(glob.glob(os.path.join(glob.escape(data), kind, '*.json'))):
            yield path, shape
    yield os.path.join(data, 'art.json'), shapes.ART
    yield os.path.join(data, 'world-maps.json'), shapes.WORLD_MAPS
    for path in sorted(glob.glob(os.path.join(glob.escape(data), 'transcripts', '*.json'))):
        yield path, shapes.TRANSCRIPT
    for path in sorted(glob.glob(os.path.join(glob.escape(data), 'prep', '*.json'))):
        yield path, shapes.PREP
    for path in sorted(glob.glob(os.path.join(glob.escape(maps), '*', 'key.json'))):
        yield path, shapes.MAP_KEY


def fill_campaign(data, maps):
    """Give every stored record each field of its shape. Existing values and unknown fields are kept."""
    for path, shape in shaped_documents(data, maps):

        def normalize(value, path, shape=shape):
            try:
                shape.fill_all(value, path)
            except shapes.ShapeError as error:
                raise SchemaError(str(error)) from error

        yield path, normalize


def hash_foundry_imports(data, maps):
    """Replace each Foundry entry's copy of its imported values with their hash and the imported image."""
    import foundry_library

    def normalize(value, path):
        for entry in value.get('entries', []):
            link = entry.get('foundry')
            if isinstance(link, dict) and isinstance(link.get('imported'), dict):
                values = link.pop('imported')
                link.setdefault('hash', foundry_library.imported_hash(values))
                link.setdefault('image', values.get('image', ''))

    yield os.path.join(data, 'codex.json'), normalize


# From version -> (path, normalize) pairs reaching version + 1. Version 1 completed codex, thread, prep and
# map key records; version 2 completed art, handout, checklist, loot, journal and event records too;
# version 3 added world maps (a campaign without world-maps.json has nothing to fill); version 4 keeps a
# hash of each Foundry-imported entry's values instead of a copy of them; version 5 added the prep archive;
# version 6 added the session log to each prep; version 7 splits the codex and thread collections into
# individual record documents (a special step in planned_changes, not an entry here); version 8 added
# links and touched sessions to each thread; version 9 adds the session pitch, scene plan and handout art brief;
# version 10 added session recording transcripts (a campaign without any has nothing to fill).
MIGRATIONS = {
    0: fill_campaign,
    1: fill_campaign,
    2: fill_campaign,
    3: hash_foundry_imports,
    4: fill_campaign,
    5: fill_campaign,
    7: fill_campaign,
    8: fill_campaign,
    9: fill_campaign,
}


def planned_changes(data, maps, start):
    """[(path, new value)] for each document the migrations from start would change."""
    changes = {}
    for step in range(start, CURRENT):
        if step == 6:
            for kind, field in records.FIELDS.items():
                legacy = os.path.join(data, kind + '.json')
                if legacy in changes:
                    value = changes[legacy]
                elif os.path.isfile(legacy):
                    with open(legacy, encoding='utf-8') as file:
                        value = json.load(file)
                else:
                    continue
                if not isinstance(value, dict) or not isinstance(value.get(field), list):
                    raise SchemaError(f'{legacy} is not a valid {kind} document.')
                changes[legacy] = value  # back up even an empty collection before removing it
                seen = set()
                for item in value[field]:
                    if not isinstance(item, dict):
                        raise SchemaError(f'{legacy} contains an invalid record.')
                    try:
                        path = records.record_path(data, kind, item.get('id'))
                    except ValueError as error:
                        raise SchemaError(f'{legacy}: {error}') from error
                    if path in seen:
                        raise SchemaError(f'{legacy} contains duplicate record IDs.')
                    seen.add(path)
                    if os.path.isfile(path):
                        with open(path, encoding='utf-8') as file:
                            saved = json.load(file)
                        if saved.get('id') != item['id']:
                            raise SchemaError(f'Record storage key collision: {path}')
                    else:
                        saved = None
                    if saved != item:
                        changes[path] = item
            continue
        for path, normalize in MIGRATIONS[step](data, maps):
            if path in changes:
                value = changes[path]
            elif os.path.isfile(path):
                with open(path, encoding='utf-8') as file:
                    try:
                        value = json.load(file)
                    except ValueError as error:
                        raise SchemaError(f'{path} is unreadable: {error}') from error
            else:
                continue
            original = json.dumps(value, sort_keys=True)
            normalize(value, path)
            if path in changes or json.dumps(value, sort_keys=True) != original:
                changes[path] = value
    return list(changes.items())


# ---------- backups ----------
def pending_path(backups):
    return os.path.join(backups, PENDING)


def interrupted_attempt(backups, found):
    """The first backup only if its migration target is still ahead of the saved version."""
    try:
        with open(pending_path(backups), encoding='utf-8') as file:
            record = json.load(file)
        latest = record.get('backup')
        first = record.get('first') or record.get('backup')
    except (FileNotFoundError, ValueError, AttributeError):
        return None
    target = re.match(r'^schema-\d+-to-(\d+)-', latest) if isinstance(latest, str) else None
    if target is None or int(target.group(1)) <= found:
        return None  # the marker was recorded, but cleanup was interrupted
    return first if isinstance(first, str) and first else None


def backup(data, maps, backups, label, first_attempt=None):
    """Copy every document a migration may rewrite, then verify each copy by SHA-256.

    first_attempt names the complete backup of an earlier, interrupted attempt. This copy was taken
    from documents that attempt may have partly migrated, so its manifest says so.
    """
    root = os.path.dirname(os.path.abspath(data))
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-') + os.urandom(3).hex()
    target = os.path.join(backups, label + '-' + stamp)
    try:
        files = []
        for path in campaign_files(data, maps):
            rel = os.path.relpath(path, root).replace(os.sep, '/')
            try:
                copy = os.path.join(target, storage.manifest_path(rel))
                record = storage.copy_and_hash(path, copy)
            except ValueError as error:
                raise SchemaError(
                    f'Could not back up {rel} before migrating ({error}). Replace a linked file '
                    'with an ordinary copy, or rename a file whose name contains ":" or "\\".'
                ) from error
            files.append({'path': rel, 'sha256': record['sha256'], 'size': record['bytes']})
        for entry in files:
            copy = os.path.join(target, storage.manifest_path(entry['path']))
            if storage.sha256_file(copy) != entry['sha256']:
                raise OSError('Backup verification failed for ' + entry['path'])
        manifest = {
            'format': FORMAT + '-backup',
            'created': datetime.datetime.now().isoformat(timespec='seconds'),
            'version': version(data, maps),
            'files': files,
        }
        if first_attempt:
            manifest['partly_migrated'] = True
            manifest['first_attempt_backup'] = first_attempt
        storage.atomic_json(os.path.join(target, 'backup.json'), manifest, durable=True)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)  # an incomplete backup must not look restorable
        raise
    return target


def restore(backup_path, data, maps):
    """Replace documents with a verified backup's copies and its schema version.

    Use this to return to an older Campaign Studio. Stop the server first.
    """
    root = os.path.realpath(os.path.dirname(os.path.abspath(data)))
    with open(os.path.join(backup_path, 'backup.json'), encoding='utf-8') as file:
        manifest = json.load(file)
    if manifest.get('format') != FORMAT + '-backup' or type(manifest.get('version')) is not int:
        raise SchemaError('Choose a Campaign Studio migration backup folder.')
    restores = []
    for entry in manifest['files']:
        try:
            rel = storage.manifest_path(entry['path'])  # rejects drives, backslashes and dots
        except ValueError as error:
            raise SchemaError('Unsafe backup path: ' + str(entry['path'])) from error
        destination = os.path.realpath(os.path.join(root, rel))
        if rel.parts[0] not in ('data', 'maps') or not destination.startswith(root + os.sep):
            raise SchemaError('Unsafe backup path: ' + entry['path'])
        copy = os.path.join(backup_path, rel)
        if storage.sha256_file(copy) != entry['sha256']:
            raise SchemaError('Backup copy changed since it was made: ' + entry['path'])
        restores.append((copy, destination))
    # Replacing record folders also removes entries added after the backup. Older
    # backups have no record folders, so their legacy collections remain authoritative.
    if manifest['version'] <= CURRENT:
        for kind in records.FIELDS:
            folder = os.path.join(data, kind)
            if os.path.lexists(folder):
                if (
                    os.path.islink(folder)
                    or os.path.commonpath((root, os.path.realpath(folder))) != root
                ):
                    raise SchemaError(f'Unsafe record folder: {folder}')
                if not os.path.isdir(folder):
                    raise SchemaError(f'Unsafe record folder: {folder}')
                with os.scandir(folder) as contents:
                    unsafe = any(
                        item.is_symlink()
                        or not item.is_file()
                        or not item.name.endswith(('.json', '.json.lock'))
                        for item in contents
                    )
                if unsafe:
                    raise SchemaError(f'Unsafe record folder: {folder}')
                shutil.rmtree(folder)
    for copy, destination in restores:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with storage.file_lock(destination):
            temporary = destination + '.tmp-' + os.urandom(6).hex()
            try:
                shutil.copy2(copy, temporary)
                storage.atomic_replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)
    write_marker(data, manifest['version'], 'Restored ' + os.path.basename(backup_path))
    return manifest


def write_marker(data, number, note):
    path = marker_path(data)
    try:
        with open(path, encoding='utf-8') as file:
            history = json.load(file).get('history', [])
    except (FileNotFoundError, ValueError, AttributeError):
        history = []
    history.append(
        {
            'version': number,
            'at': datetime.datetime.now().isoformat(timespec='seconds'),
            'note': note,
        }
    )
    storage.atomic_json(
        path, {'format': FORMAT, 'version': number, 'history': history[-50:]}, durable=True
    )


def migrate(data, maps, backups, dry_run=False):
    """Bring the campaign to CURRENT. Back up first whenever an existing campaign changes."""
    found = check(data, maps)
    if found is None:
        # Nothing to stamp yet: unversioned documents found by a later start are migrated then.
        return {'status': 'new', 'version': CURRENT, 'changes': []}
    if found == CURRENT:
        if not dry_run:
            cleanup_pending = os.path.isfile(pending_path(backups))
            if cleanup_pending:
                for kind in records.FIELDS:
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(os.path.join(data, kind + '.json'))
            with contextlib.suppress(FileNotFoundError):
                os.remove(pending_path(backups))
        return {'status': 'current', 'version': CURRENT, 'changes': []}
    changes = planned_changes(data, maps, found)
    root = os.path.dirname(os.path.abspath(data))
    names = [os.path.relpath(path, root).replace(os.sep, '/') for path, _ in changes]
    result = {'status': 'migrated', 'from': found, 'version': CURRENT, 'changes': names}
    if dry_run:
        return dict(result, status='needs migration')
    result['backup'] = None
    if changes:
        first = interrupted_attempt(backups, found)
        result['backup'] = backup(data, maps, backups, f'schema-{found}-to-{CURRENT}', first)
        # Left in place until the version is recorded, so a retry knows its documents may be partly migrated.
        storage.atomic_json(
            pending_path(backups),
            {'backup': os.path.basename(result['backup']), 'first': first},
            durable=True,
        )
    for path, value in changes:
        with storage.file_lock(path):
            # Flushed before the marker below, so a recorded version implies migrated documents.
            storage.atomic_json(path, value, durable=True)
    note = os.path.basename(result['backup']) if result['backup'] else 'no changes'
    write_marker(data, CURRENT, f'Migrated from schema {found}; backup {note}')
    for kind in records.FIELDS:
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(data, kind + '.json'))
    with contextlib.suppress(FileNotFoundError):
        os.remove(pending_path(backups))
    return result
