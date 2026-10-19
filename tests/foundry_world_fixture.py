"""A synthetic Foundry v14 dnd5e world with each post-migration defect the repair step fixes.

Every name and ID is invented. `write` stores the documents as LevelDB folders, either with the
test writer (`leveldb_writer`, read by Studio's reader) or, for the write path, with the classic-level
library that Foundry bundles (installed as a development dependency).
"""

import json
import shutil
import subprocess
from pathlib import Path

import leveldb_writer

ROOT = Path(__file__).resolve().parents[1]
CLASSIC_LEVEL = ROOT / 'node_modules' / 'classic-level'

# Scene "Lighthouse": the GM defined Ground (0-9) and Lamp Room (10-19); the migration added a
# duplicate at each base.
LIGHTHOUSE = 'sceneKeep0000001'
GROUND, GROUND_DUP = 'lvlGround0000001', 'lvlDupGround0001'
LAMP, LAMP_DUP = 'lvlLamp000000001', 'lvlLampDup000001'
# Scene "Watchtower": no floors defined, three automatic floors share base 0, one more sits at 60.
WATCHTOWER = 'sceneAuto0000001'
TOWER = ('lvlTower00000001', 'lvlTower00000002', 'lvlTower00000003', 'lvlTower00000004')
# Scene "Ruins": two automatic floors at one base with different backgrounds.
RUINS = 'sceneClash000001'
RUINS_DAY, RUINS_NIGHT = 'lvlRuinsDay00001', 'lvlRuinsNight001'
# Scene "Airship": a lone automatic floor the GM named Deck.
AIRSHIP = 'sceneRename00001'
DECK, HOLD = 'lvlDeck000000001', 'lvlHold000000001'
MARKET = 'scenePlain000001'

PARTY, FOES = 'folderParty00001', 'folderFoes000001'
HERO, SUMMONER = 'actorHero0000001', 'actorSummoner001'
WOLF, DIRE_WOLF, BANDIT = 'actorWolf0000001', 'actorDireWolf001', 'actorBandit00001'
TOKEN_BANDIT, TOKEN_WOLF = 'tokenBandit00001', 'tokenWolf0000001'
SPELL = 'worldSpell000001'
PACK_SPELL = 'spellPresent0001'


def _level(level_id, name, bottom, top, background=None, foreground=None, visible=()):
    return {
        '_id': level_id,
        'name': name,
        'elevation': {'bottom': bottom, 'top': top},
        'background': {'src': background, 'tint': '#ffffff'},
        'foreground': {'src': foreground, 'tint': '#ffffff'},
        'fog': {'src': None},
        'visibility': {'levels': ['defaultLevel0000', *visible]},
        'sort': 0,
        'flags': {},
    }


def _scene(scene_id, name, levels, intended=(), initial='defaultLevel0000'):
    return {
        '_id': scene_id,
        'name': name,
        'levels': levels,
        'initialLevel': initial,
        'flags': {'levels': {'sceneLevels': [list(entry) for entry in intended]}},
        'grid': {'size': 100, 'distance': 5},
    }


def _attack(activity_id, **extra):
    return {
        activity_id: {
            '_id': activity_id,
            'type': 'attack',
            'attack': {'ability': '', 'bonus': '', 'flat': False},
            'damage': {'includeBase': True, 'parts': []},
            **extra,
        }
    }


