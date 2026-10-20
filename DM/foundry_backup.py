"""Offline Foundry User Data backups, folder snapshots and isolated restore rehearsals, and the
comparison of User Data with its backup.

This module copies files only. Foundry performs all world database migrations.
"""

import datetime
import json
import os
import re
import shutil
import stat
import uuid
from collections import defaultdict
from pathlib import Path

import config
import psutil
import storage

FORMAT = 1
MANIFEST = 'campaign-studio-backup.json'
SNAPSHOT_MANIFEST = 'campaign-studio-folder-snapshot.json'
SNAPSHOT_FORMAT = 'campaign-studio-folder-snapshot'
# Top-level User Data folders a comparison with the backup leaves out: Foundry writes and rotates its
# logs on every launch.
IGNORED = ('Logs',)
PACKAGES = {'worlds': 'world', 'systems': 'system', 'modules': 'module'}


def within(path, parent):
    return path == parent or parent in path.parents


def absolute_folder(value, label):
    raw = Path(str(value or '')).expanduser()
    if not raw.is_absolute():
        raise ValueError(f'{label} must be an absolute folder path.')
    return raw.resolve()


def _source():
    selected = config.settings().get('world_path')
    if not selected:
        raise ValueError('Choose a local Foundry world in Settings first.')
    world = config.world_info(selected)
    data = Path(world['data_path']).resolve()
    if data.name.lower() != 'data' or not data.is_dir():
        raise ValueError('The selected world must be inside a Foundry User Data/Data folder.')
    root = data.parent
    relative_world = (
        Path(data.name) / 'worlds' / Path(world['path']).name / 'world.json'
    ).as_posix()
    if not (root / storage.manifest_path(relative_world)).is_file():
        raise ValueError('The selected world manifest is missing from Foundry User Data.')
    return root, world, relative_world


def running_foundry():
    """Return recognizable local Foundry processes; fail closed if enumeration fails."""
    found = []
    try:
        for process in psutil.process_iter(['name', 'cmdline']):
            try:
                name = (process.info['name'] or '').lower()
                args = ' '.join(process.info['cmdline'] or []).lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            desktop = 'foundry virtual tabletop' in name or name in (
                'foundry',
                'foundry.exe',
                'foundryvtt',
                'foundryvtt.exe',
            )
            node = (
                name in ('node', 'node.exe')
                and 'main.js' in args
                and ('foundry' in args or 'resources/app' in args.replace('\\', '/'))
            )
            if desktop or node:
                found.append({'pid': process.pid, 'name': process.info['name']})
    except (psutil.Error, OSError) as error:
        raise ValueError('Cannot check whether Foundry is running.') from error
    return found


def listing(root):
    """Every regular file (relative path, size, mtime) and directory; refuses links and special files.

    Two equal listings of a closed folder mean nothing was added, removed or rewritten in between.
    """
    files, dirs = [], []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            item = Path(current) / name
            if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
                raise ValueError(f'Foundry User Data contains a linked folder: {item}')
            dirs.append(item.relative_to(root).as_posix())
        for name in file_names:
            item = Path(current) / name
            if (
                item.is_symlink()
                or getattr(item, 'is_junction', lambda: False)()
                or not stat.S_ISREG(item.stat(follow_symlinks=False).st_mode)
            ):
                raise ValueError(f'Foundry User Data contains a linked or special file: {item}')
            metadata = item.stat()
            files.append(
                (item.relative_to(root).as_posix(), metadata.st_size, metadata.st_mtime_ns)
            )
    return files, dirs


def plan():
    root, world, _ = _source()
    files, _ = listing(root)
    return {
        'world': world,
        'user_data': str(root),
        'files': len(files),
        'bytes': sum(size for _, size, _ in files),
        'foundry_processes': running_foundry(),
        'suggested_destination': str(Path.home() / 'Campaign Studio Backups'),
    }


def _space(path, bytes_needed):
    if shutil.disk_usage(path).free < int(bytes_needed * 1.05):
        raise ValueError('The destination does not have enough free space for this copy.')


