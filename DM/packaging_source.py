"""Build a source-only download using a reviewed, explicit file manifest."""

import hashlib
import json
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent


def source_files(repo=None):
    repo = Path(repo or REPO).resolve()
    names = json.loads((repo / 'source_manifest.json').read_text(encoding='utf-8'))
    if not isinstance(names, list) or len(names) != len(set(names)):
        raise ValueError('Source manifest must be a list of unique paths.')
    for name in names:
        if (
            not isinstance(name, str)
            or '\\' in name
            or name.startswith('/')
            or '..' in name.split('/')
        ):
            raise ValueError('Unsafe source path: ' + str(name))
        full = repo / name
        if not full.is_file() or full.is_symlink() or not full.resolve().is_relative_to(repo):
            raise ValueError('Missing or unsafe source file: ' + name)
    return sorted(names)


def build(repo=None, destination=None):
    repo = Path(repo or REPO).resolve()
    target = Path(destination or (repo / 'DM/exports/campaign-studio-source.zip'))
    names = source_files(repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp-' + os.urandom(6).hex())
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                archive.write(repo / name, name)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(
        digest + '  ' + target.name + '\n', encoding='utf-8'
    )
    return {'path': 'DM/exports/' + target.name, 'sha256': digest, 'files': len(names)}


if __name__ == '__main__':
    print(json.dumps(build()))
