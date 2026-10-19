"""Post-migration repairs on a synthetic v14 world: findings, planned changes, writes and verification."""

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
sys.path.insert(0, str(ROOT / 'tests'))

import foundry_leveldb_write
import foundry_repair as repair
import foundry_world_fixture as fixture
from foundry_world_fixture import (
    GROUND,
    GROUND_DUP,
    LAMP,
    LAMP_DUP,
    LIGHTHOUSE,
    TOWER,
    WATCHTOWER,
)


def stored(collections):
    """Documents shaped as `repair.load` returns them, without a database."""
    return {
        name: {
            key: (json.dumps(document, separators=(',', ':')).encode(), document)
            for key, document in rows.items()
        }
        for name, rows in collections.items()
    }


def applied(documents, changes):
    """The documents as a correct write leaves them."""
    result = copy.deepcopy(documents)
    for name, rows in changes.items():
        for key, value in rows.items():
            if value is None:
                result[name].pop(key)
            else:
                result[name][key] = (json.dumps(value, separators=(',', ':')).encode(), value)
    return result


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.documents = stored(fixture.documents())
        self.findings, self.changes = repair.plan(self.documents)
        self.scenes = {item['scene']: item for item in self.findings['floors']}

    def change(self, key):
        return self.changes['scenes'][key]

    def test_duplicates_of_intended_floors_merge_and_every_reference_moves(self):
        found = self.scenes['Lighthouse']
        self.assertEqual((found['before'], found['after']), (4, 2))
        self.assertEqual(found['removed'], sorted([GROUND_DUP, LAMP_DUP]))
        self.assertEqual(
            found['changes'],
            [
                'background of "Lighthouse - Level (0|15)" moved to "Ground"',
                'foreground of "Lighthouse - Level (10|30)" moved to "Lighthouse - Level (10|19)"',
                'merge "Lighthouse - Level (0|15)" (0-15) into "Ground"',
                'merge "Lighthouse - Level (10|30)" (10-30) into "Lighthouse - Level (10|19)"',
            ],
        )
        # The scene, two kept floors' visibility lists and six placed documents.
        self.assertEqual(found['documents_updated'], 9)
        level = f'!scenes.levels!{LIGHTHOUSE}.'
        self.assertIsNone(self.change(level + GROUND_DUP))
        self.assertIsNone(self.change(level + LAMP_DUP))
        ground = self.change(level + GROUND)
        self.assertEqual(ground['background']['src'], 'maps/lighthouse.webp')
        self.assertEqual(ground['visibility']['levels'], ['defaultLevel0000', GROUND, LAMP])
        self.assertEqual(ground['elevation'], {'bottom': 0, 'top': 9})
        self.assertEqual(self.change(level + LAMP)['foreground']['src'], 'maps/lamp-roof.webp')
        scene = self.change(f'!scenes!{LIGHTHOUSE}')
        self.assertEqual((scene['levels'], scene['initialLevel']), ([GROUND, LAMP], GROUND))
        placed = f'!scenes.%s!{LIGHTHOUSE}.%s'
        self.assertEqual(self.change(placed % ('walls', 'wallOne000000001'))['levels'], [GROUND])
        self.assertEqual(self.change(placed % ('walls', 'wallThree0000001'))['levels'], [LAMP])
        self.assertEqual(self.change(placed % ('tiles', 'tileLamp00000001'))['levels'], [LAMP])
        self.assertEqual(self.change(placed % ('tokens', 'tokenBandit00001'))['level'], GROUND)
        self.assertEqual(
            self.change(placed % ('regions', 'regionStair00001'))['levels'], [GROUND, LAMP]
        )
        behavior = placed % ('regions.behaviors', 'regionStair00001.behaviorStair001')
        self.assertEqual(self.change(behavior)['system'], {'destination': LAMP})
        # A wall on every floor, and a token already on a kept floor, are left alone.
        self.assertNotIn(placed % ('walls', 'wallTwo000000001'), self.changes['scenes'])
        self.assertNotIn(placed % ('tokens', 'tokenWolf0000001'), self.changes['scenes'])

    def test_automatic_floors_at_one_base_merge_without_reaching_the_next_floor(self):
        found = self.scenes['Watchtower']
        self.assertEqual((found['before'], found['after']), (4, 2))
        self.assertEqual(found['changes'][0], '"Watchtower - Level (0|20)" top 20 -> 59')
        # The floor with most tokens is kept and grows to just below the floor at 60, not to 100.
        kept = self.change(f'!scenes.levels!{WATCHTOWER}.{TOWER[0]}')
        self.assertEqual(kept['elevation'], {'bottom': 0, 'top': 59})
        self.assertNotIn(f'!scenes.levels!{WATCHTOWER}.{TOWER[3]}', self.changes['scenes'])
        token = self.change(f'!scenes.tokens!{WATCHTOWER}.tokenTower000003')
        self.assertEqual(token['level'], TOWER[0])

    def test_floors_with_different_images_are_kept_and_reported(self):
        found = self.scenes['Ruins']
        self.assertEqual((found['before'], found['after'], found['removed']), (2, 2, []))
        self.assertEqual(
            found['kept_back'],
            [
                '"Ruins - Level (0|12)" was not merged: its background differs from '
                '"Ruins - Level (0|10)"'
            ],
        )
        self.assertFalse(
            any(key.startswith('!scenes.levels!sceneClash') for key in self.changes['scenes'])
        )

    def test_a_lone_automatic_floor_takes_the_name_the_gm_gave_it(self):
        self.assertEqual(
            self.scenes['Airship']['changes'], ['rename "Airship - Level (0|0)" -> "Deck"']
        )
        self.assertEqual(
            self.change('!scenes.levels!sceneRename00001.lvlDeck000000001')['name'], 'Deck'
        )
        self.assertNotIn('Market', self.scenes)

    def test_relative_source_ids_go_unless_a_compendium_source_stands_in(self):
        self.assertEqual(
            self.findings['source_ids'], ['Aria: Longsword', 'Lighthouse › Bandit: Dagger']
        )
        sword = self.changes['actors']['!actors.items!actorHero0000001.itemSword0000001']
        self.assertNotIn('sourceId', sword['flags']['dnd5e'])
        self.assertNotIn(
            '!actors.items!actorHero0000001.itemGuardian0001', self.changes.get('actors', {})
        )

    def test_object_object_fields_are_cleared_in_old_and_new_shapes(self):
        self.assertCountEqual(
            [(item['item'], item['field']) for item in self.findings['formulas']],
            [
                ('Bandit: Club', 'versatile damage'),
                ('world: Mace +1', 'versatile damage'),
                ('world: Wand of Frost', 'recharge'),
                ('world: Charm', 'recharge'),
                ('world: Ward Stone', 'Ward consumption target'),
            ],
        )
        items = self.changes['items']
        self.assertEqual(
            items['!items!worldMace0000001']['system']['damage']['versatile']['custom'],
            {'enabled': False, 'formula': ''},
        )
        self.assertEqual(items['!items!worldWand0000001']['system']['uses']['recovery'], '')
        self.assertEqual(
            items['!items!worldCharm000001']['system']['uses']['recovery'],
            [{'period': 'lr', 'type': 'recoverAll'}],
        )
        ward = items['!items!worldWard0000001']['system']['activities']['useWard00000001']
        self.assertEqual(ward['consumption']['targets'][0]['target'], '')
        club = self.changes['actors']['!actors.items!actorBandit00001.itemClub00000001']
        self.assertEqual(club['system']['damage']['versatile'], '')
        # Elsewhere the value is reported for the GM, not guessed at.
        self.assertEqual(
            self.findings['other_fields'],
            [{'item': 'Bandit: Fury', 'field': 'system.description.value'}],
        )

    def test_summon_links_are_reported_with_world_actors_and_changed_only_when_chosen(self):
        [found] = self.findings['summons']
        self.assertEqual(found['uuid'], '@creature[shadow wolf (alpha)|xyz]')
        self.assertEqual(
            [actor['name'] for actor in found['candidates']], ['Shadow Wolf', 'Dire Shadow Wolf']
        )
        key = '!actors.items!actorSummoner001.itemSummon000001'
        self.assertNotIn(key, self.changes.get('actors', {}))
        _, chosen = repair.plan(
            self.documents, {'summons': {found['id']: 'Actor.actorDireWolf001'}}
        )
        profiles = chosen['actors'][key]['system']['activities']['summonShade00001']['profiles']
        self.assertEqual(
            [profile['uuid'] for profile in profiles],
            ['Actor.actorDireWolf001', 'Actor.actorWolf0000001'],
        )
        for choices in (
            {'summons': {found['id']: 'Actor.notInThisWorld'}},
            {'summons': {'unknown|summon|profile': 'Actor.actorWolf0000001'}},
            {'macros': ['!macros!macroChat0000001']},
        ):
            with self.subTest(choices=choices), self.assertRaises(ValueError):
                repair.plan(self.documents, choices)

    def test_macros_creating_effects_with_label_or_icon_are_rewritten_only_when_chosen(self):
        macros = {item['name']: item for item in self.findings['macros']}
        self.assertEqual(set(macros), {'Mark Prey', 'Bless Ally'})
        self.assertEqual(
            [(use['line'], use['field'], use['rewrite']) for use in macros['Mark Prey']['uses']],
            [(3, 'label', True), (4, 'icon', True)],
        )
        # This effect already has a name, so only its icon can be renamed.
        self.assertEqual(
            [(use['field'], use['rewrite']) for use in macros['Bless Ally']['uses']],
            [('label', False), ('icon', True)],
        )
        self.assertNotIn('macros', self.changes)
        _, chosen = repair.plan(
            self.documents, {'macros': ['!macros!macroEffect00001', '!macros!macroBoth0000001']}
        )
        command = chosen['macros']['!macros!macroEffect00001']['command']
        self.assertIn('name: `Marked (${target.name})`,', command)
        self.assertIn('img: "icons/marked.webp"', command)
        self.assertIn('{ ok: { label: "Done", icon: "<i></i>" } }', command)  # a dialog button
        both = chosen['macros']['!macros!macroBoth0000001']['command']
        self.assertIn('name: "Blessed", label: "Blessed", img: "icons/bless.webp"', both)

    def test_operations_name_the_bytes_each_change_was_planned_from(self):
        batches = dict(repair.operations(self.documents, self.changes))
        self.assertEqual(set(batches), {'actors', 'items', 'scenes'})
        op = next(
            op for op in batches['scenes'] if op['key'] == f'!scenes.levels!{LIGHTHOUSE}.{LAMP_DUP}'
        )
        expected = hashlib.sha256(self.documents['scenes'][op['key']][0]).hexdigest()
        self.assertEqual((op['type'], op['expected_sha256']), ('del', expected))

    def test_a_repaired_world_passes_verification_and_has_nothing_left(self):
        after = applied(self.documents, self.changes)
        result = repair.verify(self.documents, after, self.changes)
        self.assertTrue(result['ok'], result)
        self.assertIn(
            'Scene floors 13 → 9 (4 merged); every other count unchanged',
            result['checks'][1]['detail'],
        )
        self.assertEqual(
            repair.pending(repair.plan(after)[0]), dict.fromkeys(repair.pending(self.findings), 0)
        )

    def test_verification_catches_each_kind_of_damage(self):
        good = applied(self.documents, self.changes)
        wall = f'!scenes.walls!{LIGHTHOUSE}.wallOne000000001'
        level = f'!scenes.levels!{LIGHTHOUSE}.{GROUND}'
        cases = {
            'Only planned documents changed': lambda world: world['scenes'].update(
                {wall: (b'{}', {'_id': 'wallOne000000001', 'levels': [GROUND]})}
            ),
            'No reference to a merged floor': lambda world: world['scenes'][
                f'!scenes.walls!{LIGHTHOUSE}.wallTwo000000001'
            ][1].update(levels=[LAMP_DUP]),
            'Nothing lost all its floors': lambda world: world['scenes'][
                f'!scenes.walls!{LIGHTHOUSE}.wallThree0000001'
            ][1].update(levels=[]),
            'Every floor image kept': lambda world: world['scenes'][level][1]['background'].update(
                src=None
            ),
            'Document counts change only for merged floors': lambda world: world['items'].pop(
                '!items!worldSpell000001'
            ),
        }
        for name, damage in cases.items():
            with self.subTest(name):
                world = copy.deepcopy(good)
                damage(world)
                checks = {
                    item['name']: item['ok']
                    for item in repair.verify(self.documents, world, self.changes)['checks']
                }
                self.assertFalse(checks[name], checks)
        # A world the repair never reached still has everything to repair.
        result = repair.verify(self.documents, copy.deepcopy(self.documents), self.changes)
        self.assertFalse(result['ok'])
        self.assertEqual(
            result['checks'][-1]['detail'],
            [
                '3 scenes with floors to merge or rename left',
                '2 relative source IDs left',
                '5 "[object Object]" fields left',
            ],
        )