def _copy(source, files, dirs, target):
    """Copy a listing of `source` into `target`; returns each file's record with its SHA-256.

    Fails if a file changed after it was listed.
    """
    for name in dirs:
        (target / storage.manifest_path(name)).mkdir(parents=True, exist_ok=True)
    records = []
    for name, expected_size, expected_mtime in files:
        item = source / storage.manifest_path(name)
        metadata = item.stat()
        if (metadata.st_size, metadata.st_mtime_ns) != (expected_size, expected_mtime):
            raise ValueError(f'A file changed during the copy: {item}')
        records.append(
            {'path': name, **storage.copy_and_hash(item, target / storage.manifest_path(name))}
        )
    return records


def create(destination, confirmed_closed=False):
    root, world, relative_world = _source()
    target_root = absolute_folder(destination, 'Backup destination')
    if within(target_root, root):
        raise ValueError('Choose a backup destination outside Foundry User Data.')
    if not confirmed_closed:
        raise ValueError('Confirm that Foundry VTT is closed before making an offline backup.')
    if running_foundry():
        raise ValueError('Close Foundry VTT before making an offline backup.')
    files, dirs = listing(root)
    target_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _space(target_root, sum(size for _, size, _ in files))
    safe_id = re.sub(r'[^a-z0-9-]', '-', str(world['id']).lower()).strip('-')[:48] or 'world'
    label = f'foundry-{safe_id}-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}'
    staging = target_root / ('.incomplete-' + label)
    final = target_root / label
    staging.mkdir(mode=0o700)
    payload = staging / 'User Data'
    payload.mkdir(mode=0o700)
    try:
        records = _copy(root, files, dirs, payload)
        if running_foundry():
            raise ValueError('Foundry VTT started during the backup. This copy is incomplete.')
        if listing(root) != (files, dirs):
            raise ValueError('Foundry User Data changed during the backup.')
        manifest = {
            'format': FORMAT,
            'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'source_user_data': str(root),
            'world': {
                'id': world['id'],
                'title': world['title'],
                'core_version': world['foundry_version'],
                'system': world['system'],
                'system_version': world['system_version'],
                'manifest_path': relative_world,
            },
            'files': records,
            'directories': dirs,
        }
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        _verify_tree(payload, manifest)
        staging.rename(final)
    except Exception as error:
        raise ValueError(f'{error} Incomplete copy retained at {staging}') from error
    return {
        'path': str(final),
        'world': manifest['world'],
        'files': len(records),
        'bytes': sum(record['bytes'] for record in records),
        'verified': True,
    }


def read_manifest(package):
    if package.name.startswith('.incomplete-'):
        raise ValueError('An incomplete backup cannot be restored.')
    manifest_path = package / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('This folder is not a Campaign Studio Foundry backup.')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT:
        raise ValueError('Unsupported Foundry backup format.')
    return manifest


def _records(records, directories):
    """A manifest's file records by path and its set of folders, refusing an invalid manifest."""
    if not isinstance(records, list) or not isinstance(directories, list):
        raise ValueError('Invalid Foundry backup manifest.')
    names = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Invalid Foundry backup manifest.')
        name = record.get('path')
        storage.manifest_path(name)
        if not isinstance(record.get('bytes'), int) or record['bytes'] < 0:
            raise ValueError('Invalid Foundry backup file size.')
        if not re.fullmatch(r'[0-9a-f]{64}', str(record.get('sha256', ''))):
            raise ValueError('Invalid Foundry backup checksum.')
        names.append(name)
    for name in directories:
        storage.manifest_path(name)
    if len(set(names)) != len(names) or len(set(directories)) != len(directories):
        raise ValueError('Duplicate path in Foundry backup manifest.')
    return dict(zip(names, records)), set(directories)


def _verify_files(payload, records, directories):
    """Check that `payload` holds exactly the recorded files and folders, each with its checksum."""
    names, folders = _records(records, directories)
    actual_files, actual_dirs = listing(payload)
    if set(names) != {name for name, _, _ in actual_files} or folders != set(actual_dirs):
        raise ValueError('Backup file list differs from its manifest.')
    for record in records:
        path = payload / storage.manifest_path(record['path'])
        if path.stat().st_size != record['bytes'] or storage.sha256_file(path) != record['sha256']:
            raise ValueError(f'Backup checksum failed: {record["path"]}')


