"""Build one versioned Foundry import manifest from a saved session prep.

The manifest is copied into Foundry Data, never into the world's database. The GM macro applies it
through Foundry's document API. Write the manifest last; a failed map export leaves the previous manifest,
though earlier map files may have been refreshed. A retry replaces the same stable session ID.
"""

import json
import os
import re
import sys
from pathlib import Path

import campaign
import config
import maps_io
import records
import storage

sys.path.insert(0, os.path.join(campaign.INSTALL, 'forge'))
import forge

SESSION_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
MAX_MAPS = 12
MAX_ENTRIES = 100
MAX_HANDOUTS = 50
MAX_MANIFEST = 1_000_000


def _linked(prep, catalogue, read_doc, here):
    """Collect every map and codex record linked by the prep or its map keys."""
    available = {entry['slug']: entry for entry in catalogue.get('items', [])}
    slugs = set(entry['slug'] for entry in available.values() if entry.get('session') == prep['id'])
    slugs.update(scene.get('map') for scene in prep['scenes'] if scene.get('map'))
    if len(slugs) > MAX_MAPS:
        raise ValueError(f'A session bundle may contain at most {MAX_MAPS} maps.')
    missing_maps = slugs - available.keys()
    if missing_maps:
        raise ValueError('A prep scene links a missing map: ' + ', '.join(sorted(missing_maps)))
    actors = {ident for scene in prep['scenes'] for ident in scene.get('npcs', [])}
    items = set()
    maps = []
    for slug in sorted(slugs):
        if not SESSION_ID.fullmatch(slug):
            raise ValueError('A linked map has an invalid ID.')
        entry = available[slug]
        folder = here.map_folder(slug)
        if not Path(folder, slug + '.foundry.json').is_file():
            raise ValueError(f'Render {entry["name"]} before exporting this session.')
        files = os.path.realpath(here.files)
        image = os.path.realpath(os.path.join(files, entry['image']))
        if os.path.commonpath((files, image)) != files or not os.path.isfile(image):
            raise ValueError(f'The image for {entry["name"]} is missing.')
        key = read_doc('mapkey/' + slug) or {}
        for area in key.get('areas', []):
            actors.update(area.get('npcs', []))
            items.update(area.get('items', []))
        maps.append({'slug': slug, 'name': entry['name']})
    if len(actors | items) > MAX_ENTRIES:
        raise ValueError(f'A session bundle may contain at most {MAX_ENTRIES} linked entries.')
    if actors & items:
        raise ValueError('A codex entry cannot be both an actor and an item in one bundle.')
    return maps, actors, items


def export(session, read_doc, save_doc, busy, here=None):
    """Export linked maps, sheets and handouts as one import choice for the GM macro."""
    here = here or campaign.active()
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        raise ValueError('Invalid session ID.')
    prep = read_doc('prep/' + session)
    if not prep:
        raise ValueError('No such session prep.')
    prep = {**prep, 'id': session}
    if not isinstance(prep.get('title'), str) or not prep['title'].strip():
        raise ValueError('Give the session a title before exporting.')
    if len(prep.get('handouts', [])) > MAX_HANDOUTS:
        raise ValueError(f'A session bundle may contain at most {MAX_HANDOUTS} handouts.')
    handout_ids = [handout.get('id') for handout in prep['handouts']]
    if (
        any(not isinstance(ident, str) for ident in handout_ids)
        or len(set(handout_ids)) != len(handout_ids)
        or any(
            not isinstance(handout.get(field), str) or not handout[field].strip()
            for handout in prep['handouts']
            for field in ('id', 'title', 'player_text')
        )
    ):
        raise ValueError('Each handout needs a distinct ID, title and player text.')
    foundry_data = config.foundry_data(here)
    if not foundry_data or not os.path.isdir(foundry_data):
        raise ValueError('Choose a local Foundry world in Settings before exporting.')
    world = config.world_info(config.settings(here)['world_path'])
    maps, actor_ids, item_ids = _linked(prep, read_doc('maps/index') or {}, read_doc, here)
    if not maps and not actor_ids and not item_ids and not prep['handouts']:
        raise ValueError('Link a map, NPC, item or handout before exporting this session.')
    if any(busy(entry['slug']) for entry in maps):
        raise ValueError('Wait for the session maps to finish rendering before exporting.')
    entries = {ident: records.read(here.data, 'codex', ident) for ident in actor_ids | item_ids}
    missing = {ident for ident, entry in entries.items() if entry is None}
    if missing:
        raise ValueError('The prep links missing codex entries: ' + ', '.join(sorted(missing)))
    if any(entries[ident].get('type') not in ('npc', 'monster', 'pc') for ident in actor_ids):
        raise ValueError('Every linked NPC must be a character or monster codex entry.')
    if any(entries[ident].get('type') != 'item' for ident in item_ids):
        raise ValueError('Every linked item must be an item codex entry.')
    if any(
        not isinstance(entry.get('name'), str) or not entry['name'].strip()
        for entry in entries.values()
    ):
        raise ValueError('Every linked codex entry needs a name.')
    snapshots = forge.entries_for_foundry(actor_ids | item_ids, f'sessions/{session}', here=here)
    manifest = {
        'format': 'campaign-studio-session-bundle',
        'schema': 1,
        'session': session,
        'title': prep['title'],
        'targetWorld': world['id'],
        'maps': maps,
        'actors': [snapshots[ident] for ident in sorted(actor_ids)],
        'items': [snapshots[ident] for ident in sorted(item_ids)],
        'handouts': [
            {
                'id': handout['id'],
                'title': handout['title'],
                'player_text': handout['player_text'],
                'secrets': handout['secrets'],
            }
            for handout in prep['handouts']
        ],
    }
    if len(json.dumps(manifest, ensure_ascii=False).encode('utf-8')) > MAX_MANIFEST:
        raise ValueError('The session bundle is too large; split it into smaller sessions.')
    for entry in maps:
        maps_io.export(entry['slug'], save_doc, here)
    target = Path(foundry_data, forge.FOUNDRY_DIR, 'sessions')
    target.mkdir(parents=True, exist_ok=True)
    storage.atomic_json(target / f'{session}.json', manifest)
    index = target / 'index.json'
    with storage.file_lock(index):
        listing = []
        for path in sorted(target.glob('*.json')):
            if path.name == 'index.json':
                continue
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
                if row.get('format') == manifest['format'] and row.get('schema') == 1:
                    listing.append(
                        {
                            'id': row['session'],
                            'title': row['title'],
                            'targetWorld': row['targetWorld'],
                        }
                    )
            except (OSError, ValueError, KeyError, TypeError):
                continue
        storage.atomic_json(index, listing)
    return {
        'ok': True,
        'world': world['id'],
        'maps': len(maps),
        'actors': len(actor_ids),
        'items': len(item_ids),
        'handouts': len(prep['handouts']),
    }
