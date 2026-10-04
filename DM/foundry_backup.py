"""Offline Foundry User Data backups and isolated restore rehearsals.

This module copies files only. Foundry performs all world database migrations.
"""

import datetime
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path

import config
import psutil
import storage

FORMAT = 1
MANIFEST = 'campaign-studio-backup.json'


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


def _inventory(root):
    """Return every regular file and directory; reject links and special files."""
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
    files, _ = _inventory(root)
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


def create(destination, confirmed_closed=False):
    root, world, relative_world = _source()
    target_root = absolute_folder(destination, 'Backup destination')
    if within(target_root, root):
        raise ValueError('Choose a backup destination outside Foundry User Data.')
    if not confirmed_closed:
        raise ValueError('Confirm that Foundry VTT is closed before making an offline backup.')
    if running_foundry():
        raise ValueError('Close Foundry VTT before making an offline backup.')
    files, dirs = _inventory(root)
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
        for name in dirs:
            (payload / storage.manifest_path(name)).mkdir(parents=True, exist_ok=True)
        records = []
        for name, expected_size, expected_mtime in files:
            source = root / storage.manifest_path(name)
            metadata = source.stat()
            if (metadata.st_size, metadata.st_mtime_ns) != (expected_size, expected_mtime):
                raise ValueError(f'A source file changed during backup: {source}')
            record = storage.copy_and_hash(source, payload / storage.manifest_path(name))
            records.append({'path': name, **record})
        if running_foundry():
            raise ValueError('Foundry VTT started during the backup. This copy is incomplete.')
        if _inventory(root) != (files, dirs):
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


def _verify_tree(payload, manifest):
    if not payload.is_dir() or payload.is_symlink():
        raise ValueError('Backup User Data folder is missing or linked.')
    records = manifest.get('files')
    directories = manifest.get('directories')
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
    actual_files, actual_dirs = _inventory(payload)
    if sorted(names) != sorted(name for name, _, _ in actual_files) or sorted(
        directories
    ) != sorted(actual_dirs):
        raise ValueError('Backup file list differs from its manifest.')
    for record in records:
        path = payload / storage.manifest_path(record['path'])
        if path.stat().st_size != record['bytes'] or storage.sha256_file(path) != record['sha256']:
            raise ValueError(f'Backup checksum failed: {record["path"]}')
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