def _verify_tree(payload, manifest):
    if not payload.is_dir() or payload.is_symlink():
        raise ValueError('Backup User Data folder is missing or linked.')
    records = manifest.get('files')
    _verify_files(payload, records, manifest.get('directories'))
    world = manifest.get('world')
    if not isinstance(world, dict):
        raise ValueError('Invalid Foundry world in backup manifest.')
    world_manifest = payload / storage.manifest_path(world.get('manifest_path'))
    if not world_manifest.is_file():
        raise ValueError('Selected world is missing from the backup.')
    saved_world = json.loads(world_manifest.read_text(encoding='utf-8'))
    if not isinstance(saved_world, dict):
        raise ValueError('Invalid selected world manifest in backup.')
    compatibility = saved_world.get('compatibility') or {}
    if not isinstance(compatibility, dict):
        raise ValueError('Invalid selected world manifest in backup.')
    saved_version = saved_world.get('coreVersion') or compatibility.get('verified')
    if saved_world.get('id') != world.get('id') or saved_version != world.get('core_version'):
        raise ValueError('Selected world version differs from the backup manifest.')
    return {'world': world, 'files': len(records), 'bytes': sum(r['bytes'] for r in records)}


def verify(path):
    package = absolute_folder(path, 'Backup folder')
    manifest = read_manifest(package)
    return {'path': str(package), **_verify_tree(package / 'User Data', manifest), 'verified': True}


def area(name, directory=False):
    """The part of User Data a relative path belongs to.

    `Data/worlds|systems|modules/<id>` for a package's own files, `Data/<folder>` for anything else
    under Data, otherwise the top-level folder or file, such as `Config`.
    """
    parts = name.split('/') + ([''] if directory else [])
    if parts[0] == 'Data' and len(parts) > 2:
        return '/'.join(parts[:3] if parts[1] in PACKAGES and len(parts) > 3 else parts[:2])
    return parts[0]


def _ignored(name):
    return name.split('/', 1)[0] in IGNORED


def compare_source(path):
    """What changed in the original User Data since its offline backup, per world, package or folder.

    Each changed area (see `area`) counts its files and folders added, removed or changed (by size or
    SHA-256) and names a few of them. Its `change` is 'removed' when nothing of it is left, 'added' when
    the backup had none of it, else 'changed'. Foundry's `Logs` are left out. Reads every file.
    """
    package = absolute_folder(path, 'Backup folder')
    manifest = read_manifest(package)
    root = absolute_folder(manifest.get('source_user_data'), 'Original Foundry User Data')
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f'The original Foundry User Data folder is missing or linked: {root}')
    records, folders = _records(manifest.get('files'), manifest.get('directories'))
    saved = {name: record for name, record in records.items() if not _ignored(name)}
    saved_folders = {name for name in folders if not _ignored(name)}
    files, dirs = listing(root)
    live = {name: size for name, size, _ in files if not _ignored(name)}
    live_folders = {name for name in dirs if not _ignored(name)}
    found = defaultdict(lambda: {'added': [], 'removed': [], 'changed': []})
    for name in saved.keys() - live.keys():
        found[area(name)]['removed'].append(name)
    for name in live.keys() - saved.keys():
        found[area(name)]['added'].append(name)
    for name in saved.keys() & live.keys():
        if live[name] != saved[name]['bytes'] or (
            storage.sha256_file(root / storage.manifest_path(name)) != saved[name]['sha256']
        ):
            found[area(name)]['changed'].append(name)
    for name in saved_folders - live_folders:
        found[area(name, True)]['removed'].append(name + '/')
    for name in live_folders - saved_folders:
        found[area(name, True)]['added'].append(name + '/')
    before = {area(name) for name in saved} | {area(name, True) for name in saved_folders}
    after = {area(name) for name in live} | {area(name, True) for name in live_folders}
    changes = []
    for name in sorted(found):
        paths = found[name]
        parts = name.split('/')
        package_kind = PACKAGES.get(parts[1]) if len(parts) == 3 else None
        if name not in after:
            change = 'removed'
        elif name not in before:
            change = 'added'
        else:
            change = 'changed'
        changes.append(
            {
                'area': name,
                'kind': package_kind or 'folder',
                'id': parts[2] if package_kind else None,
                'change': change,
                **{key: len(items) for key, items in paths.items()},
                'examples': sorted(paths['added'] + paths['removed'] + paths['changed'])[:5],
            }
        )
    return {'path': str(root), 'world': manifest.get('world'), 'changes': changes}


