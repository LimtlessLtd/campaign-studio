"""Map Forge: turn an ASCII map plan into a battle map image plus a Foundry scene
with walls, doors, windows and lights already placed.

    python DM/forge/forge.py DM/maps/<slug>/plan.txt [--no-foundry-copy]

Writes, next to the plan:
    <slug>.webp              the battle map (no grid; Foundry draws its own); .jpg above 16383 px
    <slug>.foundry.json      Foundry v12 scene data (walls, doors, lights, grid, background)
    <slug>.da.json           the same in Dungeon Alchemist's export format, for the usual Import Data route
    <slug>.check.jpg         the image with walls/doors/windows/lights drawn on, to eyeball alignment
and copies the image and scene into FoundryVTT/Data/wotg-maps/ so the import macro
(forge/foundry-import-macro.js) can create the scene in one click. See forge/README.md.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
from copy import deepcopy
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # the application's modules
import campaign
import config
import foundry_library
import records
import storage

FOUNDRY_DIR = 'wotg-maps'


def _target_world(here):
    """(world, key) of the Foundry world exports go to, or None when none is chosen or it is unreadable."""
    path = config.settings(here).get('world_path')
    if not path:
        return None
    try:
        world = config.world_info(path)
        return world, foundry_library.world_key(world)
    except (OSError, ValueError, KeyError):
        return None


def key_for_foundry(slug, key, copy_art=True, here=None):
    """Snapshot linked codex entries and local art into the private GM journal export."""
    if not key:
        return None
    here = here or campaign.active()
    result = deepcopy(key)
    linked_ids = {
        ident
        for area in result.get('areas', [])
        for field in ('npcs', 'items')
        for ident in area.get(field, [])
    }
    entries = {
        ident: entry
        for ident in linked_ids
        if (entry := records.read(here.data, 'codex', ident)) is not None
    }
    files = os.path.realpath(here.files)
    foundry_data = config.foundry_data(here)
    art_dir = os.path.join(foundry_data, FOUNDRY_DIR, slug, 'art')
    target = _target_world(here)

    def art_path(path):
        if not path or not isinstance(path, str):
            return ''
        source = os.path.realpath(os.path.join(files, path.replace('/', os.sep)))
        if os.path.commonpath((files, source)) != files or not os.path.isfile(source):
            return ''
        if os.path.splitext(source)[1].lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
            return ''
        name = (
            hashlib.sha256(path.encode('utf-8')).hexdigest()[:12] + '-' + os.path.basename(source)
        )
        if copy_art and os.path.isdir(foundry_data):
            os.makedirs(art_dir, exist_ok=True)
            shutil.copy2(source, os.path.join(art_dir, name))
        return f'{FOUNDRY_DIR}/{slug}/art/{name}'

    def linked(entry):
        """The entry's Foundry origin, when the export goes to the world it was imported from."""
        origin = entry.get('foundry')
        if not (target and isinstance(origin, dict)):
            return None
        if origin.get('world_key') != target[1] or not isinstance(origin.get('uuid'), str):
            return None
        return origin

    def entry_image(entry, origin):
        image = entry.get('image', '')
        if origin and isinstance(image, str) and image:
            try:  # already in Foundry's Data folder: pass the path through
                foundry_library.media_path(image, target[0])
                return image
            except (OSError, ValueError):
                return ''
        return art_path(image)

    result['images'] = [p for p in (art_path(path) for path in result.get('images', [])) if p]
    for area in result.get('areas', []):
        area['images'] = [p for p in (art_path(path) for path in area.get('images', [])) if p]
        for field, dest in (('npcs', 'npcs_detail'), ('items', 'items_detail')):
            area[dest] = []
            for entry_id in area.get(field, []):
                entry = entries.get(entry_id)
                if entry:
                    origin = linked(entry)
                    area[dest].append(
                        {
                            k: v
                            for k, v in dict(
                                id=entry_id,
                                type=entry.get('type'),
                                name=entry.get('name', ''),
                                public=entry.get('public', ''),
                                secrets=entry.get('secrets', ''),
                                notes=entry.get('notes', ''),
                                image=entry_image(entry, origin),
                                uuid=origin['uuid'] if origin else '',
                            ).items()
                            if v
                        }
                    )
    return result