def documents():
    """{collection: {key: document}} for the synthetic world."""
    scenes = {}

    def add_scene(scene, levels):
        scenes[f'!scenes!{scene["_id"]}'] = scene
        for level in levels:
            scenes[f'!scenes.levels!{scene["_id"]}.{level["_id"]}'] = level

    lighthouse_ids = [GROUND, GROUND_DUP, LAMP, LAMP_DUP]
    add_scene(
        _scene(
            LIGHTHOUSE,
            'Lighthouse',
            lighthouse_ids,
            [('0', '9', 'Ground'), ('10', '19', 'Lamp Room')],
            initial=GROUND_DUP,
        ),
        [
            _level(GROUND, 'Ground', 0, 9, visible=lighthouse_ids),
            _level(
                GROUND_DUP,
                'Lighthouse - Level (0|15)',
                0,
                15,
                background='maps/lighthouse.webp',
                visible=lighthouse_ids,
            ),
            _level(LAMP, 'Lighthouse - Level (10|19)', 10, 19, visible=lighthouse_ids),
            _level(
                LAMP_DUP,
                'Lighthouse - Level (10|30)',
                10,
                30,
                foreground='maps/lamp-roof.webp',
                visible=lighthouse_ids,
            ),
        ],
    )
    placed = {
        f'!scenes.walls!{LIGHTHOUSE}.wallOne000000001': {
            '_id': 'wallOne000000001',
            'c': [0, 0, 100, 0],
            'levels': [GROUND_DUP, GROUND],
        },
        f'!scenes.walls!{LIGHTHOUSE}.wallTwo000000001': {
            '_id': 'wallTwo000000001',
            'c': [0, 100, 100, 100],
            'levels': [],
        },
        f'!scenes.walls!{LIGHTHOUSE}.wallThree0000001': {
            '_id': 'wallThree0000001',
            'c': [0, 200, 100, 200],
            'levels': [LAMP_DUP],
        },
        f'!scenes.tiles!{LIGHTHOUSE}.tileLamp00000001': {
            '_id': 'tileLamp00000001',
            'texture': {'src': 'maps/lamp.webp'},
            'levels': [LAMP, LAMP_DUP],
        },
        f'!scenes.tokens!{LIGHTHOUSE}.{TOKEN_BANDIT}': {
            '_id': TOKEN_BANDIT,
            'name': 'Bandit',
            'actorId': BANDIT,
            'disposition': -1,
            'level': GROUND_DUP,
        },
        f'!scenes.tokens!{LIGHTHOUSE}.{TOKEN_WOLF}': {
            '_id': TOKEN_WOLF,
            'name': 'Shadow Wolf',
            'actorId': WOLF,
            'disposition': 0,
            'level': GROUND,
        },
        f'!scenes.tokens.delta.items!{LIGHTHOUSE}.{TOKEN_BANDIT}.deltaDagger00001': {
            '_id': 'deltaDagger00001',
            'name': 'Dagger',
            'type': 'weapon',
            'flags': {'dnd5e': {'sourceId': '.dag5ger0000000001'}},
            'system': {},
        },
        f'!scenes.regions!{LIGHTHOUSE}.regionStair00001': {
            '_id': 'regionStair00001',
            'name': 'Stair',
            'levels': [GROUND_DUP, LAMP_DUP],
            'behaviors': ['behaviorStair001'],
        },
        f'!scenes.regions.behaviors!{LIGHTHOUSE}.regionStair00001.behaviorStair001': {
            '_id': 'behaviorStair001',
            'type': 'changeLevel',
            'system': {'destination': LAMP_DUP},
        },
    }
    scenes.update(placed)
    tower = [
        _level(TOWER[0], 'Watchtower - Level (0|20)', 0, 20),
        _level(TOWER[1], 'Watchtower - Level (0|50)', 0, 50),
        _level(TOWER[2], 'Watchtower - Level (0|100)', 0, 100),
        _level(TOWER[3], 'Watchtower - Level (60|80)', 60, 80),
    ]
    add_scene(_scene(WATCHTOWER, 'Watchtower', list(TOWER)), tower)
    for number, level in enumerate((TOWER[0], TOWER[0], TOWER[2]), 1):
        scenes[f'!scenes.tokens!{WATCHTOWER}.tokenTower00000{number}'] = {
            '_id': f'tokenTower00000{number}',
            'name': 'Guard',
            'actorId': BANDIT,
            'disposition': -1,
            'level': level,
        }
    add_scene(
        _scene(RUINS, 'Ruins', [RUINS_DAY, RUINS_NIGHT]),
        [
            _level(RUINS_DAY, 'Ruins - Level (0|10)', 0, 10, background='maps/ruins-day.webp'),
            _level(RUINS_NIGHT, 'Ruins - Level (0|12)', 0, 12, background='maps/ruins-night.webp'),
        ],
    )
    add_scene(
        _scene(AIRSHIP, 'Airship', [DECK, HOLD], [('0', '99', 'Deck'), ('100', '109', 'Hold')]),
        [_level(DECK, 'Airship - Level (0|0)', 0, 0), _level(HOLD, 'Hold', 100, 109)],
    )
    add_scene(_scene(MARKET, 'Market', ['lvlMarket0000001']), [])
    scenes[f'!scenes.levels!{MARKET}.lvlMarket0000001'] = _level(
        'lvlMarket0000001', 'Market', 0, 20
    )

    def abilities(**scores):
        return {
            key: {'value': scores.get(key, 10)}
            for key in ('str', 'dex', 'con', 'int', 'wis', 'cha')
        }

    actors = {
        f'!actors!{HERO}': {
            '_id': HERO,
            'name': 'Aria',
            'type': 'character',
            'folder': PARTY,
            'system': {
                'abilities': abilities(str=16, dex=12, wis=14),
                'attributes': {
                    'hp': {'value': 40, 'max': 40},
                    'ac': {'flat': 16},
                    'spellcasting': 'wis',
                },
            },
        },
        f'!actors!{SUMMONER}': {
            '_id': SUMMONER,
            'name': 'Bram',
            'type': 'character',
            'folder': PARTY,
            'system': {
                'abilities': abilities(int=16),
                'attributes': {'hp': {'value': 30, 'max': 30}, 'spellcasting': 'int'},
            },
        },
        f'!actors!{WOLF}': {
            '_id': WOLF,
            'name': 'Shadow Wolf',
            'type': 'npc',
            'folder': FOES,
            'system': {
                'abilities': abilities(),
                'details': {'cr': 1},
                'attributes': {'hp': {'max': 20}},
            },
        },
        f'!actors!{DIRE_WOLF}': {
            '_id': DIRE_WOLF,
            'name': 'Dire Shadow Wolf',
            'type': 'npc',
            'folder': FOES,
            'system': {
                'abilities': abilities(),
                'details': {'cr': 2},
                'attributes': {'hp': {'max': 30}},
            },
        },
        f'!actors!{BANDIT}': {
            '_id': BANDIT,
            'name': 'Bandit',
            'type': 'npc',
            'folder': FOES,
            'system': {
                'abilities': abilities(str=12, dex=12),
                'details': {'cr': 0.125},
                'attributes': {'hp': {'value': 11, 'max': 11}, 'ac': {'flat': 12}},
            },
        },
    }

    def actor_item(actor, item_id, document):
        actors[f'!actors.items!{actor}.{item_id}'] = {'_id': item_id, **document}

    actor_item(
        HERO, 'itemClass0000001', {'name': 'Cleric', 'type': 'class', 'system': {'levels': 5}}
    )
    actor_item(
        HERO,
        'itemSword0000001',
        {
            'name': 'Longsword',
            'type': 'weapon',
            'flags': {'dnd5e': {'sourceId': '.swordCopy0000001'}},
            'system': {
                'type': {'value': 'martialM'},
                'damage': {'base': {'number': 1, 'denomination': 8}},
                'activities': _attack('attackSword00001'),
            },
        },
    )
    actor_item(
        HERO,
        'itemGuardian0001',
        {
            'name': 'Spirit Guardians',
            'type': 'spell',
            'flags': {'dnd5e': {'sourceId': '.guardianCopy001'}},
            '_stats': {'compendiumSource': 'Compendium.dnd5e.spells.Item.guardianSpell001'},
            'system': {
                'level': 5,
                'activities': {
                    'saveGuardian0001': {
                        '_id': 'saveGuardian0001',
                        'type': 'save',
                        'damage': {
                            'parts': [
                                {
                                    'custom': {
                                        'enabled': True,
                                        'formula': '(floor(@item.level / 2))d8',
                                    }
                                }
                            ]
                        },
                    },
                    'healGuardian0001': {
                        '_id': 'healGuardian0001',
                        'type': 'heal',
                        'healing': {'number': 1, 'denomination': 4, 'bonus': '@mod'},
                    },
                },
            },
        },
    )
    actor_item(
        SUMMONER,
        'itemSummon000001',
        {
            'name': 'Summon Shade',
            'type': 'spell',
            'system': {
                'activities': {
                    'summonShade00001': {
                        '_id': 'summonShade00001',
                        'type': 'summon',
                        'name': 'Summon',
                        'profiles': [
                            {
                                '_id': 'profileShade0001',
                                'name': 'Alpha',
                                'uuid': '@creature[shadow wolf (alpha)|xyz]',
                            },
                            {'_id': 'profileGood00001', 'name': 'Pup', 'uuid': f'Actor.{WOLF}'},
                        ],
                    }
                }
            },
        },
    )
    actor_item(
        SUMMONER,
        'itemWand00000001',
        {
            'name': 'Wand of Sparks',
            'type': 'equipment',
            'system': {
                'activities': {
                    'castMissing00001': {
                        '_id': 'castMissing00001',
                        'type': 'cast',
                        'name': 'Spark',
                        'spell': {'uuid': 'Compendium.dnd5e.spells.Item.spellMissing0001'},
                    },
                    'castPresent00001': {
                        '_id': 'castPresent00001',
                        'type': 'cast',
                        'name': 'Glow',
                        'spell': {'uuid': f'Compendium.dnd5e.spells.Item.{PACK_SPELL}'},
                    },
                    'castWorld0000001': {
                        '_id': 'castWorld0000001',
                        'type': 'cast',
                        'name': 'Light',
                        'spell': {'uuid': f'Item.{SPELL}'},
                    },
                }
            },
        },
    )
    actor_item(
        BANDIT,
        'itemClub00000001',
        {
            'name': 'Club',
            'type': 'weapon',
            'system': {
                'actionType': 'mwak',
                'damage': {
                    'parts': [['1d4 + @mod', 'bludgeoning']],
                    'versatile': '[object Object]',
                },
            },
        },
    )
    actor_item(
        BANDIT,
        'itemPotion000001',
        {
            'name': 'Smoke Bomb',
            'type': 'consumable',
            'system': {
                'activities': {
                    'usePotion0000001': {
                        '_id': 'usePotion0000001',
                        'type': 'utility',
                        'name': 'Throw',
                        'consumption': {
                            'targets': [{'type': 'itemUses', 'target': 'missingItem00001'}]
                        },
                    }
                }
            },
        },
    )
    actor_item(
        BANDIT,
        'itemRage00000001',
        {
            'name': 'Fury',
            'type': 'feat',
            'system': {
                'description': {'value': '[object Object]'},
                'uses': {'max': ''},
                'activities': {
                    'useRage00000001': {
                        '_id': 'useRage00000001',
                        'type': 'utility',
                        'consumption': {'targets': [{'type': 'itemUses', 'target': ''}]},
                    }
                },
            },
        },
    )
    items = {
        f'!items!{SPELL}': {'_id': SPELL, 'name': 'Light', 'type': 'spell', 'system': {}},
        '!items!worldMace0000001': {
            '_id': 'worldMace0000001',
            'name': 'Mace +1',
            'type': 'weapon',
            'system': {
                'damage': {'versatile': {'custom': {'enabled': True, 'formula': '[object Object]'}}}
            },
        },
        '!items!worldWand0000001': {
            '_id': 'worldWand0000001',
            'name': 'Wand of Frost',
            'type': 'equipment',
            'system': {'uses': {'max': '7', 'recovery': '[object Object]'}},
        },
        '!items!worldCharm000001': {
            '_id': 'worldCharm000001',
            'name': 'Charm',
            'type': 'equipment',
            'system': {
                'uses': {
                    'max': '3',
                    'recovery': [
                        {'period': 'dawn', 'type': 'formula', 'formula': '[object Object]'},
                        {'period': 'lr', 'type': 'recoverAll'},
                    ],
                }
            },
        },
        '!items!worldWard0000001': {
            '_id': 'worldWard0000001',
            'name': 'Ward Stone',
            'type': 'equipment',
            'system': {
                'activities': {
                    'useWard00000001': {
                        '_id': 'useWard00000001',
                        'type': 'utility',
                        'name': 'Ward',
                        'consumption': {
                            'targets': [{'type': 'itemUses', 'target': '[object Object]'}]
                        },
                    }
                }
            },
        },
    }
    macros = {
        '!macros!macroEffect00001': {
            '_id': 'macroEffect00001',
            'name': 'Mark Prey',
            'type': 'script',
            'command': '\n'.join(
                [
                    '// Marks the target.',
                    'const effectData = {',
                    '  label: `Marked (${target.name})`,',
                    '  icon: "icons/marked.webp", // shown on the token',
                    '  duration: { rounds: 1 },',
                    '};',
                    'await actor.createEmbeddedDocuments("ActiveEffect", [effectData]);',
                    'new Dialog({ buttons: { ok: { label: "Done", icon: "<i></i>" } } }).render(true);',
                ]
            ),
        },
        '!macros!macroBoth0000001': {
            '_id': 'macroBoth0000001',
            'name': 'Bless Ally',
            'type': 'script',
            'command': (
                'ActiveEffect.create({ name: "Blessed", label: "Blessed", icon: "icons/bless.webp", '
                "changes: [], 'origin': actor.uuid }, { parent: actor });"
            ),
        },
        '!macros!macroChat0000001': {
            '_id': 'macroChat0000001',
            'name': 'Say Hello',
            'type': 'chat',
            'command': 'label: hello, changes: none, effect',
        },
    }
    folders = {
        f'!folders!{PARTY}': {'_id': PARTY, 'name': 'Heroes', 'type': 'Actor'},
        f'!folders!{FOES}': {'_id': FOES, 'name': 'Foes', 'type': 'Actor'},
        '!folders!folderScenes0001': {'_id': 'folderScenes0001', 'name': 'Maps', 'type': 'Scene'},
    }
    return {
        'scenes': scenes,
        'actors': actors,
        'items': items,
        'macros': macros,
        'folders': folders,
    }


