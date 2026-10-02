"""Fail closed when public source contains unreviewed files or private runtime paths."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import packaging_source

PRIVATE_PARTS = {'data', 'maps', 'uploads', 'exports', '.history', '.env', '.local'}
IGNORED_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.ruff_cache', 'dist'}
PERSONAL_PATH = re.compile(r'[A-Z]:[\\/](?:Users[\\/]|DnD[\\/])', re.IGNORECASE)
TOKEN = re.compile(
    r'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{40,}|sk-ant-[A-Za-z0-9_-]{20,})'
)


def check(repo=ROOT):
    repo = Path(repo).resolve()
    names = set(packaging_source.source_files(repo))
    for name in names:
        parts = Path(name).parts
        if any(part in PRIVATE_PARTS or part.startswith('.env') for part in parts):
            raise ValueError('Private path in source manifest: ' + name)
        if Path(name).suffix.lower() in {'.log', '.zip', '.db', '.sqlite', '.pyc'}:
            raise ValueError('Runtime/binary archive in source manifest: ' + name)
        # Never echo possible secret contents in CI logs.
        try:
            content = (repo / name).read_text(encoding='utf-8')
        except UnicodeDecodeError:
            raise ValueError('Binary source needs a reviewed synthetic asset policy: ' + name)
        if PERSONAL_PATH.search(content) or TOKEN.search(content):
            raise ValueError('Possible personal path or credential in: ' + name)
    if (repo / '.git').exists():
        tracked = (
            subprocess.check_output(
                ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=repo
            )
            .decode('utf-8')
            .split('\0')
        )
        actual = {name for name in tracked if name}
    else:
        actual = set()
        for folder, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and d not in PRIVATE_PARTS]
            for file in files:
                if file.endswith(('.pyc', '.log')) or file.startswith('.env'):
                    continue
                actual.add((Path(folder) / file).relative_to(repo).as_posix())
    extra, missing = actual - names, names - actual
    if extra or missing:
        raise ValueError(
            f'Source manifest mismatch. Unlisted: {sorted(extra)}; missing: {sorted(missing)}'
        )
    print(f'Public source check passed: {len(names)} explicitly reviewed files.')


if __name__ == '__main__':
    try:
        check()
    except (ValueError, OSError) as error:
        sys.exit(str(error))
