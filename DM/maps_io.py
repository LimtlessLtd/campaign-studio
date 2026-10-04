"""Import image maps and copy complete exports to the configured Foundry Data folder."""

import datetime
import json
import math
import os
import shutil
import sys
from PIL import Image
import config
import storage


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'forge'))


def unique_slug(name):
    import generate

    base = generate.slugify(name)[:54]
    slug, n = base, 2
    while os.path.exists(os.path.join(ROOT, 'maps', slug)) or os.path.exists(
        os.path.join(ROOT, 'data', 'mapbrief', slug + '.json')
    ):
        slug, n = base + '-' + str(n), n + 1
    return slug


def import_image(p, save_doc):
    name = str(p.get('name') or '').strip()[:60]
    if not name:
        raise ValueError('Give the map a name.')
    cell = int(p.get('cell', 100))
    if not 50 <= cell <= 300:
        raise ValueError('Pixels per square must be 50–300.')
    rel = str(p.get('image') or '').replace('\\', '/')
    source = os.path.realpath(os.path.join(os.path.dirname(ROOT), rel))
    uploads = os.path.realpath(os.path.join(ROOT, 'uploads'))
    if not source.startswith(uploads + os.sep) or not os.path.isfile(source):
        raise ValueError('Choose an image uploaded into this studio.')
    with Image.open(source) as image:
        width, height = image.size
        if image.format not in ('PNG', 'JPEG', 'WEBP') or width * height > 80000000:
            raise ValueError('Choose a PNG, JPEG or WebP map of at most 80 megapixels.')
        image.verify()
    w, h = math.ceil(width / cell), math.ceil(height / cell)
    if max(w, h) > 320:
        raise ValueError(
            'The imported map may have at most 320 squares per side. Adjust its grid scale.'
        )
    slug = unique_slug(name)
    folder = os.path.join(ROOT, 'maps', slug)
    os.makedirs(folder)
    ext = os.path.splitext(source)[1].lower()
    local = os.path.join(folder, slug + ext)
    shutil.copy2(source, local)
    key = dict(map=name, areas=[], events=[], notes='', stocked=False, session='', images=[])
    scene = dict(
        name=name,
        width=width,
        height=height,
        padding=0,
        background={'src': f'wotg-maps/{slug}{ext}'},
        grid={
            'type': 1,
            'size': cell,
            'distance': 5,
            'units': 'ft',
            'alpha': 0.2,
            'color': '#000000',
        },
        walls=[],
        lights=[],
        tokenVision=True,
        fog={'exploration': True},
        flags={
            'world': {
                'wotgForge': {
                    'plan': slug,
                    'forged': datetime.datetime.now().isoformat(),
                    'key': key,
                    'roofs': [],
                }
            }
        },
    )
    save_doc('mapkey/' + slug, key)
    scene_path = os.path.join(folder, slug + '.foundry.json')
    with open(scene_path, 'w', encoding='utf-8') as f:
        json.dump(scene, f, indent=1)
    entry = dict(
        slug=slug,
        name=name,
        summary='',
        theme='imported',
        imported=True,
        cells=[w, h],
        image=f'DM/maps/{slug}/{slug}{ext}',
        scene=f'DM/maps/{slug}/{slug}.foundry.json',
        check='',
        plan='',
        da='',
        roofs_preview='',
        key=f'DM/maps/{slug}/key.json',
        in_foundry=False,
        session='',
        stocked=False,
        updated=datetime.datetime.now().isoformat(),
        walls=0,
        doors=0,
        windows=0,
        lights=0,
        roofs=0,
        railings=0,
    )
    index_path = os.path.join(ROOT, 'data', 'maps', 'index.json')
    storage.update_json(
        index_path,
        {'items': []},
        lambda index: index['items'].append(entry),
        lambda value: save_doc('maps/index', value),
    )
    brief = dict(
        name=name,
        prompt='',
        type='custom',
        theme='outdoor',
        width=w,
        height=h,
        cell=cell,
        seed=1,
        darkness=0.15,
        tone='Grounded fantasy',
        party_level=5,
        threads=[],
        session='',
        content={'npcs': 5, 'items': 4, 'journals': 4, 'events': 5, 'art': True, 'threads': True},
        auto_content=False,
    )
    save_doc('mapbrief/' + slug, brief)
    if config.foundry_data():
        export(slug, save_doc)
    return {'slug': slug}


def export(slug, save_doc):
    with storage.file_lock(os.path.join(ROOT, 'data', 'maps', 'index.json')):
        import forge

        data_path = config.foundry_data()
        if not data_path or not os.path.isdir(data_path):
            raise ValueError('Choose a local Foundry world in Settings before exporting.')
        index_path = os.path.join(ROOT, 'data', 'maps', 'index.json')
        index = read_json(index_path)
        entry = next((m for m in index['items'] if m['slug'] == slug), None)
        if not entry:
            raise ValueError('Map not found.')
        folder = os.path.join(ROOT, 'maps', slug)
        scene_path = os.path.join(folder, slug + '.foundry.json')
        scene = read_json(scene_path)
        key_path = os.path.join(folder, 'key.json')
        key = read_json(key_path) if os.path.isfile(key_path) else None
        forge.FOUNDRY_DATA = data_path
        tag = scene.setdefault('flags', {}).setdefault('world', {}).setdefault('wotgForge', {})
        tag['key'] = forge.key_for_foundry(slug, key)
        tag['targetWorld'] = (
            config.world_info(config.settings()['world_path'])['id']
            if config.settings().get('world_path')
            else ''
        )
        storage.atomic_json(scene_path, scene)
        target = os.path.join(data_path, 'wotg-maps')
        os.makedirs(target, exist_ok=True)
        image_path = os.path.join(os.path.dirname(ROOT), entry['image'])
        shutil.copy2(image_path, os.path.join(target, slug + os.path.splitext(image_path)[1]))
        shutil.copy2(scene_path, os.path.join(target, slug + '.json'))
        roofs = os.path.join(folder, slug + '.roofs')
        if os.path.isdir(roofs):
            shutil.copytree(roofs, os.path.join(target, slug, 'roofs'), dirs_exist_ok=True)
        forge.write_foundry_index(target)
        entry.update(
            in_foundry=True,
            exported_world=tag['targetWorld'],
            stocked=bool((key or {}).get('stocked')),
            session=(key or {}).get('session', ''),
        )
        save_doc('maps/index', index)
        return {'ok': True, 'world': tag['targetWorld']}
