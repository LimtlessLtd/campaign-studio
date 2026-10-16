"""Read-only readiness checks for one session prep.

Checks use saved records and map artifacts. Each result has one action that takes the GM to the
editor for the first gap; later features can add checks without changing the prep page.
"""

import json
import math
import os
import re
from pathlib import Path
from urllib.parse import quote

import config
import foundry_library
import records
import storage

PREP_ID = re.compile(r's\d{1,6}\Z')
MAP_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
AC = re.compile(r'\bAC\s*\d+', re.I)
HP = re.compile(r'\bHP\s*\d+', re.I)


def _read(path, default):
    try:
        with open(path, encoding='utf-8') as file:
            return json.load(file)
    except FileNotFoundError:
        return default


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except FileNotFoundError:
        return 0


def _artifact(here, value):
    """A map-index path points at a real file inside this campaign's visible file root."""
    try:
        parts = storage.posix_parts(value, 'Invalid map artifact path.')
    except ValueError:
        return None
    try:
        root = Path(here.files).resolve()
        path = root.joinpath(*parts).resolve()
        return path if path.is_relative_to(root) and path.is_file() else None
    except OSError:
        return None


def _map_key_path(here, slug):
    """Only inspect a key in the selected campaign's real maps directory."""
    try:
        root = Path(here.maps).resolve()
        folder = Path(here.map_folder(slug))
        path = folder / 'key.json'
        if folder.is_symlink() or path.is_symlink():
            return None
        path = path.resolve()
        return path if path.is_relative_to(root) else None
    except OSError:
        return None


def _copy_present(foundry_data, slug, image):
    if not foundry_data or not image:
        return False
    folder = Path(foundry_data) / 'wotg-maps'
    return (folder / (slug + '.json')).is_file() and (folder / (slug + image.suffix)).is_file()


def _list(items):
    labels = [str(item or 'Untitled')[:70] for item in items]
    return ', '.join(labels[:3]) + (f' and {len(labels) - 3} more' if len(labels) > 3 else '')


def _action(label, href, section='', tab=''):
    return {'label': label, 'href': href, 'section': section, 'tab': tab}


def _check(identity, title, problems, ready, action, applicable=True):
    return {
        'id': identity,
        'title': title,
        'status': 'not-applicable' if not applicable else 'needs-work' if problems else 'ready',
        'detail': 'Nothing linked for this check.'
        if not applicable
        else _list(problems)
        if problems
        else ready,
        'action': action if applicable and problems else None,
    }


def _has_stats(entry, world_key):
    foundry = entry.get('foundry') if isinstance(entry.get('foundry'), dict) else {}
    if (
        world_key
        and foundry.get('world_key') == world_key
        and str(foundry.get('uuid', '')).startswith(('Actor.', 'Compendium.'))
    ):
        return True
    stats = entry.get('stats') if isinstance(entry.get('stats'), dict) else {}
    if stats.get('ac') is not None and stats.get('hp') is not None:
        return True
    text = str(entry.get('notes') or '') + '\n' + str(entry.get('public') or '')
    return bool(AC.search(text) and HP.search(text))