class LoadTests(unittest.TestCase):
    def test_load_reads_every_collection_the_repair_needs(self):
        with tempfile.TemporaryDirectory(prefix='repair-load-') as folder:
            world = fixture.write(Path(folder) / 'world', fixture.documents())
            documents = repair.load(world)
            self.assertEqual(set(documents), set(repair.COLLECTIONS))
            self.assertEqual(documents['items']['!items!worldSpell000001'][1]['name'], 'Light')
            self.assertEqual(repair.fingerprint(documents), repair.fingerprint(repair.load(world)))
            self.assertEqual(repair.load(world, ('cards',)), {'cards': {}})


@unittest.skipUnless(
    fixture.node(), 'Node.js or the classic-level development dependency is missing'
)
class WriterTests(unittest.TestCase):
    """The write path through classic-level, the library Foundry bundles, on a closed world."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='repair-writer-')
        self.addCleanup(self.temp.cleanup)
        self.world = fixture.write(
            Path(self.temp.name) / 'world', fixture.documents(), classic=True
        )
        self.install = {'root': Path(self.temp.name), 'library': fixture.CLASSIC_LEVEL}

    def test_planned_changes_are_written_and_read_back_by_studios_reader(self):
        before = repair.load(self.world)
        findings, changes = repair.plan(before)
        batches = repair.operations(before, changes)
        result = foundry_leveldb_write.write(
            [(self.world / 'data' / name, ops) for name, ops in batches], self.install
        )
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(result['committed']), 3)
        after = repair.load(self.world)
        self.assertTrue(repair.verify(before, after, changes)['ok'])
        self.assertEqual(sum(repair.pending(repair.plan(after)[0]).values()), 0)

    def test_a_document_changed_after_the_dry_run_stops_every_write(self):
        before = repair.load(self.world)
        _, changes = repair.plan(before)
        batches = repair.operations(before, changes)
        batches[0][1][0]['expected_sha256'] = '0' * 64
        result = foundry_leveldb_write.write(
            [(self.world / 'data' / name, ops) for name, ops in batches], self.install
        )
        self.assertFalse(result['ok'])
        self.assertEqual(result['committed'], [])
        self.assertIn('changed after the dry run', result['error'])
        self.assertEqual(repair.fingerprint(repair.load(self.world)), repair.fingerprint(before))

    def test_a_database_another_process_has_open_is_refused(self):
        executable, library = fixture.node()
        holder = subprocess.Popen(
            [
                executable,
                '-e',
                'const { ClassicLevel } = require(process.argv[1]);'
                'const db = new ClassicLevel(process.argv[2]);'
                "db.open().then(() => { console.log('open'); setTimeout(() => {}, 60000); });",
                str(library),
                str(self.world / 'data' / 'items'),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), 'open')
        before = repair.load(self.world)
        _, changes = repair.plan(before)
        batches = repair.operations(before, changes)
        result = foundry_leveldb_write.write(
            [(self.world / 'data' / name, ops) for name, ops in batches], self.install
        )
        self.assertFalse(result['ok'])
        self.assertEqual(result['committed'], [])
        self.assertEqual(repair.fingerprint(repair.load(self.world)), repair.fingerprint(before))


class InstallationTests(unittest.TestCase):
    def test_only_the_installation_of_the_target_build_with_its_library_is_accepted(self):
        with tempfile.TemporaryDirectory(prefix='repair-install-') as folder:
            desktop = Path(folder) / 'Foundry Virtual Tabletop'
            app = desktop / 'resources' / 'app'
            app.mkdir(parents=True)
            package = {'name': 'foundryvtt', 'release': {'generation': 14, 'build': 369}}
            (app / 'package.json').write_text(json.dumps(package), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'no bundled classic-level'):
                foundry_leveldb_write.installation(str(desktop), '14.369')
            (app / 'node_modules' / 'classic-level').mkdir(parents=True)
            (app / 'node_modules' / 'classic-level' / 'package.json').write_text('{}')
            found = foundry_leveldb_write.installation(str(desktop), '14.369')
            self.assertEqual((found['app'], found['build']), (app.resolve(), '14.369'))
            with self.assertRaisesRegex(ValueError, 'is Foundry 14.369, but the clone'):
                foundry_leveldb_write.installation(str(desktop), '13.351')
            with self.assertRaisesRegex(ValueError, 'Choose the folder Foundry VTT'):
                foundry_leveldb_write.installation(folder, '14.369')
            with self.assertRaisesRegex(ValueError, 'absolute'):
                foundry_leveldb_write.installation('relative/folder', '14.369')

    def test_a_desktop_executable_runs_scripts_only_when_its_run_as_node_fuse_is_on(self):
        with tempfile.TemporaryDirectory(prefix='repair-fuse-') as folder:
            root = Path(folder)
            executable = root / 'Foundry Virtual Tabletop.exe'
            sentinel = foundry_leveldb_write.FUSE_SENTINEL
            for wire, expected in (
                (b'\x01\x081011', executable),
                (b'\x01\x080011', None),
                (b'', None),
            ):
                with self.subTest(wire=wire):
                    executable.write_bytes(b'MZ' + b'\0' * 64 + sentinel + wire + b'\0' * 64)
                    self.assertEqual(foundry_leveldb_write._electron(root), expected)
            executable.write_bytes(b'')
            self.assertIsNone(foundry_leveldb_write._electron(root))


if __name__ == '__main__':
    unittest.main()
