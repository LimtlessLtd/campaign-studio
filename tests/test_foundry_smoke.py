"""The offline dice roller and the encounter smoke test on a synthetic v14 dnd5e world."""

import random
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
sys.path.insert(0, str(ROOT / 'tests'))

import foundry_smoke as smoke
import foundry_world_fixture as fixture
from foundry_roll import FormulaError, average, compile_formula, roll
from foundry_world_fixture import FOES, LIGHTHOUSE, PACK_SPELL, PARTY, WATCHTOWER


def rolls(formula, resolve=lambda _path: None, times=300):
    tree, _ = compile_formula(formula, resolve)
    rng = random.Random(4)
    return [roll(tree, rng) for _ in range(times)]


class RollTests(unittest.TestCase):
    def test_dice_arithmetic_and_functions(self):
        self.assertEqual(set(rolls('2d6 + 3')), set(range(5, 16)))
        self.assertEqual(rolls('max(1, 4 - 7) * 2 / 4 - -1', times=1), [1.5])
        self.assertEqual(set(rolls('d4')), {1, 2, 3, 4})
        self.assertEqual(set(rolls('1d%', times=2000)) <= set(range(1, 101)), True)
        self.assertGreater(min(rolls('1d4min3')), 2)
        self.assertEqual(rolls('round(2.5) + floor(-0.5) + ceil(0.2) + abs(-2)', times=1), [5])

    def test_a_calculated_dice_count_rolls_that_many_dice(self):
        level = {'item.level': 5}
        values = rolls('(floor(@item.level / 2))d8', level.get)
        self.assertEqual((min(values) >= 2, max(values) <= 16), (True, True))
        self.assertTrue(any(value > 8 for value in values))

    def test_keep_and_drop(self):
        self.assertGreater(sum(rolls('2d20kh')), sum(rolls('2d20kl')))
        dropped = rolls('4d6dl3')
        self.assertTrue(set(dropped) <= set(range(1, 7)) and sum(dropped) / len(dropped) > 4)

    def test_references_flavor_and_runtime_values(self):
        mean, unknown = average(
            '1d1[fire] + @mod + @scale.rogue.sneak-attack',
            {'mod': 3}.get,
            10,
            random.Random(1),
        )
        self.assertEqual((mean, unknown), (4, ['scale.rogue.sneak-attack']))
        self.assertEqual(average('', {}.get, 5, random.Random(1)), (0.0, []))

    def test_broken_formulas_say_what_is_wrong(self):
        cases = {
            '[object Object]': 'formula is "[object Object]"',
            '2d6 +': 'cannot parse "2d6 +"',
            'alert(1)': 'cannot parse "alert(1)"',
            '1d6 / 0': 'cannot evaluate "1d6 / 0"',
            '5000d6': 'cannot evaluate "5000d6"',
            '(2': 'cannot parse "(2"',
            'x' * 1001: 'cannot parse',
            '(' * 400 + '1' + ')' * 400: 'cannot',
        }
        for formula, message in cases.items():
            with (
                self.subTest(formula=formula[:20]),
                self.assertRaisesRegex(FormulaError, re.escape(message)),
            ):
                average(formula, {}.get, 3, random.Random(1))


