"""Find and plan repairs for known defects in a world Foundry has migrated to v14 with dnd5e 5.x.

`load` reads a world's documents, `plan` returns findings and the document changes that fix them, and
`verify` checks a repaired world against its backup. Nothing here writes: `foundry_readiness` applies a
reviewed plan through `foundry_leveldb_write`.

The defects come from real v12 to v14 upgrades:

- Scene Levels: the Levels module's v14 migration adds auto-named "<Scene> - Level (a|b)" floors at the
  base of a floor the GM had defined (`flags.levels.sceneLevels`), or several at one base where none was
  defined. They merge into the floor kept at that base and every reference moves with them.
- A relative ".<id>" `flags.dnd5e.sourceId` with no `_stats.compendiumSource` crashes dnd5e 5.3 item
  preparation, so the actor's AC, hit points and save DCs never compute.
- "[object Object]" in old-format damage, recharge and consumption fields leaves the item invalid or
  without its attack once dnd5e converts it.
- A summon profile whose uuid is not a Foundry UUID (a Plutonium `@creature[...]` link) summons nothing;
  the GM may point it at a world actor matched by name.
- A script macro that creates an ActiveEffect with `label`/`icon` fails in v14, which removed them for
  `name`/`img`; the GM may have them renamed.
"""

import copy
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import foundry_leveldb

COLLECTIONS = ('actors', 'items', 'scenes', 'macros')
ITEM_KINDS = ('items', 'actors.items', 'scenes.tokens.delta.items')
BAD = '[object'
UUID = re.compile(
    r'^(?:Compendium\.[\w-]+\.[\w-]+\.(?:[A-Za-z]+\.)?[A-Za-z0-9]{16}'
    r'|[A-Z][A-Za-z]*\.[A-Za-z0-9]{16}(?:\.[A-Z][A-Za-z]*\.[A-Za-z0-9]{16})*)$'
)
# Keys only an ActiveEffect's data has, so a dialog button's `label`/`icon` is never touched.
EFFECT_KEYS = frozenset(('changes', 'duration', 'statuses', 'origin', 'transfer', 'disabled'))
RENAMED_EFFECT_KEYS = {'label': 'name', 'icon': 'img'}
LEVEL_IMAGES = ('background', 'foreground')
PENDING = {
    'floors': 'scenes with floors to merge or rename',
    'source_ids': 'relative source IDs',
    'formulas': '"[object Object]" fields',
}


def load(world, names=COLLECTIONS):
    """{collection: {key: (stored bytes, document)}} for each named database of a world folder."""
    result = {}
    for name in names:
        folder = Path(world) / 'data' / name
        rows = {}
        if (folder / 'CURRENT').is_file():
            for key, value in foundry_leveldb.read(folder).items():
                try:
                    document = json.loads(value)
                except ValueError as error:
                    raise ValueError(f'A {name} document cannot be read: {key!r}') from error
                if isinstance(document, dict):
                    rows[key.decode('utf-8')] = (value, document)
        result[name] = rows
    return result


def kind(key):
    """A document key's collection path, such as 'actors.items' for '!actors.items!<a>.<b>'."""
    return key.split('!')[1] if key.count('!') >= 2 else ''


def fingerprint(documents):
    """One hash of every document read, so a later step can tell whether the world changed."""
    digest = hashlib.sha256()
    for collection in sorted(documents):
        for key in sorted(documents[collection]):
            digest.update(f'{collection}\0{key}\0'.encode())
            digest.update(hashlib.sha256(documents[collection][key][0]).digest())
    return digest.hexdigest()


