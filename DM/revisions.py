"""Map checkpoints: retain the plan, key, brief and a preview before a layout change."""

import datetime
import json
import os
import re
import shutil
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.join(ROOT, 'maps')


def folder(slug):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', slug):
        raise ValueError('Invalid map id.')
    path = os.path.join(MAPS, slug)
    if not os.path.isdir(path):
        raise ValueError('Map not found.')
    return path


def checkpoint(slug, label='Before edit'):
    source = folder(slug)
    rid = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-') + os.urandom(3).hex()
    target = os.path.join(source, 'revisions', rid)
    os.makedirs(target)
    files = []
    for name in ('plan.txt', 'key.json'):
        if os.path.isfile(os.path.join(source, name)):
            shutil.copy2(os.path.join(source, name), os.path.join(target, name))
            files.append(name)
    brief = os.path.join(ROOT, 'data', 'mapbrief', slug + '.json')
    if os.path.isfile(brief):
        shutil.copy2(brief, os.path.join(target, 'brief.json'))
    preview = ''
    for name in (slug + '.roofs.jpg', slug + '.webp', slug + '.jpg', slug + '.png', slug + '.jpeg'):
        if os.path.isfile(os.path.join(source, name)):
            dest = 'preview' + os.path.splitext(name)[1]
            shutil.copy2(os.path.join(source, name), os.path.join(target, dest))
            preview = f'DM/maps/{slug}/revisions/{rid}/{dest}'
            break
    value = {
        'id': rid,
        'label': str(label)[:100],
        'created': datetime.datetime.now().isoformat(timespec='seconds'),
        'preview': preview,
        'files': files,
    }
    with open(os.path.join(target, 'revision.json'), 'w', encoding='utf-8') as f:
        json.dump(value, f, indent=1)
    return value


def listing(slug):
    path = os.path.join(folder(slug), 'revisions')
    if not os.path.isdir(path):
        return []
    result = []
    for name in sorted(os.listdir(path), reverse=True):
        manifest = os.path.join(path, name, 'revision.json')
        if os.path.isfile(manifest):
            with open(manifest, encoding='utf-8') as f:
                result.append(json.load(f))
    return result


def restore(slug, rid, save_doc):
    if not re.fullmatch(r'[0-9a-f-]+', rid):
        raise ValueError('Invalid revision id.')
    source = os.path.join(folder(slug), 'revisions', rid)
    plan = os.path.join(source, 'plan.txt')
    if not os.path.isfile(plan) and not os.path.isfile(os.path.join(source, 'key.json')):
        raise ValueError('Revision has no saved plan or area key.')
    import sys

    sys.path.insert(0, os.path.join(ROOT, 'forge'))
    import forge

    text = Path(plan).read_text(encoding='utf-8') if os.path.isfile(plan) else None
    if text is not None:
        forge.parse_plan(text)
    checkpoint(slug, 'Before restoring ' + rid)
    if text is not None:
        with open(os.path.join(folder(slug), 'plan.txt'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
    for name, doc in (('key.json', 'mapkey/' + slug), ('brief.json', 'mapbrief/' + slug)):
        path = os.path.join(source, name)
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as f:
                save_doc(doc, json.load(f))