def rehearse(path, destination):
    package = absolute_folder(path, 'Backup folder')
    manifest = read_manifest(package)
    summary = _verify_tree(package / 'User Data', manifest)
    target = absolute_folder(destination, 'Restore test destination')
    source_root = absolute_folder(manifest.get('source_user_data'), 'Original Foundry User Data')
    try:
        current_root, _, _ = _source()
    except (ValueError, OSError):
        current_root = None
    if target.exists():
        raise ValueError('Restore test destination must be a new, empty path.')
    if (
        within(target, source_root)
        or (current_root is not None and within(target, current_root))
        or within(target, package)
        or within(package, target)
    ):
        raise ValueError('Restore test destination must be separate from live data and the backup.')
    target.parent.mkdir(parents=True, exist_ok=True)
    _space(target.parent, summary['bytes'])
    staging = target.with_name('.incomplete-' + target.name + '-' + uuid.uuid4().hex[:8])
    staging.mkdir(mode=0o700)
    try:
        for name in manifest['directories']:
            (staging / storage.manifest_path(name)).mkdir(parents=True, exist_ok=True)
        for record in manifest['files']:
            name = storage.manifest_path(record['path'])
            storage.copy_and_hash(package / 'User Data' / name, staging / name)
        _verify_tree(staging, manifest)
        staging.rename(target)
    except Exception as error:
        raise ValueError(f'{error} Incomplete restore test retained at {staging}') from error
    receipt_path = package / f'restore-test-{uuid.uuid4().hex}.json'
    receipt = {
        'format': 'campaign-studio-foundry-restore-test',
        'backup_path': str(package),
        'backup_manifest_sha256': storage.sha256_file(package / MANIFEST),
        'restore_path': str(target),
        'world': summary['world'],
        'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with receipt_path.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2)
    return {
        'path': str(target),
        'world_path': str(target / storage.manifest_path(summary['world']['manifest_path']).parent),
        'world': summary['world'],
        'files': summary['files'],
        'verified': True,
        'receipt_path': str(receipt_path),
    }


def snapshot(source, destination):
    """Copy a closed Foundry folder, such as one world, to a new folder and verify every file.

    The world-level backup taken before Studio repairs a migrated clone; `restore_snapshot` puts parts
    of it back.
    """
    source = absolute_folder(source, 'Folder to back up')
    target = absolute_folder(destination, 'Snapshot destination')
    if not source.is_dir() or source.is_symlink():
        raise ValueError('The folder to back up is missing or linked.')
    if target.exists():
        raise ValueError('Snapshot destination must be a new, empty path.')
    if within(target, source) or within(source, target):
        raise ValueError('The snapshot must be outside the folder it copies.')
    if running_foundry():
        raise ValueError('Close Foundry VTT before backing up the world.')
    files, dirs = listing(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    _space(target.parent, sum(size for _, size, _ in files))
    staging = target.with_name('.incomplete-' + target.name + '-' + uuid.uuid4().hex[:8])
    staging.mkdir(mode=0o700)
    payload = staging / 'folder'
    payload.mkdir(mode=0o700)
    try:
        records = _copy(source, files, dirs, payload)
        if running_foundry() or listing(source) != (files, dirs):
            raise ValueError('Foundry started or the folder changed during the backup.')
        manifest = {
            'format': SNAPSHOT_FORMAT,
            'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'source': str(source),
            'files': records,
            'directories': dirs,
        }
        (staging / SNAPSHOT_MANIFEST).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        _verify_files(payload, records, dirs)
        staging.rename(target)
    except Exception as error:
        raise ValueError(f'{error} Incomplete copy retained at {staging}') from error
    return {
        'path': str(target),
        'source': str(source),
        'files': len(records),
        'bytes': sum(record['bytes'] for record in records),
        'verified': True,
    }


def verify_snapshot(path):
    """Check a folder snapshot against its manifest and return the manifest."""
    package = absolute_folder(path, 'Snapshot')
    manifest_path = package / SNAPSHOT_MANIFEST
    if package.name.startswith('.incomplete-') or not manifest_path.is_file():
        raise ValueError('This folder is not a complete Campaign Studio folder snapshot.')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('format') != SNAPSHOT_FORMAT:
        raise ValueError('Unsupported folder snapshot format.')
    payload = package / 'folder'
    if not payload.is_dir() or payload.is_symlink():
        raise ValueError('The snapshot folder is missing or linked.')
    _verify_files(payload, manifest.get('files'), manifest.get('directories'))
    return manifest


def restore_snapshot(path, names, set_aside):
    """Put the snapshot's copy of each named subfolder back where it came from.

    Each replaced folder is moved into `set_aside` for inspection rather than deleted. The snapshot is
    verified first, and each restored folder is checked against it before it takes the old one's place.
    """
    package = absolute_folder(path, 'Snapshot')
    manifest = verify_snapshot(package)
    source = absolute_folder(manifest.get('source'), 'Snapshot source')
    keep = absolute_folder(set_aside, 'Folder for replaced files')
    if within(keep, source):
        raise ValueError('Replaced files must be kept outside the restored folder.')
    if running_foundry():
        raise ValueError('Close Foundry VTT before restoring the world.')
    restored = []
    for name in names:
        relative = storage.manifest_path(name)
        prefix = relative.as_posix() + '/'
        records = [item for item in manifest['files'] if item['path'].startswith(prefix)]
        directories = [
            item[len(prefix) :] for item in manifest['directories'] if item.startswith(prefix)
        ]
        if relative.as_posix() not in manifest['directories']:
            raise ValueError(f'The snapshot has no folder {name}.')
        current = source / relative
        staging = current.with_name('.restore-' + current.name + '-' + uuid.uuid4().hex[:8])
        staging.mkdir(mode=0o700)
        for item in directories:
            (staging / storage.manifest_path(item)).mkdir(parents=True, exist_ok=True)
        local = [{**item, 'path': item['path'][len(prefix) :]} for item in records]
        for item in local:
            storage.copy_and_hash(
                package / 'folder' / relative / storage.manifest_path(item['path']),
                staging / storage.manifest_path(item['path']),
            )
        _verify_files(staging, local, directories)
        keep.mkdir(parents=True, exist_ok=True)
        if current.exists():
            shutil.move(str(current), str(keep / f'{current.name}-{uuid.uuid4().hex[:8]}'))
        staging.rename(current)
        restored.append(relative.as_posix())
    return restored


def stale_locks(root):
    """Folders named `*.lock` that hold no files, such as `Config/options.json.lock`.

    Foundry takes these locks while it runs; with Foundry closed, one left behind is stale.
    """
    files, dirs = listing(root)
    names = [name for name, _, _ in files]
    locks = [
        name
        for name in dirs
        if name.rsplit('/', 1)[-1].lower().endswith('.lock')
        and not any(item.startswith(name + '/') for item in names)
    ]
    return [name for name in locks if not any(name.startswith(other + '/') for other in locks)]


def copy_folder(source, destination, leave_out=()):
    """Copy the closed folder `source` to the new path `destination` and verify every copied file.

    `leave_out` names relative folders that are not copied. Returns the counts of files and bytes.
    """
    source = absolute_folder(source, 'Folder to copy')
    target = absolute_folder(destination, 'Copy destination')
    if not source.is_dir() or source.is_symlink():
        raise ValueError('The folder to copy is missing or linked.')
    if target.exists():
        raise ValueError('The copy destination must be a new, empty path.')
    if within(target, source) or within(source, target):
        raise ValueError('The copy must be outside the folder it copies.')
    skipped = [storage.manifest_path(name).as_posix() for name in leave_out]

    def copied(name):
        return not any(name == folder or name.startswith(folder + '/') for folder in skipped)

    def selection(found):
        files, dirs = found
        return [item for item in files if copied(item[0])], [name for name in dirs if copied(name)]

    files, dirs = selection(listing(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    _space(target.parent, sum(size for _, size, _ in files))
    target.mkdir()
    records = _copy(source, files, dirs, target)
    if running_foundry() or selection(listing(source)) != (files, dirs):
        raise ValueError('Foundry started or the folder changed during the copy.')
    _verify_files(target, records, dirs)
    return {'files': len(records), 'bytes': sum(record['bytes'] for record in records)}