def report(name, here):
    """Current checks for a saved prep, or None when it does not exist."""
    if not isinstance(name, str) or not PREP_ID.fullmatch(name):
        raise ValueError('Invalid prep ID.')
    path = os.path.join(here.data, 'prep', name + '.json')
    prep = _read(path, None)
    if prep is None:
        return None
    index = _read(here.map_index, {'items': []})
    maps = {
        m['slug']: m
        for m in index.get('items', [])
        if isinstance(m, dict) and MAP_ID.fullmatch(str(m.get('slug', '')))
    }
    codex = {e['id']: e for e in records.all_records(here.data, 'codex')}
    threads = {t['id']: t for t in records.all_records(here.data, 'threads')}
    art = _read(os.path.join(here.data, 'art.json'), {'items': []}).get('items', [])
    scenes = prep.get('scenes') or []
    prep_href = '#/prep/' + quote(name, safe='')
    prep_action = lambda label, section: _action(label, prep_href, section=section)
    checks = []

    incomplete_scenes = [
        s.get('title') or 'Untitled scene'
        for s in scenes
        if not s.get('title') or not (s.get('purpose') or s.get('read_aloud'))
    ]
    checks.append(
        _check(
            'scenes',
            'Scenes outlined',
            incomplete_scenes or (['No scenes planned'] if not scenes else []),
            'Every scene has a title and a purpose or read-aloud text.',
            prep_action('Edit scenes', 'scenes'),
        )
    )

    slugs = list(
        dict.fromkeys(
            [s.get('map') for s in scenes if isinstance(s.get('map'), str) and s.get('map')]
            + [slug for slug, entry in maps.items() if entry.get('session') == name]
        )
    )
    map_action = lambda slug, tab: _action('Open map', '#/maps/' + quote(slug, safe=''), tab=tab)
    missing_maps = [slug for slug in slugs if slug not in maps]
    unrendered = [
        slug
        for slug in slugs
        if slug in maps
        and not all(_artifact(here, maps[slug].get(field)) for field in ('image', 'scene'))
    ]
    checks.append(
        _check(
            'maps',
            'Maps rendered',
            missing_maps + [maps[slug].get('name') or slug for slug in unrendered],
            'All linked maps have an image and Foundry scene.',
            map_action(unrendered[0], 'foundry')
            if unrendered
            else prep_action('Link a map', 'scenes'),
            applicable=bool(slugs),
        )
    )

    key_paths = {slug: _map_key_path(here, slug) for slug in slugs if slug in maps}
    keys = {slug: _read(path, {}) if path else {} for slug, path in key_paths.items()}
    unstocked = [slug for slug in slugs if slug in maps and not keys[slug].get('stocked')]
    checks.append(
        _check(
            'stocked',
            'Maps stocked',
            missing_maps + [maps[slug].get('name') or slug for slug in unstocked],
            'All linked maps have applied content.',
            map_action(unstocked[0], 'workflow')
            if unstocked
            else prep_action('Check scene maps', 'scenes'),
            applicable=bool(slugs),
        )
    )

    prep_modified = _mtime(path)
    settings = config.settings(here)
    try:
        selected_world = (
            config.world_info(settings['world_path']) if settings.get('world_path') else None
        )
        current_world = selected_world['id'] if selected_world else ''
        current_world_key = foundry_library.world_key(selected_world) if selected_world else ''
        foundry_data = config.foundry_data(here)
    except (OSError, ValueError, KeyError):
        current_world = None
        current_world_key = ''
        foundry_data = ''
    stale = []
    for slug in slugs:
        entry = maps.get(slug)
        if not entry:
            continue
        image = _artifact(here, entry.get('image'))
        sources = [str(key_paths[slug])] if key_paths[slug] else []
        sources += [
            str(path) for field in ('image', 'scene') if (path := _artifact(here, entry.get(field)))
        ]
        try:
            exported = float(entry.get('exported_at') or 0)
        except (TypeError, ValueError):
            exported = 0
        if (
            not math.isfinite(exported)
            or not current_world
            or entry.get('exported_world') != current_world
            or not key_paths[slug]
            or not key_paths[slug].is_file()
            or not entry.get('in_foundry')
            or not _copy_present(foundry_data, slug, image)
            or exported < max([prep_modified] + [_mtime(source) for source in sources])
        ):
            stale.append(slug)
    checks.append(
        _check(
            'export',
            'Foundry map copies current',
            missing_maps + [maps[slug].get('name') or slug for slug in stale],
            'Every linked map was exported after the latest prep and map edit.',
            map_action(stale[0], 'foundry') if stale else prep_action('Check scene maps', 'scenes'),
            applicable=bool(slugs),
        )
    )

    actor_ids = {ident for scene in scenes for ident in scene.get('npcs', [])}
    item_ids = set()
    for key in keys.values():
        for area in key.get('areas', []):
            actor_ids.update(area.get('npcs', []))
            item_ids.update(area.get('items', []))
    handouts = prep.get('handouts') or []
    bundle_path = (
        Path(foundry_data) / 'wotg-maps' / 'sessions' / (name + '.json') if foundry_data else None
    )
    try:
        bundle = json.loads(bundle_path.read_text(encoding='utf-8')) if bundle_path else {}
    except (OSError, ValueError):
        bundle = {}
    source_times = [prep_modified]
    for slug in slugs:
        if slug in key_paths and key_paths[slug]:
            source_times.append(_mtime(key_paths[slug]))
        if slug in maps:
            source_times.extend(
                _mtime(path)
                for field in ('image', 'scene')
                if (path := _artifact(here, maps[slug].get(field)))
            )
    for ident in actor_ids | item_ids:
        if ident in codex:
            source_times.append(_mtime(records.record_path(here.data, 'codex', ident)))
    bundle_current = (
        isinstance(bundle, dict)
        and bundle.get('format') == 'campaign-studio-session-bundle'
        and bundle.get('schema') == 1
        and bundle.get('session') == name
        and bool(current_world)
        and bundle.get('targetWorld') == current_world
        and bundle.get('title') == prep.get('title')
        and all(
            isinstance(bundle.get(field), list) for field in ('maps', 'actors', 'items', 'handouts')
        )
        and all(ident in codex for ident in actor_ids | item_ids)
        and {row.get('slug') for row in bundle.get('maps', []) if isinstance(row, dict)}
        == set(slugs)
        and {row.get('id') for row in bundle.get('actors', []) if isinstance(row, dict)}
        == actor_ids
        and {row.get('id') for row in bundle.get('items', []) if isinstance(row, dict)} == item_ids
        and bundle.get('handouts')
        == [
            {field: handout.get(field) for field in ('id', 'title', 'player_text', 'secrets')}
            for handout in handouts
        ]
        and _mtime(bundle_path) >= max(source_times)
    )
    checks.append(
        _check(
            'bundle',
            'Session bundle current',
            [] if bundle_current else ['Export the saved session bundle'],
            'The complete bundle matches the selected world and saved session.',
            prep_action('Go to session export', 'publish'),
            applicable=bool(slugs or actor_ids or item_ids or handouts),
        )
    )

    npc_ids = list(dict.fromkeys(ident for scene in scenes for ident in (scene.get('npcs') or [])))
    no_stats = [
        codex[ident].get('name') or ident if ident in codex else ident
        for ident in npc_ids
        if ident not in codex or not _has_stats(codex[ident], current_world_key)
    ]
    no_portrait = [
        codex[ident].get('name') or ident if ident in codex else ident
        for ident in npc_ids
        if ident not in codex or not codex[ident].get('image')
    ]

    def npc_action(predicate):
        first = next((ident for ident in npc_ids if ident in codex and predicate(codex[ident])), '')
        return (
            _action('Open NPC', '#/codex/' + quote(first, safe=''))
            if first
            else prep_action('Choose an NPC', 'scenes')
        )

    checks.append(
        _check(
            'npc_stats',
            'NPCs playable',
            no_stats,
            'Linked NPCs have Foundry actor data or AC and HP in their notes.',
            npc_action(lambda entry: not _has_stats(entry, current_world_key)),
            applicable=bool(npc_ids),
        )
    )
    checks.append(
        _check(
            'npc_art',
            'NPC portraits ready',
            no_portrait,
            'Linked NPCs have an image.',
            npc_action(lambda entry: not entry.get('image')),
            applicable=bool(npc_ids),
        )
    )

    relevant_art = [
        item
        for item in art
        if item.get('session') == name
        or item.get('codex') in npc_ids
        or (item.get('map') and item.get('map') in slugs)
    ]
    unfinished_art = [
        item.get('title') or item.get('id')
        for item in relevant_art
        if item.get('status') != 'ready' or not item.get('image')
    ]
    checks.append(
        _check(
            'art',
            'Queued art ready',
            unfinished_art,
            'All art queued for this prep is ready.',
            _action('Open art queue', '#/art'),
            applicable=bool(relevant_art),
        )
    )

    unwritten = [
        h.get('title') or 'Untitled handout'
        for h in handouts
        if not h.get('title') or not h.get('player_text')
    ]
    checks.append(
        _check(
            'handouts',
            'Handouts written',
            unwritten,
            'Every planned handout has a title and player text.',
            prep_action('Edit handouts', 'handouts'),
            applicable=bool(handouts),
        )
    )

    thread_ids = prep.get('threads') or []
    missing_threads = [ident for ident in thread_ids if ident not in threads]
    checks.append(
        _check(
            'threads',
            'Story threads chosen',
            missing_threads or (['No threads chosen'] if not thread_ids else []),
            'Every chosen thread exists in the campaign.',
            prep_action('Choose threads', 'threads'),
        )
    )

    encounters = [
        s
        for s in scenes
        if s.get('encounter') or (s.get('encounter_detail') or {}).get('creatures')
    ]
    unplanned = [
        s.get('title') or 'Untitled scene'
        for s in encounters
        if not (s.get('encounter_detail') or {}).get('creatures')
        or not (s.get('encounter_detail') or {}).get('difficulty')
    ]
    checks.append(
        _check(
            'encounters',
            'Encounter plans recorded',
            unplanned,
            'Each encounter has creatures and a target difficulty.',
            prep_action('Edit encounters', 'scenes'),
            applicable=bool(encounters),
        )
    )

    return {
        'prep': name,
        'checks': checks,
        'ready': sum(c['status'] == 'ready' for c in checks),
        'gaps': sum(c['status'] == 'needs-work' for c in checks),
    }