# legacy (Dungeon Alchemist / Foundry v9) wall flags: move 0/1, sense 0 none / 1 normal / 2 limited, sound 0/1, door 0/1 door/2 secret
WALLS = {
    'wall': dict(move=1, sense=1, sound=1, door=0),
    'stone wall': dict(
        move=1, sense=1, sound=1, door=0
    ),  # same in Foundry, painted as stone (city walls, temples)
    'door': dict(move=1, sense=1, sound=1, door=1),
    'secret door': dict(move=1, sense=1, sound=1, door=2),
    'window': dict(move=1, sense=0, sound=1, door=0),
    'railing': dict(move=1, sense=0, sound=0, door=0),
    'hedge': dict(move=1, sense=2, sound=0, door=0),
}
FLOORS = {'floor', 'grass', 'dirt', 'water', 'rug', 'sand', 'wood', 'stone', 'cobble', 'void'}
INDOOR = {'floor', 'wood', 'stone', 'rug'}  # floors under a roof, in themes that have roofs
# props sit on the floor; some give light (bright/dim in feet), some block sight like a pillar
PROPS = {
    'table': {},
    'chair': {},
    'barrel': {},
    'crate': {},
    'bed': {},
    'shelf': {},
    'altar': {},
    'chest': {},
    'rock': {},
    'cannon': {},
    'statue': dict(block=0.34),
    'pillar': dict(block=0.3),
    'mast': dict(block=0.26),
    'brazier': dict(light=(10, 25, '#FF9F3A')),
    'lantern': dict(light=(5, 15, '#FFC266')),
    'fireplace': dict(light=(10, 30, '#FF8A2A')),
    'candles': dict(light=(2, 10, '#FFD08A')),
    'crystal': dict(light=(5, 20, '#7FD3FF')),
    'hatch': {},
    'stairs': {},
    # foliage: a ring of limited-sight wall, like Dungeon Alchemist's trees
    'tree': dict(block=0.5, limited=True),
    'bush': dict(block=0.34, limited=True),
    # town furniture; a well is a see-through ring you can't walk through
    'well': dict(block=0.36, see=True),
    'fountain': {},
    'stall': {},
    'anvil': {},
    'cart': {},
    'sacks': {},
    'lamppost': dict(light=(10, 20, '#FFB65C')),
    'bench': {},
    'flowers': {},
    'grave': {},
    'counter': {},
}
LEGEND = {
    '#': 'wall',
    '+': 'door',
    'S': 'secret door',
    'W': 'window',
    '=': 'railing',
    '"': 'hedge',
    '.': 'floor',
    ' ': 'void',
    ',': 'grass',
    ':': 'dirt',
    '~': 'water',
    '_': 'rug',
    ';': 'sand',
    '-': 'wood',
    '^': 'stone',
    'T': 'table',
    'c': 'chair',
    'b': 'barrel',
    'x': 'crate',
    'B': 'bed',
    's': 'shelf',
    'A': 'altar',
    'C': 'chest',
    'o': 'rock',
    'K': 'cannon',
    'M': 'statue',
    'P': 'pillar',
    'm': 'mast',
    '*': 'brazier',
    't': 'lantern',
    'f': 'fireplace',
    'i': 'candles',
    'Y': 'crystal',
    'H': 'hatch',
    '>': 'stairs',
    '&': 'tree',
    '%': 'bush',
    '$': 'stone wall',
    '`': 'cobble',
    'O': 'well',
    'Q': 'fountain',
    'Z': 'stall',
    'n': 'anvil',
    'k': 'cart',
    'q': 'sacks',
    'l': 'lamppost',
    'j': 'bench',
    'y': 'flowers',
    'g': 'grave',
    'u': 'counter',
}
THEMES = ('dungeon', 'cellar', 'temple', 'tavern', 'ship', 'cave', 'outdoor', 'city')
ROOF_THEMES = ('outdoor', 'city')