def node():
    """The Node.js executable and the development classic-level, or None when either is missing."""
    executable = shutil.which('node')
    return (executable, CLASSIC_LEVEL) if executable and CLASSIC_LEVEL.is_dir() else None


def write(world, collections, classic=False):
    """Store each {key: document} collection as `world/data/<name>`, as Foundry does."""
    world = Path(world)
    world.mkdir(parents=True, exist_ok=True)
    (world / 'world.json').write_text(
        json.dumps(
            {'id': world.name, 'title': 'Synthetic', 'system': 'dnd5e', 'coreVersion': '14.369'}
        ),
        encoding='utf-8',
    )
    for name, rows in collections.items():
        folder = world / 'data' / name
        if not classic:
            batch = sorted(
                (key.encode(), json.dumps(document, separators=(',', ':')).encode())
                for key, document in rows.items()
            )
            leveldb_writer.database(folder, logs=[leveldb_writer.log([batch])])
            continue
        executable, library = node()
        script = (
            'const { ClassicLevel } = require(process.argv[1]);'
            "const rows = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));"
            "(async () => { const db = new ClassicLevel(process.argv[2], { valueEncoding: 'json' });"
            "await db.batch(Object.entries(rows).map(([key, value]) => ({ type: 'put', key, value })));"
            'await db.close(); })();'
        )
        folder.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [executable, '-e', script, str(library), str(folder)],
            input=json.dumps(rows),
            text=True,
            encoding='utf-8',
            check=True,
            capture_output=True,
        )
    return world


def pack(user_data, ids):
    """A dnd5e spells compendium holding the given item IDs."""
    folder = Path(user_data) / 'Data' / 'systems' / 'dnd5e' / 'packs' / 'spells'
    batch = sorted(
        (f'!items!{item}'.encode(), json.dumps({'_id': item, 'name': item}).encode())
        for item in ids
    )
    leveldb_writer.database(folder, logs=[leveldb_writer.log([batch])])
    return folder