class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='smoke-test-')
        cls.user_data = Path(cls.temp.name) / 'User Data'
        cls.world = fixture.write(
            cls.user_data / 'Data' / 'worlds' / 'fixture-world', fixture.documents()
        )
        fixture.pack(cls.user_data, [PACK_SPELL])
        cls.report = smoke.run(cls.world, cls.user_data, PARTY, [LIGHTHOUSE, WATCHTOWER], rolls=50)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_choices_list_actor_folders_and_scenes_with_their_npcs(self):
        choices = smoke.choices(smoke.load(self.world))
        self.assertEqual(
            [(item['name'], item['characters']) for item in choices['folders']],
            [('Heroes', 2), ('Foes', 0)],
        )
        scenes = {item['name']: item['npcs'] for item in choices['scenes']}
        self.assertEqual((scenes['Lighthouse'], scenes['Watchtower'], scenes['Market']), (2, 3, 0))

    def test_every_broken_link_and_field_is_a_problem(self):
        self.assertCountEqual(
            self.report['problems'],
            [
                'Bram › Summon Shade › Summon: summon "Alpha" -> '
                '@creature[shadow wolf (alpha)|xyz] not found',
                'Bram › Wand of Sparks › Spark: linked spell '
                'Compendium.dnd5e.spells.Item.spellMissing0001 not found',
                'Bandit › Club: has a corrupted "[object Object]" field',
                'Bandit › Fury: has a corrupted "[object Object]" field',
                'Bandit › Smoke Bomb › Throw: consumes uses of missing item missingItem00001',
                'Bandit › Fury: consumes its own uses but the item has none',
            ],
        )

    def test_party_actions_roll_and_the_best_attack_is_kept(self):
        party = {item['name']: item for item in self.report['party']}
        self.assertEqual(set(party), {'Aria', 'Bram'})
        aria = party['Aria']
        self.assertEqual(
            (aria['ac'], aria['hp'], aria['actions'], aria['failures']), (16, 40, 3, 0)
        )
        # Longsword: 1d8 + Strength 3, to hit 3 + proficiency 3 at level 5.
        self.assertEqual((aria['best']['name'], aria['best']['to_hit']), ('Longsword', 6))
        self.assertTrue(6 < aria['best']['average'] < 9)
        self.assertEqual(party['Bram']['failures'], 2)

    def test_each_scene_is_a_rough_encounter_labelled_as_no_balance_prediction(self):
        self.assertIn('not a balance prediction', self.report['note'])
        lighthouse, tower = self.report['encounters']
        self.assertEqual(
            (lighthouse['scene'], lighthouse['npcs'], lighthouse['enemies']), ('Lighthouse', 2, 1)
        )
        self.assertTrue(lighthouse['enemies_hostile_only'])
        # Two corrupted items and two actions consuming what is not there.
        self.assertEqual(lighthouse['failing_actions'], 4)
        self.assertEqual((tower['npcs'], tower['enemies']), (1, 3))
        # Three bandits (11 HP each) against the party's best attacks.
        self.assertGreaterEqual(tower['rounds_for_party_to_win'], 2)
        self.assertGreater(tower['enemy_damage_per_round'], 0)

    def test_an_actor_without_system_data_is_tested_with_defaults(self):
        bare = {'_id': 'actorBare0000001', 'name': 'Bare', 'type': 'npc', 'system': None}
        documents = {'actors': {'!actors!actorBare0000001': (b'', bare)}}
        world = smoke._World({**dict.fromkeys(smoke.COLLECTIONS, {}), **documents}, '.', '.')
        result = smoke._test(world, world.actors['actorBare0000001'], 1, random.Random(1), [])
        self.assertEqual((result['ac'], result['hp'], result['actions']), (10, 1, 0))

    def test_a_compendium_link_is_looked_up_only_in_this_user_data(self):
        world = smoke._World(dict.fromkeys(smoke.COLLECTIONS, {}), self.world, self.user_data)
        self.assertTrue(world.exists('Compendium.dnd5e.spells.Item.' + PACK_SPELL))
        self.assertFalse(world.exists('Compendium.dnd5e.spells.Item.spellMissing0001'))
        # An absolute folder in place of the package ID (its path must not hold a dot, which splits).
        outside = Path(self.temp.name) / 'outside'
        fixture.pack(outside, ['spellOutside0001'])
        absolute = str(outside / 'Data' / 'systems' / 'dnd5e')
        self.assertNotIn('.', absolute)
        for scope in (absolute, '', 'dnd5e/sub'):
            with self.subTest(scope=scope):
                self.assertIsNone(world.exists(f'Compendium.{scope}.spells.Item.spellOutside0001'))

    def test_choices_must_belong_to_the_world(self):
        for folder, scenes, message in (
            ('folderScenes0001', [LIGHTHOUSE], 'actor folder'),
            (FOES, [LIGHTHOUSE], 'no player characters'),
            (PARTY, [], 'one or more scenes'),
            (PARTY, ['sceneUnknown0001'], 'one or more scenes'),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                smoke.run(self.world, self.user_data, folder, scenes)


if __name__ == '__main__':
    unittest.main()