def _number(value):
    try:
        number = float(value if value is not None else 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _text(number):
    return str(int(number)) if float(number).is_integer() else str(number)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _bad(value):
    return isinstance(value, str) and BAD in value


# ---- Scene Levels


def _bottom(level):
    return _number(_dict(level.get('elevation')).get('bottom'))


def _top(level):
    return _number(_dict(level.get('elevation')).get('top'))


def _src(level, field):
    return _dict(level.get(field)).get('src') or None


def _intended(scene):
    """The floors the GM defined in the Levels module: (bottom, top, name)."""
    result = []
    for entry in _dict(_dict(scene.get('flags')).get('levels')).get('sceneLevels') or []:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        try:
            bottom, top = float(entry[0]), float(entry[1])
        except (TypeError, ValueError):
            continue
        name = entry[2] if len(entry) > 2 and entry[2] is not None else ''
        result.append((bottom, top, str(name)))
    return result


def _rewrite(value, replace, level_ids):
    """Point every reference to a removed floor at its kept floor. Returns (value, touched)."""
    if isinstance(value, list):
        out, touched = [], False
        for item in value:
            if isinstance(item, str) and item in replace:
                out.append(replace[item])
                touched = True
            else:
                item, changed = _rewrite(item, replace, level_ids)
                touched = touched or changed
                out.append(item)
        if not touched:
            return value, False
        seen, unique = set(), []
        for item in out:  # a floor listed twice now is listed once; nothing else changes
            if isinstance(item, str) and item in level_ids:
                if item in seen:
                    continue
                seen.add(item)
            unique.append(item)
        return unique, True
    if isinstance(value, dict):
        touched = False
        for key in list(value):
            if key == '_id':
                continue
            item, changed = _rewrite(value[key], replace, level_ids)
            if changed:
                value[key] = item
                touched = True
        return value, touched
    if isinstance(value, str) and value in replace:
        return replace[value], True
    return value, False


def _scene_floors(scene_key, scene, keys, scenes):
    """Plan one scene's floor merges: (finding or None, {key: new document or None})."""
    scene_id = scene.get('_id')

    def level_key(level_id):
        return f'!scenes.levels!{scene_id}.{level_id}'

    ids = [item for item in scene.get('levels') or [] if isinstance(item, str)]
    levels = [
        copy.deepcopy(scenes[level_key(item)][1])
        for item in ids
        if level_key(item) in scenes and scenes[level_key(item)][1].get('_id') == item
    ]
    if len(levels) < 2:
        return None, {}
    intended = _intended(scene)
    prefix = f'{scene.get("name")} - Level ('

    def auto(level):
        return str(level.get('name') or '').startswith(prefix)

    def is_intended(level):
        return not auto(level) or any(
            bottom == _bottom(level) and abs(top - _top(level)) <= 1 for bottom, top, _ in intended
        )

    tokens = Counter(
        scenes[key][1].get('level')
        for key in keys
        if key.startswith('!scenes.tokens!') and scenes[key][1].get('level')
    )
    groups = defaultdict(list)
    for level in levels:
        groups[_bottom(level)].append(level)
    replace = {}  # removed floor ID -> kept floor ID
    removed = []  # (removed floor, kept floor)
    edited = {}  # kept floor ID -> its changed document
    kept_back = []
    changes, carried = [], []

    def merge(rem, keep):
        """Merge `rem` into `keep` unless that would lose one of `rem`'s images."""
        for field in LEVEL_IMAGES:
            if _src(rem, field) and _src(keep, field) and _src(rem, field) != _src(keep, field):
                kept_back.append((rem, field, keep))
                return False
        for field in LEVEL_IMAGES:
            if _src(rem, field) and not _src(keep, field):
                keep[field] = copy.deepcopy(rem[field])
                edited[keep['_id']] = keep
                carried.append((rem, field, keep))
        replace[rem['_id']] = keep['_id']
        removed.append((rem, keep))
        return True

    for base in sorted(groups):
        group = groups[base]
        keepers = [level for level in group if is_intended(level)]
        autos = [level for level in group if not is_intended(level)]
        if not autos:
            continue
        named = next((name for bottom, _, name in intended if bottom == base and name), None)
        if not keepers:
            if len(autos) < 2:
                if named and autos[0].get('name') != named:
                    changes.append(f'rename "{autos[0].get("name")}" -> "{named}"')
                    autos[0]['name'] = named
                    edited[autos[0]['_id']] = autos[0]
                continue
            primary = next((level for level in autos if _src(level, 'background')), None)
            if primary is None:
                primary = sorted(autos, key=lambda level: -tokens[level['_id']])[0]
            merged = [level for level in autos if level is not primary and merge(level, primary)]
            # Keep the tallest merged height, but never reach into the next floor up.
            above = [_bottom(level) for level in levels if _bottom(level) > base]
            highest = max(_top(level) for level in [primary, *merged])
            top = min(highest, min(above, default=math.inf) - 1)
            if top > _top(primary):
                changes.append(
                    f'"{primary.get("name")}" top {_text(_top(primary))} -> {_text(top)}'
                )
                if not isinstance(primary.get('elevation'), dict):
                    primary['elevation'] = {}
                primary['elevation']['top'] = int(top) if top.is_integer() else top
                edited[primary['_id']] = primary
            if named:
                changes.append(f'rename "{primary.get("name")}" -> "{named}"')
                primary['name'] = named
                edited[primary['_id']] = primary
        else:
            for rem in autos:
                # Moving onto the kept floor with the same base: what was on the duplicate keeps its
                # other floors and gains none.
                keepers.sort(key=lambda level, rem=rem: abs(_top(level) - _top(rem)))
                merge(rem, keepers[0])
    result = {}
    if not replace and not edited and not kept_back:
        return None, result
    level_ids = set(ids)
    updated = 0
    for key in keys:
        if key.startswith('!scenes.levels!') and key.rsplit('.', 1)[-1] in replace:
            continue
        document = copy.deepcopy(scene if key == scene_key else scenes[key][1])
        if key.startswith('!scenes.levels!') and document.get('_id') in edited:
            document = edited[document['_id']]
        document, touched = _rewrite(document, replace, level_ids)
        if touched:
            updated += 1
        if touched or (key.startswith('!scenes.levels!') and document.get('_id') in edited):
            result[key] = document
    merges = []
    for rem, keep in removed:
        result[level_key(rem['_id'])] = None
        merges.append(
            f'merge "{rem.get("name")}" ({_text(_bottom(rem))}-{_text(_top(rem))}) '
            f'into "{keep.get("name")}"'
        )
    if replace:
        document = result.get(scene_key) or copy.deepcopy(scene)
        document['levels'] = [item for item in document.get('levels') or [] if item not in replace]
        result[scene_key] = document
    finding = {
        'scene': str(scene.get('name') or scene_id),
        'scene_id': scene_id,
        'before': len(levels),
        'after': len(levels) - len(replace),
        'documents_updated': updated,
        # Lines name floors as renamed, in the order: renames and heights, images, merges.
        'changes': changes
        + [
            f'{field} of "{rem.get("name")}" moved to "{keep.get("name")}"'
            for rem, field, keep in carried
        ]
        + merges,
        'removed': sorted(replace),
        'kept_back': [
            f'"{rem.get("name")}" was not merged: its {field} differs from "{keep.get("name")}"'
            for rem, field, keep in kept_back
        ],
    }
    return finding, result


def _floors(scenes):
    """Every scene's floor merges: (findings, {key: new document or None})."""
    by_scene = defaultdict(list)
    for key in scenes:
        parts = key.split('!')
        if len(parts) == 3 and parts[1].startswith('scenes'):
            by_scene[parts[2].split('.')[0]].append(key)
    findings, changes = [], {}
    for key, (_, scene) in scenes.items():
        if kind(key) != 'scenes':
            continue
        finding, result = _scene_floors(key, scene, by_scene[scene.get('_id')], scenes)
        if finding:
            findings.append(finding)
            changes.update(result)
    return findings, changes


# ---- Items


def _item_labels(documents):
    """A readable label for each item key: its owner (world, actor or scene token) and its name."""
    actors = {
        document.get('_id'): document.get('name')
        for key, (_, document) in documents.get('actors', {}).items()
        if kind(key) == 'actors'
    }
    scenes = documents.get('scenes', {})
    names = {
        document.get('_id'): document.get('name')
        for key, (_, document) in scenes.items()
        if kind(key) == 'scenes'
    }
    tokens = {
        key.split('!')[2]: document.get('name')
        for key, (_, document) in scenes.items()
        if kind(key) == 'scenes.tokens'
    }

    def label(key, item):
        owner = key.split('!')[2].split('.')
        if kind(key) == 'actors.items':
            prefix = actors.get(owner[0]) or owner[0]
        elif kind(key) == 'scenes.tokens.delta.items':
            token = tokens.get('.'.join(owner[:2])) or owner[1]
            prefix = f'{names.get(owner[0]) or owner[0]} › {token}'
        else:
            prefix = 'world'
        return f'{prefix}: {item.get("name")}'

    return label


def _items(documents):
    """(collection, key, document) for every item: world items, actor items and token-delta items."""
    for collection in ('actors', 'items', 'scenes'):
        for key, (_, document) in documents.get(collection, {}).items():
            if kind(key) in ITEM_KINDS:
                yield collection, key, document


def _repair_item(item, label, findings):
    """Fix one item's relative source ID and "[object Object]" fields in place; True if changed."""
    changed = False
    dnd5e = _dict(_dict(item.get('flags')).get('dnd5e'))
    source = dnd5e.get('sourceId')
    if (
        isinstance(source, str)
        and source.startswith('.')
        and not _dict(item.get('_stats')).get('compendiumSource')
    ):
        del dnd5e['sourceId']
        findings['source_ids'].append(label)
        changed = True
    system = _dict(item.get('system'))

    def fixed(field):
        findings['formulas'].append({'item': label, 'field': field})

    for activity in _dict(system.get('activities')).values():
        for target in _dict(_dict(activity).get('consumption')).get('targets') or []:
            if isinstance(target, dict) and _bad(target.get('target')):
                target['target'] = ''
                fixed(f'{activity.get("name") or activity.get("type")} consumption target')
                changed = True
    damage = _dict(system.get('damage'))
    uses = _dict(system.get('uses'))
    if _bad(damage.get('versatile')):
        damage['versatile'] = ''
        fixed('versatile damage')
        changed = True
    if _bad(uses.get('recovery')):
        uses['recovery'] = ''
        fixed('recharge')
        changed = True
    versatile = _dict(damage.get('versatile'))
    if _bad(_dict(versatile.get('custom')).get('formula')):
        versatile['custom'] = {'enabled': False, 'formula': ''}
        fixed('versatile damage')
        changed = True
    recovery = uses.get('recovery')
    if isinstance(recovery, list) and any(_bad(_dict(entry).get('formula')) for entry in recovery):
        uses['recovery'] = [entry for entry in recovery if not _bad(_dict(entry).get('formula'))]
        fixed('recharge')
        changed = True
    return changed


def _corrupted(value, path):
    """Paths of strings still holding "[object Object]" anywhere in a value."""
    if isinstance(value, dict):
        return [found for key, item in value.items() for found in _corrupted(item, f'{path}.{key}')]
    if isinstance(value, list):
        return [found for item in value for found in _corrupted(item, f'{path}[]')]
    return [path] if _bad(value) else []


# ---- Summons


def _words(text):
    return re.findall(r'[a-z0-9]+', str(text).lower())


def _linked_name(link):
    """The creature a link names: 'shadow wolf (alpha)' in '@creature[shadow wolf (alpha)|xyz]'."""
    found = re.match(r'^@\w+\[([^\]|]+)', link)
    return found.group(1).strip() if found else link


def _contains(words, part):
    return bool(part) and any(
        words[at : at + len(part)] == part for at in range(len(words) - len(part) + 1)
    )


def _candidates(link, actors):
    """World actors whose names match the linked creature, best first (at most five)."""
    full = _words(_linked_name(link))
    base = _words(re.sub(r'\([^)]*\)', ' ', _linked_name(link)))
    ranked = []
    for uuid, name in actors:
        words = _words(name)
        rank = 0 if words == full else 1 if words == base else 2 if _contains(words, base) else None
        if rank is not None:
            ranked.append((rank, str(name), uuid))
    return [{'uuid': uuid, 'name': name} for _, name, uuid in sorted(ranked)[:5]]


def _summons(documents, changes, label, actors, chosen):
    """Summon profiles whose uuid is not a UUID; points the GM's choices at their actors."""
    findings = []
    for collection, key, item in _items(documents):
        current = changes[collection].get(key) or item
        edited = None
        for activity_id, activity in _dict(_dict(current.get('system')).get('activities')).items():
            if _dict(activity).get('type') != 'summon':
                continue
            for index, profile in enumerate(activity.get('profiles') or []):
                uuid = _dict(profile).get('uuid')
                if not isinstance(uuid, str) or not uuid or UUID.fullmatch(uuid):
                    continue
                finding_id = f'{key}|{activity_id}|{profile.get("_id") or index}'
                findings.append(
                    {
                        'id': finding_id,
                        'item': label(key, item),
                        'activity': str(activity.get('name') or 'Summon'),
                        'profile': str(profile.get('name') or ''),
                        'uuid': uuid,
                        'candidates': _candidates(uuid, actors),
                    }
                )
                if finding_id in chosen:
                    edited = edited or copy.deepcopy(current)
                    profiles = edited['system']['activities'][activity_id]['profiles']
                    profiles[index]['uuid'] = chosen[finding_id]
        if edited:
            changes[collection][key] = edited
    return findings


# ---- Macros


def _skip_string(source, at):
    """The index just past the string or template literal that starts at `at`."""
    quote, at, depth = source[at], at + 1, 0
    while at < len(source):
        char = source[at]
        if char == '\\':
            at += 2
            continue
        if quote == '`' and source.startswith('${', at):
            depth += 1
            at += 2
            continue
        if quote == '`' and depth and char == '}':
            depth -= 1
        elif char == quote and not depth:
            return at + 1
        at += 1
    return at


def _colon_after(source, at):
    while at < len(source) and source[at] in ' \t\r\n':
        at += 1
    return source.startswith(':', at) and not source.startswith('::', at)


def _object_keys(source):
    """Each braced block's top-level `key:` names in a script: [[(name, start, end), ...], ...].

    A light scanner, not a JavaScript parser: it skips strings and comments and reads a key right after
    `{` or `,`. Its findings are shown to the GM before anything is rewritten.
    """
    literals, stack, at = [], [], 0
    while at < len(source):
        char = source[at]
        top = stack[-1] if stack and isinstance(stack[-1], dict) else None
        if source.startswith('//', at):
            end = source.find('\n', at)
            at = len(source) if end < 0 else end
            continue
        if source.startswith('/*', at):
            end = source.find('*/', at + 2)
            at = len(source) if end < 0 else end + 2
            continue
        if char in '\'"`':
            end = _skip_string(source, at)
            if top and top['expect'] and char != '`' and _colon_after(source, end):
                top['keys'].append((source[at + 1 : end - 1], at + 1, end - 1))
            if top:
                top['expect'] = False
            at = end
            continue
        if char == '{':
            stack.append({'keys': [], 'expect': True})
        elif char in '([':
            stack.append(None)
        elif char in '}])':
            closed = stack.pop() if stack else None
            if isinstance(closed, dict):
                literals.append(closed['keys'])
            if stack and isinstance(stack[-1], dict):
                stack[-1]['expect'] = False
        elif char == ',' and top:
            top['expect'] = True
        elif top and top['expect'] and (char.isalpha() or char in '_$'):
            end = at
            while end < len(source) and (source[end].isalnum() or source[end] in '_$'):
                end += 1
            if _colon_after(source, end):
                top['keys'].append((source[at:end], at, end))
            top['expect'] = False
            at = end
            continue
        elif top and not char.isspace():
            top['expect'] = False
        at += 1
    return literals


def _macros(documents, chosen, changes):
    """Script macros that create ActiveEffects with `label`/`icon`; rewrites the GM's choices."""
    findings = []
    for key, (_, macro) in documents.get('macros', {}).items():
        command = macro.get('command')
        if kind(key) != 'macros' or macro.get('type') != 'script' or not isinstance(command, str):
            continue
        lines = command.splitlines()
        uses = []
        for keys in _object_keys(command):
            names = {name for name, _, _ in keys}
            if not names & EFFECT_KEYS:
                continue
            for name, start, end in keys:
                if name in RENAMED_EFFECT_KEYS:
                    line = command.count('\n', 0, start) + 1
                    uses.append(
                        {
                            'line': line,
                            'field': name,
                            'replacement': RENAMED_EFFECT_KEYS[name],
                            'text': lines[line - 1].strip()[:160] if line <= len(lines) else '',
                            # A literal that already has the new key is left for the GM.
                            'rewrite': RENAMED_EFFECT_KEYS[name] not in names,
                            'span': (start, end),
                        }
                    )
        if not uses:
            continue
        rewritable = any(use['rewrite'] for use in uses)
        if key in chosen and rewritable:
            rewritten = command
            for use in sorted(uses, key=lambda use: use['span'][0], reverse=True):
                if use['rewrite']:
                    start, end = use['span']
                    rewritten = rewritten[:start] + use['replacement'] + rewritten[end:]
            changes['macros'][key] = {**copy.deepcopy(macro), 'command': rewritten}
        findings.append(
            {
                'id': key,
                'name': str(macro.get('name') or ''),
                'uses': [
                    {name: value for name, value in use.items() if name != 'span'} for use in uses
                ],
                'rewritable': rewritable,
            }
        )
    return findings


# ---- Plan, write operations and verification


def plan(documents, choices=None):
    """Findings and the changes that fix them: (findings, {collection: {key: document or None}}).

    Floors, relative source IDs and "[object Object]" fields are always repaired. Summon links and
    macros change only as `choices` says: {'summons': {finding id: 'Actor.<id>'}, 'macros': [keys]}.
    """
    choices = choices if isinstance(choices, dict) else {}
    findings = {key: [] for key in ('source_ids', 'formulas', 'other_fields')}
    changes = defaultdict(dict)
    findings['floors'], floor_changes = _floors(documents.get('scenes', {}))
    changes['scenes'].update(floor_changes)
    label = _item_labels(documents)
    for collection, key, item in _items(documents):
        edited = copy.deepcopy(item)
        name = label(key, item)
        if _repair_item(edited, name, findings):
            changes[collection][key] = edited
        findings['other_fields'] += [
            {'item': name, 'field': path} for path in _corrupted(edited.get('system'), 'system')
        ]
    actors = [
        (f'Actor.{document["_id"]}', document.get('name'))
        for key, (_, document) in documents.get('actors', {}).items()
        if kind(key) == 'actors' and isinstance(document.get('_id'), str)
    ]
    chosen = choices.get('summons') or {}
    if not isinstance(chosen, dict) or any(
        not isinstance(value, str) or value not in {uuid for uuid, _ in actors}
        for value in chosen.values()
    ):
        raise ValueError('Each summon choice must be an actor in this world.')
    findings['summons'] = _summons(documents, changes, label, actors, chosen)
    unknown = set(chosen) - {finding['id'] for finding in findings['summons']}
    macros = choices.get('macros') or []
    if not isinstance(macros, list) or not all(isinstance(item, str) for item in macros):
        raise ValueError('Macro choices must be a list of macro IDs.')
    findings['macros'] = _macros(documents, set(macros), changes)
    unknown |= set(macros) - {item['id'] for item in findings['macros'] if item['rewritable']}
    if unknown:
        raise ValueError('A chosen repair is not in this dry run. Run the dry run again.')
    return findings, {collection: rows for collection, rows in changes.items() if rows}


def pending(findings):
    """How many automatic repairs a dry run found; floors kept back for their images do not count."""
    return {
        'floors': sum(1 for item in findings['floors'] if item['removed'] or item['changes']),
        'source_ids': len(findings['source_ids']),
        'formulas': len(findings['formulas']),
    }


def summary(findings):
    """Counts for a report heading."""
    floors = findings.get('floors') or []
    return {
        'scenes': len(floors),
        'floors_before': sum(item['before'] for item in floors),
        'floors_after': sum(item['after'] for item in floors),
        'source_ids': len(findings.get('source_ids') or []),
        'formulas': len(findings.get('formulas') or []),
        'other_fields': len(findings.get('other_fields') or []),
        'summons': len(findings.get('summons') or []),
        'macros': len(findings.get('macros') or []),
    }


def operations(documents, changes):
    """[(collection, ops)] for `foundry_leveldb_write`; each op names the bytes it was planned from."""
    result = []
    for collection in sorted(changes):
        ops = []
        for key in sorted(changes[collection]):
            stored = documents[collection].get(key)
            expected = hashlib.sha256(stored[0]).hexdigest() if stored else None
            value = changes[collection][key]
            if value is None:
                ops.append({'type': 'del', 'key': key, 'expected_sha256': expected})
            else:
                ops.append({'type': 'put', 'key': key, 'expected_sha256': expected, 'value': value})
        result.append((collection, ops))
    return result


def _strings(value):
    if isinstance(value, dict):
        return {found for key, item in value.items() if key != '_id' for found in _strings(item)}
    if isinstance(value, list):
        return {found for item in value for found in _strings(item)}
    return {value} if isinstance(value, str) else set()


def _scene_of(key):
    return key.split('!')[2].split('.')[0]


def _level_images(scenes):
    images = defaultdict(set)
    for key, (_, level) in scenes.items():
        if kind(key) == 'scenes.levels':
            for field in LEVEL_IMAGES:
                if _src(level, field):
                    images[_scene_of(key)].add(_src(level, field))
    return images


def verify(before, after, changes):
    """Compare a repaired world with its backup: {'ok', 'checks': [{'name', 'ok', 'detail'}]}."""
    checks = []

    def check(name, problems, passed):
        checks.append({'name': name, 'ok': not problems, 'detail': problems[:20] or [passed]})

    problems = []
    for collection in sorted(set(before) | set(after)):
        old, new = before.get(collection, {}), after.get(collection, {})
        planned = changes.get(collection, {})
        problems += [f'{collection}: unexpected new document {key}' for key in set(new) - set(old)]
        for key, (stored, _) in old.items():
            if key in planned and planned[key] is None:
                if key in new:
                    problems.append(f'{collection}: {key} was not removed')
            elif key in planned:
                if key not in new or new[key][1] != planned[key]:
                    problems.append(f'{collection}: {key} was not written as planned')
            elif key not in new:
                problems.append(f'{collection}: {key} was lost')
            elif new[key][0] != stored:
                problems.append(f'{collection}: {key} changed without a planned repair')
    written = sum(len(rows) for rows in changes.values())
    check('Only planned documents changed', problems, f'{written} planned changes; nothing else')

    removed = Counter(
        kind(key) for rows in changes.values() for key, value in rows.items() if value is None
    )
    counted_before = Counter(kind(key) for rows in before.values() for key in rows)
    counted_after = Counter(kind(key) for rows in after.values() for key in rows)
    problems = [
        f'{name}: {counted_before[name]} before, {counted_after[name]} after'
        for name in sorted(set(counted_before) | set(counted_after))
        if counted_after[name] != counted_before[name] - removed[name]
        or (removed[name] and name != 'scenes.levels')
    ]
    floors = removed['scenes.levels']
    check(
        'Document counts change only for merged floors',
        problems,
        f'Scene floors {counted_before["scenes.levels"]} → {counted_after["scenes.levels"]} '
        f'({floors} merged); every other count unchanged',
    )

    gone = defaultdict(set)
    for key, value in changes.get('scenes', {}).items():
        if value is None and kind(key) == 'scenes.levels':
            gone[_scene_of(key)].add(key.rsplit('.', 1)[-1])
    problems = [
        f'{key} still names a merged floor'
        for key, (_, document) in after.get('scenes', {}).items()
        if gone.get(_scene_of(key)) and _strings(document) & gone[_scene_of(key)]
    ]
    check('No reference to a merged floor', problems, 'No document names a merged floor')

    problems = []
    for key, (_, document) in before.get('scenes', {}).items():
        new = after.get('scenes', {}).get(key)
        if not new or not kind(key).startswith('scenes.') or kind(key) == 'scenes.levels':
            continue
        if (document.get('levels') and not new[1].get('levels')) or (
            document.get('level') and not new[1].get('level')
        ):
            problems.append(f'{key} lost all its floors')
    check('Nothing lost all its floors', problems, 'Everything on a floor still has one')

    images = _level_images(after.get('scenes', {}))
    problems = [
        f'Scene {scene}: floor image {source} is no longer on any floor'
        for scene, sources in _level_images(before.get('scenes', {})).items()
        for source in sorted(sources - images.get(scene, set()))
    ]
    check('Every floor image kept', problems, 'Every floor background and foreground is kept')

    left = pending(plan(after)[0])
    problems = [f'{count} {PENDING[name]} left' for name, count in left.items() if count]
    check(
        'No duplicate floors, relative source IDs or "[object Object]" fields left',
        problems,
        'A new dry run finds nothing left to repair automatically',
    )
    return {'ok': all(item['ok'] for item in checks), 'checks': checks}
