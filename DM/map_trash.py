"""A recoverable trash for maps: the map's folder and catalogue entry wait here until restored.

Files move under `Campaign.map_trash/<id>/`: `folder/` is the map's own folder and `meta.json`
holds the catalogue entry, the brief and the references that were cleared, so a restore can put them back.
"""

import datetime
import json
import os
import re
import shutil

import storage

TRASH_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,90}')


def _folder(here, trash_id):
    if not TRASH_ID.fullmatch(trash_id):
        raise ValueError('Invalid trash id.')
    return os.path.join(here.map_trash, trash_id)


def put(here, slug, entry, brief, links):
    """Stage a deletion before moving files; reconcile can undo an interrupted move."""
    stamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    trash_id = f'{slug}-{stamp}-{os.urandom(3).hex()}'
    target = _folder(here, trash_id)
    os.makedirs(target)
    meta = {
        'id': trash_id,
        'slug': slug,
        'entry': entry,
        'brief': brief,
        'links': links,
        'trashed': stamp,
        'phase': 'deleting',
    }
    storage.atomic_json(os.path.join(target, 'meta.json'), meta, durable=True)
    source = here.map_folder(slug)
    if os.path.isdir(source):
        storage.atomic_replace(source, os.path.join(target, 'folder'))
    return trash_id


def phase(here, trash_id, value):
    meta = read(here, trash_id)
    meta['phase'] = value
    storage.atomic_json(os.path.join(_folder(here, trash_id), 'meta.json'), meta, durable=True)


def listing(here):
    base = here.map_trash
    found = []
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        meta_path = os.path.join(base, name, 'meta.json')
        if os.path.isfile(meta_path):
            with open(meta_path, encoding='utf-8') as f:
                meta = json.load(f)
            if meta.get('phase', 'trashed') != 'trashed':
                continue
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
    """Move the folder back to where the map lived."""
    target = _folder(here, trash_id)
    stored = os.path.join(target, 'folder')
    if os.path.isdir(stored):
        storage.atomic_replace(stored, here.map_folder(slug))


def restore(here, trash_id, slug):
    """Stage a restore before moving files; reconcile can undo an interrupted move."""
    phase(here, trash_id, 'restoring')
    take_back(here, trash_id, slug)


def reconcile(here):
    """Finish or undo file moves after document-journal recovery."""
    try:
        with open(here.map_index, encoding='utf-8') as file:
            active = {item['slug'] for item in json.load(file).get('items', [])}
    except FileNotFoundError:
        active = set()
    if not os.path.isdir(here.map_trash):
        return
    for trash_id in sorted(os.listdir(here.map_trash)):
        if not TRASH_ID.fullmatch(trash_id):
            continue
        try:
            meta = read(here, trash_id)
        except KeyError:
            continue
        state = meta.get('phase', 'trashed')
        if state not in ('deleting', 'restoring'):
            continue
        slug = meta['slug']
        source = here.map_folder(slug)
        stored = os.path.join(_folder(here, trash_id), 'folder')
        if os.path.exists(source) and os.path.exists(stored):
            raise OSError(f'Both active and trashed map folders exist for {slug}.')
        if state == 'deleting':
            if slug in active:
                take_back(here, trash_id, slug)
                discard(here, trash_id)
            else:
                phase(here, trash_id, 'trashed')
        elif slug in active:
            take_back(here, trash_id, slug)
            discard(here, trash_id)
        else:
            if os.path.isdir(source):
                storage.atomic_replace(source, stored)
            phase(here, trash_id, 'trashed')


def discard(here, trash_id):
    try:
        shutil.rmtree(_folder(here, trash_id))
    except FileNotFoundError:
        pass
