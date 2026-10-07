"""A recoverable trash for maps: the map's folder and catalogue entry wait here until restored.

Files move under `Campaign.map_trash/<id>/`: `folder/` is the map's own folder and `meta.json`
holds the catalogue entry, the brief and the references that were cleared, so a restore can put them back.
"""

import datetime
import json
import os
import re

import storage

TRASH_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,80}')


def _folder(here, trash_id):
    if not TRASH_ID.fullmatch(trash_id):
        raise ValueError('Invalid trash id.')
    return os.path.join(here.map_trash, trash_id)


def put(here, slug, entry, brief, links):
    """Move the map's folder into the trash and record how to restore it. Returns the trash id."""
    stamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    trash_id = f'{slug}-{stamp}'
    target = _folder(here, trash_id)
    os.makedirs(target)
    source = here.map_folder(slug)
    if os.path.isdir(source):
        storage.atomic_replace(source, os.path.join(target, 'folder'))
    meta = {'id': trash_id, 'slug': slug, 'entry': entry, 'brief': brief, 'links': links}
    storage.atomic_json(os.path.join(target, 'meta.json'), meta | {'trashed': stamp})
    return trash_id


def listing(here):
    base = here.map_trash
    found = []
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        meta_path = os.path.join(base, name, 'meta.json')
        if os.path.isfile(meta_path):
            with open(meta_path, encoding='utf-8') as f:
                meta = json.load(f)
            found.append(
                {k: meta.get(k) for k in ('id', 'slug', 'trashed')}
                | {'name': (meta.get('entry') or {}).get('name', meta.get('slug'))}
            )
    return found


def read(here, trash_id):
    path = os.path.join(_folder(here, trash_id), 'meta.json')
    if not os.path.isfile(path):
        raise KeyError(trash_id)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def take_back(here, trash_id, slug):
    """Move the folder back to where the map lived, then drop the trash record."""
    target = _folder(here, trash_id)
    stored = os.path.join(target, 'folder')
    if os.path.isdir(stored):
        storage.atomic_replace(stored, here.map_folder(slug))


def discard(here, trash_id):
    import shutil

    shutil.rmtree(_folder(here, trash_id), ignore_errors=True)
