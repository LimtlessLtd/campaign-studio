"""Write reviewed document changes into a closed Foundry world's LevelDB folders.

Studio reads world databases itself (`foundry_leveldb`) but writes them only through the classic-level
library bundled with the Foundry installation that opens the world, run by Node.js or by that
installation's own Electron runtime (`foundry_leveldb_write.cjs`). A Python writer would be a second
LevelDB implementation that only Studio's own reader had checked; Foundry's library writes exactly what
Foundry reads, and its LOCK refuses a database Foundry has open. Callers back the world up first.
"""

import json
import mmap
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import foundry_backup

HELPER = Path(__file__).with_name('foundry_leveldb_write.cjs')
# Folders that hold Foundry's package.json: a Node.js build (the folder itself or its `app`), and the
# Windows/Linux and macOS desktop builds.
APP_FOLDERS = ('.', 'app', 'resources/app', 'Contents/Resources/app')
EXECUTABLES = (
    'Foundry Virtual Tabletop.exe',
    'foundryvtt',
    'Contents/MacOS/Foundry Virtual Tabletop',
)
# Electron's fuse wire follows this sentinel: a version byte, a length byte, then one '0'/'1' per fuse,
# RunAsNode first. With that fuse off the executable would start Foundry instead of running a script.
FUSE_SENTINEL = b'dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX'
TIMEOUT = 900


def installation(folder, target_build):
    """The Foundry installation's app folder and bundled classic-level, refusing another build's."""
    root = foundry_backup.absolute_folder(folder, 'Foundry installation folder')
    for name in APP_FOLDERS:
        app = (root / name).resolve()
        try:
            data = json.loads((app / 'package.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get('name') == 'foundryvtt':
            break
    else:
        raise ValueError(
            'Choose the folder Foundry VTT is installed in: it holds resources/app (desktop) or '
            'main.js (Node.js build).'
        )
    release = data.get('release') if isinstance(data.get('release'), dict) else {}
    build = f'{release.get("generation")}.{release.get("build")}'
    if build != target_build:
        raise ValueError(
            f'That installation is Foundry {build}, but the clone was migrated with Foundry '
            f'{target_build}. Choose the installation that opens the clone.'
        )
    library = app / 'node_modules' / 'classic-level'
    if not (library / 'package.json').is_file():
        raise ValueError('That Foundry installation has no bundled classic-level library.')
    return {'root': root, 'app': app, 'build': build, 'library': library}


def _electron(root):
    """The installation's executable, when its RunAsNode fuse lets it run a script as Node.js."""
    for name in EXECUTABLES:
        executable = root / name
        if not executable.is_file():
            continue
        try:
            with (
                executable.open('rb') as stream,
                mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data,
            ):
                at = data.find(FUSE_SENTINEL)
                end = at + len(FUSE_SENTINEL)
                wire = data[end : end + 3] if at >= 0 else b''
        except (OSError, ValueError):  # unreadable or empty
            return None
        return executable if len(wire) == 3 and wire[1] >= 1 and wire[2:3] == b'1' else None
    return None


def _run(command, environment, arguments, timeout):
    completed = subprocess.run(
        [*command, str(HELPER), *arguments],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        env={**os.environ, **environment},
        check=False,
    )
    lines = completed.stdout.strip().splitlines()
    try:
        result = json.loads(lines[-1])
    except (IndexError, ValueError):
        result = None
    if not isinstance(result, dict):
        raise ValueError(
            'The LevelDB writer returned no result. ' + (completed.stderr or '').strip()[-400:]
        )
    return result


def runtime(install):
    """The first JavaScript runtime here that loads the installation's classic-level."""
    candidates = []
    node = shutil.which('node')
    if node:
        candidates.append(([node], {}))
    executable = _electron(install['root'])
    if executable:
        candidates.append(([str(executable)], {'ELECTRON_RUN_AS_NODE': '1'}))
    errors = []
    for command, environment in candidates:
        try:
            result = _run(command, environment, [str(install['library']), '--probe'], 120)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            errors.append(str(error))
            continue
        if result.get('ok') is True:
            return {
                'command': command,
                'environment': environment,
                'classic_level': result.get('classic_level'),
                'node': result.get('node'),
            }
        errors.append(str(result.get('error')))
    raise ValueError(
        "No JavaScript runtime here can load this Foundry installation's LevelDB library. Install "
        'Node.js 20 or later, or choose a desktop Foundry installation. ' + '; '.join(errors)[:400]
    )


def write(collections, install, found=None):
    """Write [(database folder, ops)] through the helper; returns {'ok', 'committed', 'error'}.

    Each op is {'type': 'put'|'del', 'key', 'expected_sha256', 'value'}; the helper refuses every
    change if any key no longer holds the bytes its expected hash names. `found` is a `runtime()`.
    """
    found = found or runtime(install)
    job = {'collections': [{'folder': str(folder), 'ops': ops} for folder, ops in collections]}
    with tempfile.TemporaryDirectory(prefix='studio-leveldb-') as folder:
        path = Path(folder) / 'job.json'
        path.write_text(json.dumps(job, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        result = _run(
            found['command'], found['environment'], [str(install['library']), str(path)], TIMEOUT
        )
    committed = result.get('committed')
    return {
        'ok': result.get('ok') is True,
        'committed': [str(item) for item in committed] if isinstance(committed, list) else [],
        'error': str(result.get('error') or ''),
        'runtime': {'classic_level': found['classic_level'], 'node': found['node']},
    }