class PlanError(ValueError):
    pass


def parse_plan(text):
    head, sep, grid = text.partition('\n---\n')
    if not sep:
        raise PlanError('the plan needs a line with just --- between the settings and the map')
    meta = dict(theme='dungeon', cell=150, darkness=0.0, seed=1)
    legend = dict(LEGEND)
    for raw in head.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, _, value = line.partition(':')
        key, value = key.strip().lower(), value.strip()
        if key == 'legend':
            ch, _, name = value.partition('=')
            ch, name = ch.strip(), name.strip().lower()
            if len(ch) != 1 or name not in set(WALLS) | FLOORS | set(PROPS):
                raise PlanError(f'bad legend line "{line}": use one character = a known feature')
            legend[ch] = name
        elif key in ('cell', 'seed'):
            meta[key] = int(value)
            if key == 'cell':  # wall centre lines sit on half cells, so keep it even
                meta[key] = max(50, min(300, meta[key] // 2 * 2))
        elif key == 'darkness':
            meta[key] = float(value)
        else:
            meta[key] = value
    if meta['theme'] not in THEMES:
        raise PlanError(f'unknown theme {meta["theme"]!r}; pick one of {", ".join(THEMES)}')
    rows = grid.rstrip('\n').split('\n')
    while rows and not rows[0].strip():
        rows.pop(0)
    width = max(len(r) for r in rows)
    cells = []
    for i, r in enumerate(rows):
        row = []
        for j, ch in enumerate(r.ljust(width)):
            if ch not in legend:
                raise PlanError(f'unknown map character {ch!r} on map line {i + 1}, column {j + 1}')
            row.append(legend[ch])
        cells.append(row)
    meta.setdefault('name', 'Untitled map')
    return meta, cells


def is_wall(cells, i, j):
    return 0 <= i < len(cells) and 0 <= j < len(cells[0]) and cells[i][j] in WALLS


def is_open(cells, i, j):
    """floor or prop: somewhere a creature can stand"""
    return (
        0 <= i < len(cells)
        and 0 <= j < len(cells[0])
        and cells[i][j] not in WALLS
        and cells[i][j] != 'void'
    )


def wall_nodes(cells):
    """Wall cells that face an open cell (8-neighbourhood). Solid rock behind a wall face stays out,
    so thick cave walls give one line along the rock face instead of a lattice."""
    H, W = len(cells), len(cells[0])
    return {
        (i, j)
        for i in range(H)
        for j in range(W)
        if cells[i][j] in WALLS
        and any(is_open(cells, i + di, j + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1))
    }


def geometry(meta, cells):
    """Wall segments along the centre lines of wall cells, merged into long straight runs.
    Coordinates are in half-cells: the centre of cell (i, j) is (2j+1, 2i+1)."""
    nodes = wall_nodes(cells)
    halves = []  # (kind, orient, line, a, b, owner)
    for i, j in nodes:
        for di, dj in ((0, 1), (1, 0)):
            if (i + di, j + dj) not in nodes:
                continue
            if dj:  # horizontal edge along row i
                y, x0 = 2 * i + 1, 2 * j + 1
                halves.append((cells[i][j], 'h', y, x0, x0 + 1, (i, j)))
                halves.append((cells[i][j + 1], 'h', y, x0 + 1, x0 + 2, (i, j + 1)))
            else:
                x, y0 = 2 * j + 1, 2 * i + 1
                halves.append((cells[i][j], 'v', x, y0, y0 + 1, (i, j)))
                halves.append((cells[i + 1][j], 'v', x, y0 + 1, y0 + 2, (i + 1, j)))
    # merge contiguous halves of the same kind on the same line; each door stays its own 5 ft leaf
    groups = {}
    for kind, orient, line, a, b, owner in halves:
        key = (kind, orient, line, owner if 'door' in kind else None)
        groups.setdefault(key, []).append((a, b))
    segments = []
    for (kind, orient, line, _), spans in groups.items():
        spans.sort()
        start, end = spans[0]
        for a, b in spans[1:] + [(None, None)]:
            if a is not None and a <= end:
                end = max(end, b)
                continue
            segments.append(
                dict(
                    kind=kind,
                    pts=[(start, line), (end, line)]
                    if orient == 'h'
                    else [(line, start), (line, end)],
                )
            )
            if a is not None:
                start, end = a, b
    # a lone wall cell (a column of rock, a pillar drawn with #) becomes a small box
    for i, j in nodes:
        if not any((i + di, j + dj) in nodes for di, dj in ((0, 1), (1, 0), (0, -1), (-1, 0))):
            segments += box(2 * j + 1, 2 * i + 1, 0.7, cells[i][j])
    # sight-blocking props (pillars, masts, statues) get an octagon of walls
    lights = []
    for i, row in enumerate(cells):
        for j, name in enumerate(row):
            p = PROPS.get(name, {})
            if 'block' in p:
                kind = 'hedge' if p.get('limited') else 'railing' if p.get('see') else 'wall'
                segments += ring(
                    2 * j + 1, 2 * i + 1, p['block'] * 2, kind, 6 if p.get('limited') else 8
                )
            if 'light' in p:
                lights.append(
                    dict(
                        x=2 * j + 1,
                        y=2 * i + 1,
                        bright=p['light'][0],
                        dim=p['light'][1],
                        color=p['light'][2],
                        source=name,
                    )
                )
    return segments, lights


def box(cx, cy, size, kind):
    s = size
    pts = [(cx - s, cy - s), (cx + s, cy - s), (cx + s, cy + s), (cx - s, cy + s)]
    return [dict(kind=kind, pts=[pts[k], pts[(k + 1) % 4]]) for k in range(4)]


def ring(cx, cy, r, kind='wall', n=8):
    """Walls round a pillar (8 sides) or a tree (6: foliage is fuzzy and big maps have hundreds)."""
    import math

    pts = [
        (
            cx + r * math.cos(math.pi / n + k * 2 * math.pi / n),
            cy + r * math.sin(math.pi / n + k * 2 * math.pi / n),
        )
        for k in range(n)
    ]
    # prop=True: the painter draws the pillar itself, so these only exist for Foundry
    return [dict(kind=kind, prop=True, pts=[pts[k], pts[(k + 1) % n]]) for k in range(n)]


def lint(cells):
    """Catch the usual slips in hand- or AI-written plans before they reach Foundry."""
    warnings = []
    H, W = len(cells), len(cells[0])
    nodes = wall_nodes(cells)
    wallish = lambda i, j: is_wall(cells, i, j)
    for i in range(H):
        for j in range(W):
            name = cells[i][j]
            where = f'map line {i + 1}, column {j + 1}'
            if (
                name in WALLS
                and (i, j) in nodes
                and not any(wallish(i + a, j + b) for a, b in ((0, 1), (1, 0), (0, -1), (-1, 0)))
            ):
                warnings.append(f'{where}: lone {name} with no wall next to it (misaligned row?)')
            if name in ('door', 'secret door', 'window'):
                if not (
                    (wallish(i, j - 1) and wallish(i, j + 1))
                    or (wallish(i - 1, j) and wallish(i + 1, j))
                ):
                    warnings.append(
                        f'{where}: {name} should sit in a wall, with wall on both sides'
                    )
            if name in PROPS and not is_open(cells, i, j):
                warnings.append(f'{where}: {name} is outside the map')
    return warnings


def buildings(meta, cells):
    """Groups of indoor cells joined by floor or by doors, i.e. one house each (terraced houses share walls
    but not doors). Only for themes with roofs, unless the plan says `roofs: no`."""
    if meta['theme'] not in ROOF_THEMES or str(meta.get('roofs', 'yes')).lower() in (
        'no',
        'off',
        'false',
    ):
        return []
    H, W = len(cells), len(cells[0])
    # indoors = indoor floor, plus any furniture reachable from it without crossing a wall
    inside = [[cells[i][j] in INDOOR for j in range(W)] for i in range(H)]
    stack = [(i, j) for i in range(H) for j in range(W) if inside[i][j]]
    while stack:
        i, j = stack.pop()
        for a, b in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            p, q = i + a, j + b
            if 0 <= p < H and 0 <= q < W and not inside[p][q] and cells[p][q] in PROPS:
                inside[p][q] = True
                stack.append((p, q))
    # an inner door (indoors on both sides) joins rooms of the same house
    for i in range(H):
        for j in range(W):
            if cells[i][j] in ('door', 'secret door'):
                if (0 < j < W - 1 and inside[i][j - 1] and inside[i][j + 1]) or (
                    0 < i < H - 1 and inside[i - 1][j] and inside[i + 1][j]
                ):
                    inside[i][j] = True
    seen, out = set(), []
    for i in range(H):
        for j in range(W):
            if not inside[i][j] or (i, j) in seen:
                continue
            stack, comp = [(i, j)], []
            seen.add((i, j))
            while stack:
                a, b = stack.pop()
                comp.append((a, b))
                for da, db in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                    q = (a + da, b + db)
                    if 0 <= q[0] < H and 0 <= q[1] < W and q not in seen and inside[q[0]][q[1]]:
                        seen.add(q)
                        stack.append(q)
            if len(comp) >= 2:
                out.append(sorted(comp))
    return out


def to_px(meta, pt):
    half = meta['cell'] / 2
    return [round(pt[0] * half), round(pt[1] * half)]


def v12_wall(flags):
    level = {0: 0, 1: 20, 2: 10}
    return dict(
        move=20 if flags['move'] else 0,
        sight=level[flags['sense']],
        light=level[flags['sense']],
        sound=20 if flags['sound'] else 0,
        door=flags['door'],
        ds=0,
        dir=0,
    )


def scene_files(meta, cells, segments, lights, image_src):
    c = meta['cell']
    H, W = len(cells), len(cells[0])
    walls = [
        dict(c=to_px(meta, s['pts'][0]) + to_px(meta, s['pts'][1]), **WALLS[s['kind']])
        for s in segments
    ]
    ft_light = [
        dict(l, x=to_px(meta, (l['x'], l['y']))[0], y=to_px(meta, (l['x'], l['y']))[1])
        for l in lights
    ]
    da = dict(
        name=meta['name'],
        width=W * c,
        height=H * c,
        grid=c,
        shiftX=0,
        shiftY=0,
        gridDistance=5.0,
        gridUnits='ft',
        padding=0.0,
        gridColor='#000000',
        gridAlpha=0.2,
        globalLight=meta['darkness'] < 0.5,
        darkness=meta['darkness'],
        lights=[
            dict(
                x=l['x'],
                y=l['y'],
                dim=float(l['dim']),
                bright=float(l['bright']),
                tintColor=l['color'],
                tintAlpha=0.3,
            )
            for l in ft_light
        ],
        walls=walls,
        img=image_src,
        foreground=None,
    )
    v12 = dict(
        name=meta['name'],
        width=W * c,
        height=H * c,
        padding=0,
        background=dict(src=image_src),
        grid=dict(type=1, size=c, distance=5, units='ft', color='#000000', alpha=0.2),
        environment=dict(
            darknessLevel=meta['darkness'], globalLight=dict(enabled=meta['darkness'] < 0.5)
        ),
        tokenVision=True,
        fog=dict(exploration=True),
        walls=[dict(c=w['c'], **v12_wall(w)) for w in walls],
        lights=[
            dict(
                x=l['x'],
                y=l['y'],
                walls=True,
                vision=False,
                config=dict(
                    bright=l['bright'],
                    dim=l['dim'],
                    color=l['color'],
                    alpha=0.3,
                    angle=360,
                    animation=dict(
                        type='torch'
                        if l['source'] in ('brazier', 'lantern', 'fireplace', 'candles')
                        else None,
                        speed=3,
                        intensity=3,
                    ),
                ),
            )
            for l in ft_light
        ],
        flags={
            'world': {
                'wotgForge': dict(
                    plan=meta.get('slug'),
                    forged=datetime.datetime.now().isoformat(timespec='seconds'),
                )
            }
        },
    )
    return v12, da


def write_foundry_index(target):
    """List exported scenes for the import macro's picker; an unreadable scene keeps its slug."""
    path = os.path.join(target, 'index.json')
    with storage.file_lock(path):  # the server's export and forge processes both rebuild it
        listing = sorted(
            f[:-5] for f in os.listdir(target) if f.endswith('.json') and f != 'index.json'
        )
        names = {}
        for slug in listing:
            try:
                scene = json.loads(Path(target, slug + '.json').read_text(encoding='utf-8'))
                names[slug] = scene['name']
            except (OSError, ValueError, KeyError, TypeError):
                names[slug] = slug
        storage.atomic_json(path, [dict(slug=slug, name=names[slug]) for slug in listing])


def update_index(entry, here=None):
    path = (here or campaign.active()).map_index

    def upsert(index):
        index['items'] = [e for e in index['items'] if e['slug'] != entry['slug']] + [entry]
        index['items'].sort(key=lambda e: e['name'].lower())

    storage.update_json(path, {'items': []}, upsert)


def forge(plan_path, foundry_copy=True, jobs=None, here=None):
    here = here or campaign.active()
    plan_path = os.path.abspath(plan_path)
    folder = os.path.dirname(plan_path)
    meta, cells = parse_plan(Path(plan_path).read_text(encoding='utf-8'))
    slug = meta['slug'] = meta.get('slug') or os.path.basename(folder)
    if not re.fullmatch(r'[a-z0-9-]+', slug):
        raise PlanError('map folder names must be lowercase letters, digits and dashes')
    for w in lint(cells):
        print(f'  warning: {w}')
    print('PROGRESS 5% preparing map', flush=True)
    segments, lights = geometry(meta, cells)
    out = lambda ext: os.path.join(folder, slug + ext)
    from render2d import render

    houses = buildings(meta, cells)
    image, roofs = render(meta, cells, segments, lights, out(''), out('.check.jpg'), jobs, houses)
    print('PROGRESS 95% exporting map', flush=True)
    if os.path.exists(out('.check.png')):
        os.remove(out('.check.png'))  # older forges wrote PNG
    ext = os.path.splitext(image)[1]
    src = f'{FOUNDRY_DIR}/{slug}{ext}'
    v12, da = scene_files(meta, cells, segments, lights, src)
    # roofs become overhead tiles (the import macro creates them); the DA format has no tiles
    v12['flags']['world']['wotgForge']['roofs'] = [
        dict(
            src=f'{FOUNDRY_DIR}/{slug}/roofs/{r["file"]}',
            x=r['x'],
            y=r['y'],
            width=r['width'],
            height=r['height'],
        )
        for r in roofs
    ]
    key = os.path.join(folder, 'key.json')
    key_data = json.loads(Path(key).read_text(encoding='utf-8')) if os.path.exists(key) else None
    if key_data:  # the DM key: numbered areas, loot and events, for the journal and map pins
        v12['flags']['world']['wotgForge']['key'] = key_for_foundry(
            slug, key_data, foundry_copy, here
        )
    storage.atomic_json(out('.foundry.json'), v12)
    storage.atomic_json(out('.da.json'), da)
    copied = False
    foundry_data = config.foundry_data(here)
    if foundry_copy and os.path.isdir(foundry_data):
        target = os.path.join(foundry_data, FOUNDRY_DIR)
        os.makedirs(target, exist_ok=True)
        for old in (
            '.webp',
            '.jpg',
        ):  # the format depends on size; drop a stale copy in the other one
            if old != ext and os.path.exists(os.path.join(target, slug + old)):
                os.remove(os.path.join(target, slug + old))
        shutil.copy2(image, os.path.join(target, slug + ext))
        shutil.copy2(out('.foundry.json'), os.path.join(target, slug + '.json'))
        roof_dir = os.path.join(target, slug, 'roofs')
        if os.path.isdir(roof_dir):
            shutil.rmtree(roof_dir)
        if roofs:
            shutil.copytree(out('.roofs'), roof_dir)
        write_foundry_index(target)
        copied = True
    rel = lambda p: os.path.relpath(p, here.files).replace('\\', '/')
    counts = dict(
        walls=sum(1 for s in segments if s['kind'] in ('wall', 'hedge')),
        doors=sum(1 for s in segments if 'door' in s['kind']),
        windows=sum(1 for s in segments if s['kind'] == 'window'),
        railings=sum(1 for s in segments if s['kind'] == 'railing'),
        lights=len(lights),
        roofs=len(roofs),
    )
    update_index(
        dict(
            slug=slug,
            name=meta['name'],
            summary=meta.get('summary', ''),
            theme=meta['theme'],
            cells=[len(cells[0]), len(cells)],
            image=rel(image),
            check=rel(out('.check.jpg')),
            scene=rel(out('.foundry.json')),
            da=rel(out('.da.json')),
            plan=rel(plan_path),
            in_foundry=copied,
            roofs_preview=rel(out('.roofs.jpg')) if roofs else '',
            key=rel(key) if key_data is not None else '',
            session=(key_data or {}).get('session', ''),
            stocked=bool((key_data or {}).get('stocked')),
            updated=datetime.datetime.now().isoformat(timespec='seconds'),
            **counts,
        ),
        here,
    )
    print(
        f'{meta["name"]}: {len(cells[0])}x{len(cells)} cells, '
        + ', '.join(f'{v} {k}' for k, v in counts.items())
        + (f'  ->  Foundry Data/{FOUNDRY_DIR}/{slug}' if copied else '')
    )
    print('PROGRESS 100% complete', flush=True)


def key_only(plan_path, here=None):
    """Put an edited DM key into the already-forged scene (and Foundry's copy) without repainting."""
    folder = os.path.dirname(os.path.abspath(plan_path))
    slug = os.path.basename(folder)
    scene_path = os.path.join(folder, slug + '.foundry.json')
    key_path = os.path.join(folder, 'key.json')
    if not os.path.exists(scene_path):
        raise PlanError('forge the map once before updating its key')
    scene = json.loads(Path(scene_path).read_text(encoding='utf-8'))
    key = (
        json.loads(Path(key_path).read_text(encoding='utf-8')) if os.path.exists(key_path) else None
    )
    here = here or campaign.active()
    scene['flags']['world']['wotgForge']['key'] = key_for_foundry(slug, key, here=here)
    storage.atomic_json(scene_path, scene)
    foundry_data = config.foundry_data(here)
    target = os.path.join(foundry_data, FOUNDRY_DIR)
    if foundry_data and os.path.isdir(target):
        shutil.copy2(scene_path, os.path.join(target, slug + '.json'))
    index = here.map_index
    if os.path.exists(index) and key:

        def annotate(data):
            for entry in data['items']:
                if entry['slug'] == slug:
                    entry['session'] = key.get('session', '')
                    entry['stocked'] = bool(key.get('stocked'))

        storage.update_json(index, {'items': []}, annotate)
    print(f'{slug}: key updated ({len((key or {}).get("areas", []))} areas)')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument('plans', nargs='+')
    ap.add_argument(
        '--no-foundry-copy', action='store_true', help='do not copy into FoundryVTT/Data/wotg-maps'
    )
    ap.add_argument(
        '--jobs', type=int, help='parallel painting processes (default: most CPU cores)'
    )
    ap.add_argument(
        '--key-only', action='store_true', help='only update the DM key in the forged scene'
    )
    args = ap.parse_args()
    sys.path.insert(0, HERE)
    for p in args.plans:
        try:
            key_only(p) if args.key_only else forge(p, not args.no_foundry_copy, args.jobs)
        except PlanError as e:
            sys.exit(f'{p}: {e}')


if __name__ == '__main__':
    main()
