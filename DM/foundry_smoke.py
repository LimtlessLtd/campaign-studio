"""Encounter smoke test for a dnd5e world read offline, with Foundry closed or open.

Every action of a chosen party and of the NPCs placed on chosen scenes is turned into its formulas and
links: each formula must parse and roll to a number (`foundry_roll`), linked spells and summoned actors
must exist, and consumption must point at something real. A simple exchange of best attacks then gives a
rough number of rounds for each side to win. Foundry's own dice, active effects, module automation and
macros are not run, so this finds broken data, not balance.
"""

import json
import math
import random
from collections import defaultdict
from pathlib import Path

import foundry_leveldb
import foundry_repair
from foundry_compat import ID_RE
from foundry_roll import FormulaError, average

COLLECTIONS = ('actors', 'items', 'scenes', 'folders')
NOTE = (
    'Rounds to win are a rough sanity check, not a balance prediction: each side uses its best attack '
    'every round against the other side’s average armour class, with no spells, effects, resistances, '
    'module automation or tactics.'
)
HOSTILE = -1


def _dict(value):
    return value if isinstance(value, dict) else {}


def _int(value, default=0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return int(number) if math.isfinite(number) else default


class _World:
    """The documents the smoke test reads, with UUID lookups into the world and its compendiums."""

    def __init__(self, documents, world, user_data):
        self.world = Path(world)
        self.user_data = Path(user_data)
        self.actors, self.items, self.scenes, self.tokens, self.folders = {}, {}, {}, [], {}
        for key, (_, document) in documents['actors'].items():
            if foundry_repair.kind(key) == 'actors':
                system = _dict(document.get('system'))
                self.actors[document.get('_id')] = {**document, 'system': system, 'items': []}
        for key, (_, document) in documents['actors'].items():
            if foundry_repair.kind(key) == 'actors.items':
                actor = self.actors.get(key.split('!')[2].split('.')[0])
                if actor:
                    actor['items'].append(document)
        for key, (_, document) in documents['items'].items():
            if foundry_repair.kind(key) == 'items':
                self.items[document.get('_id')] = document
        for key, (_, document) in documents['scenes'].items():
            if foundry_repair.kind(key) == 'scenes':
                self.scenes[document.get('_id')] = document
            elif foundry_repair.kind(key) == 'scenes.tokens':
                self.tokens.append((key.split('!')[2].split('.')[0], document))
        for key, (_, document) in documents['folders'].items():
            if foundry_repair.kind(key) == 'folders':
                self.folders[document.get('_id')] = document
        self.packs = {}

    def _pack(self, scope, name):
        if not ID_RE.fullmatch(scope) or not ID_RE.fullmatch(name):
            return None  # Not a package and pack name, so never a folder of this User Data.
        if (scope, name) not in self.packs:
            if scope == 'world':
                folders = [self.world / 'packs' / name]
            else:
                folders = [
                    self.user_data / 'Data' / kind / scope / 'packs' / name
                    for kind in ('systems', 'modules')
                ]
            ids = None
            for folder in folders:
                if (folder / 'CURRENT').is_file():
                    try:
                        ids = {
                            key.decode('utf-8', 'replace').rsplit('!', 1)[-1]
                            for key in foundry_leveldb.read(folder)
                            if key.count(b'!') == 2 and b'.' not in key.split(b'!')[1]
                        }
                    except (foundry_leveldb.LevelDBError, OSError):
                        ids = None
                    break
            self.packs[(scope, name)] = ids
        return self.packs[(scope, name)]

    def exists(self, uuid):
        """True or False for a UUID in this world or an installed compendium; None when unknown."""
        parts = str(uuid or '').split('.')
        if parts[0] == 'Item' and len(parts) == 2:
            return parts[1] in self.items
        if parts[0] == 'Actor' and len(parts) == 2:
            return parts[1] in self.actors
        if parts[0] == 'Actor' and len(parts) == 4 and parts[2] == 'Item':
            actor = self.actors.get(parts[1])
            return bool(actor) and any(item.get('_id') == parts[3] for item in actor['items'])
        if parts[0] == 'Compendium' and len(parts) >= 4:
            ids = self._pack(parts[1], parts[2])
            return None if ids is None else parts[-1] in ids
        if foundry_repair.UUID.fullmatch(str(uuid or '')):
            return None
        return False


# ---- Approximate dnd5e rules


def _modifier(actor, ability):
    return math.floor(
        (_int(_dict(_dict(actor['system'].get('abilities')).get(ability)).get('value'), 10) - 10)
        / 2
    )


def _level(actor):
    if actor.get('type') != 'character':
        return 0
    return sum(
        _int(_dict(item.get('system')).get('levels'))
        for item in actor['items']
        if item.get('type') == 'class'
    )


def _challenge(actor):
    try:
        value = float(_dict(_dict(actor['system'].get('details'))).get('cr') or 0)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _proficiency(actor):
    tier = _level(actor) if actor.get('type') == 'character' else _challenge(actor)
    return math.floor((max(tier, 1) - 1) / 4) + 2


def _ability(actor, item, activity):
    system = _dict(item.get('system'))
    explicit = (
        _dict(_dict(activity).get('attack')).get('ability')
        or _dict(activity).get('ability')
        or system.get('ability')
    )
    spellcasting = _dict(actor['system'].get('attributes')).get('spellcasting') or 'int'
    if explicit and explicit not in ('none', 'spellcasting'):
        return explicit
    if explicit == 'spellcasting' or item.get('type') == 'spell':
        return spellcasting
    properties = set(system.get('properties') or [])
    ranged = _dict(system.get('type')).get('value') in ('simpleR', 'martialR') or str(
        system.get('actionType') or ''
    ) in ('rwak', 'rsak')
    if 'fin' in properties:
        return 'dex' if _modifier(actor, 'dex') >= _modifier(actor, 'str') else 'str'
    return 'dex' if ranged else 'str'


def _armour_class(actor):
    ac = _dict(_dict(actor['system'].get('attributes')).get('ac'))
    flat = ac.get('flat')
    if isinstance(flat, (int, float)) and not isinstance(flat, bool) and math.isfinite(flat):
        return int(flat)

    def equipped(kind):
        return [
            item
            for item in actor['items']
            if item.get('type') == 'equipment'
            and _dict(item.get('system')).get('equipped')
            and _dict(_dict(item.get('system')).get('type')).get('value') in kind
        ]

    armour = equipped(('light', 'medium', 'heavy', 'natural'))
    shield = 2 if equipped(('shield',)) else 0
    dexterity = _modifier(actor, 'dex')
    if armour:
        system = _dict(armour[0].get('system'))
        weight = _dict(system.get('type')).get('value')
        bonus = 0 if weight == 'heavy' else min(dexterity, 2) if weight == 'medium' else dexterity
        values = _dict(system.get('armor'))
        return (_int(values.get('value')) or 10) + bonus + shield + _int(values.get('magicalBonus'))
    return 10 + dexterity + shield


def _hit_points(actor):
    hp = _dict(_dict(actor['system'].get('attributes')).get('hp'))
    return _int(hp.get('max')) or _int(hp.get('value')) or 1


def _resolver(actor, item, ability):
    def resolve(path):
        if path == 'mod':
            return _modifier(actor, ability)
        if path in ('prof', 'attributes.prof'):
            return _proficiency(actor)
        parts = path.split('.')
        if len(parts) == 3 and parts[0] == 'abilities' and parts[2] in ('mod', 'value'):
            if parts[2] == 'mod':
                return _modifier(actor, parts[1])
            return _int(
                _dict(_dict(actor['system'].get('abilities')).get(parts[1])).get('value'), 10
            )
        if path == 'details.level':
            return _level(actor)
        if path == 'details.cr':
            return _challenge(actor)
        if len(parts) == 3 and parts[0] == 'classes' and parts[2] == 'levels':
            for entry in actor['items']:
                system = _dict(entry.get('system'))
                if entry.get('type') == 'class' and parts[1] in (
                    system.get('identifier'),
                    str(entry.get('name')).lower(),
                ):
                    return _int(system.get('levels'))
            return 0
        if path == 'item.level':
            return _int(_dict(item.get('system')).get('level'))
        return None  # dnd5e resolves it at run time, such as @scale.*

    return resolve


# ---- Actions


def _actions(actor):
    for item in actor['items']:
        activities = [
            value
            for value in _dict(_dict(item.get('system')).get('activities')).values()
            if isinstance(value, dict)
        ]
        if activities:
            for activity in activities:
                yield item, activity, False
        elif _dict(item.get('system')).get('actionType'):
            yield item, {}, True


def _part_formula(part):
    part = _dict(part)
    custom = _dict(part.get('custom'))
    if custom.get('enabled'):
        return custom.get('formula')
    if part.get('number') and part.get('denomination'):
        bonus = f' + {part["bonus"]}' if part.get('bonus') else ''
        return f'{part["number"]}d{part["denomination"]}{bonus}'
    return part.get('bonus') or ''


def _formulas(item, activity, legacy):
    system = _dict(item.get('system'))
    if legacy:
        return [
            part[0]
            for part in _dict(system.get('damage')).get('parts') or []
            if isinstance(part, list) and part
        ]
    formulas = []
    damage = _dict(activity.get('damage'))
    base = _dict(_dict(system.get('damage')).get('base'))
    if damage.get('includeBase') and base:
        if _dict(base.get('custom')).get('enabled'):
            formulas.append(base['custom'].get('formula'))
        elif base.get('number') and base.get('denomination'):
            formulas.append(f'{base["number"]}d{base["denomination"]} + @mod')
        else:
            formulas.append('')
    parts = list(damage.get('parts') or [])
    if activity.get('healing'):
        parts.append(activity['healing'])
    formulas += [_part_formula(part) for part in parts]
    roll = _dict(activity.get('roll'))
    if _dict(activity.get('check')).get('ability') is None and roll.get('formula'):
        formulas.append(roll['formula'])
    dc = _dict(_dict(activity.get('save')).get('dc'))
    if not dc.get('calculation') and dc.get('formula'):
        formulas.append(dc['formula'])
    return formulas


def _test(world, actor, rolls, rng, problems):
    result = {
        'name': str(actor.get('name') or ''),
        'ac': _armour_class(actor),
        'hp': _hit_points(actor),
        'actions': 0,
        'failures': 0,
        'best': {'name': None, 'to_hit': 0, 'average': 0.0},
        'runtime_refs': [],
    }
    runtime = set()
    for item in actor['items']:
        if '[object Object]' in json.dumps(item.get('system') or {}):
            problems.append(
                f'{result["name"]} › {item.get("name")}: has a corrupted "[object Object]" field'
            )
            result['failures'] += 1
    for item, activity, legacy in _actions(actor):
        system = _dict(item.get('system'))
        kind = system.get('actionType') if legacy else activity.get('type')
        where = f'{result["name"]} › {item.get("name")}' + (
            f' › {activity["name"]}' if activity.get('name') else ''
        )
        result['actions'] += 1
        before = len(problems)
        ability = _ability(actor, item, activity)
        resolve = _resolver(actor, item, ability)
        if kind == 'cast':
            uuid = _dict(activity.get('spell')).get('uuid')
            if world.exists(uuid) is False:
                problems.append(f'{where}: linked spell {uuid} not found')
        if kind == 'summon':
            for profile in activity.get('profiles') or []:
                uuid = _dict(profile).get('uuid')
                if uuid and world.exists(uuid) is False:
                    problems.append(f'{where}: summon "{profile.get("name")}" -> {uuid} not found')
        for target in _dict(activity.get('consumption')).get('targets') or []:
            target = _dict(target)
            aim = target.get('target')
            if foundry_repair.BAD in str(aim or ''):
                problems.append(f'{where}: consumption target is "[object Object]"')
            elif target.get('type') in ('itemUses', 'material') and aim:
                if not any(
                    aim in (entry.get('_id'), _dict(entry.get('system')).get('identifier'))
                    for entry in actor['items']
                ):
                    problems.append(
                        f'{where}: consumes {"uses of " if target["type"] == "itemUses" else ""}missing item {aim}'
                    )
            elif (
                target.get('type') == 'itemUses'
                and not str(_dict(system.get('uses')).get('max') or '').strip()
            ):
                problems.append(f'{where}: consumes its own uses but the item has none')
        damage = 0.0
        for formula in _formulas(item, activity, legacy):
            try:
                mean, unknown = average(formula, resolve, rolls, rng)
            except FormulaError as error:
                problems.append(f'{where}: {error}')
                continue
            damage += mean
            runtime.update(unknown)
        if kind == 'attack' or (legacy and str(kind).endswith('ak')):
            attack = _dict(activity.get('attack'))
            try:
                formula = attack.get('bonus') or _dict(system.get('attack')).get('bonus') or '0'
                bonus, _ = average(formula, resolve, 1, rng)
            except FormulaError as error:
                problems.append(f'{where}: {error}')
                bonus = 0.0
            to_hit = (
                _modifier(actor, ability)
                + (0 if attack.get('flat') else _proficiency(actor))
                + bonus
                + _int(system.get('magicalBonus'))
            )
            best = result['best']
            if damage + to_hit / 10 > best['average'] + best['to_hit'] / 10:
                name = str(item.get('name')) + (
                    f' ({activity["name"]})' if activity.get('name') else ''
                )
                result['best'] = {
                    'name': name,
                    'to_hit': round(to_hit),
                    'average': round(damage, 1),
                }
        if len(problems) > before:
            result['failures'] += 1
    result['runtime_refs'] = sorted(runtime)
    return result


def _rounds(attackers, defenders):
    """Damage per round of one side's best attacks and the rounds it needs to drop the other side."""
    if not attackers or not defenders:
        return 0, None
    armour = round(sum(item['ac'] for item in defenders) / len(defenders))
    per_round = sum(
        min(0.95, max(0.05, (21 - (armour - item['best']['to_hit'])) / 20))
        * item['best']['average']
        for item in attackers
        if item['best']['name']
    )
    health = sum(item['hp'] for item in defenders)
    return round(per_round), (math.ceil(health / per_round) if per_round else None)


def load(world):
    """The world documents the smoke test and its choices read."""
    return foundry_repair.load(world, COLLECTIONS)


def choices(documents):
    """Actor folders (with their characters) and scenes (with their NPC tokens) to choose from."""
    data = _World(documents, '.', '.')
    characters = defaultdict(int)
    for actor in data.actors.values():
        if actor.get('type') == 'character':
            characters[actor.get('folder')] += 1
    npcs = defaultdict(int)
    for scene_id, token in data.tokens:
        if _dict(data.actors.get(token.get('actorId'))).get('type') == 'npc':
            npcs[scene_id] += 1
    folders = sorted(
        (
            {
                'id': folder_id,
                'name': str(folder.get('name') or folder_id),
                'characters': characters[folder_id],
            }
            for folder_id, folder in data.folders.items()
            if folder.get('type') == 'Actor'
        ),
        key=lambda item: (-item['characters'], item['name'].lower()),
    )
    scenes = sorted(
        (
            {'id': scene_id, 'name': str(scene.get('name') or scene_id), 'npcs': npcs[scene_id]}
            for scene_id, scene in data.scenes.items()
        ),
        key=lambda item: item['name'].lower(),
    )
    return {'folders': folders, 'scenes': scenes}


def run(world, user_data, party_folder, scene_ids, rolls=100, seed=1):
    """Smoke-test the party in one actor folder against the NPCs on each chosen scene.

    `world` is the world folder and `user_data` the User Data folder whose systems and modules hold
    the compendiums that links point into.
    """
    data = _World(load(world), world, user_data)
    if party_folder not in data.folders or data.folders[party_folder].get('type') != 'Actor':
        raise ValueError('Choose an actor folder in this world for the party.')
    if (
        not isinstance(scene_ids, list)
        or not scene_ids
        or any(item not in data.scenes for item in scene_ids)
    ):
        raise ValueError('Choose one or more scenes in this world.')
    if not isinstance(rolls, int) or isinstance(rolls, bool) or not 1 <= rolls <= 1000:
        raise ValueError('Rolls per formula must be between 1 and 1000.')
    rng = random.Random(seed)
    problems = []
    members = sorted(
        (
            actor
            for actor in data.actors.values()
            if actor.get('type') == 'character' and actor.get('folder') == party_folder
        ),
        key=lambda actor: str(actor.get('name')),
    )
    if not members:
        raise ValueError('That folder has no player characters.')
    party = [_test(data, actor, rolls, rng, problems) for actor in members]
    encounters = []
    for scene_id in dict.fromkeys(scene_ids):
        tokens = [
            token
            for found, token in data.tokens
            if found == scene_id
            and _dict(data.actors.get(token.get('actorId'))).get('type') == 'npc'
        ]
        tested = {
            actor_id: _test(data, data.actors[actor_id], rolls, rng, problems)
            for actor_id in dict.fromkeys(token['actorId'] for token in tokens)
        }
        hostile = [token for token in tokens if token.get('disposition') == HOSTILE] or tokens
        enemies = [tested[token['actorId']] for token in hostile]
        party_damage, party_rounds = _rounds(party, enemies)
        enemy_damage, enemy_rounds = _rounds(enemies, party)
        results = list(tested.values())
        encounters.append(
            {
                'scene': str(data.scenes[scene_id].get('name') or scene_id),
                'scene_id': scene_id,
                'npcs': len(results),
                'actions': sum(item['actions'] for item in results),
                'failing_actions': sum(item['failures'] for item in results),
                'enemies': len(enemies),
                # The exchange uses the hostile tokens, or every NPC token when none is hostile.
                'enemies_hostile_only': any(
                    token.get('disposition') == HOSTILE for token in tokens
                ),
                'party_damage_per_round': party_damage,
                'enemy_damage_per_round': enemy_damage,
                'rounds_for_party_to_win': party_rounds,
                'rounds_for_enemies_to_win': enemy_rounds,
                'results': results,
            }
        )
    return {
        'rolls': rolls,
        'note': NOTE,
        'party_folder': str(data.folders[party_folder].get('name') or party_folder),
        'party': party,
        'encounters': encounters,
        'problems': list(dict.fromkeys(problems)),
    }
