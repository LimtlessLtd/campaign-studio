"""2014 5e encounter estimates from a synthetic party and creature CRs."""

import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'DM'))

import encounter_budget


def party(*levels):
    return [{'level': level} for level in levels]


class EncounterBudgetTests(unittest.TestCase):
    def test_supported_challenge_ratings_and_group_multipliers(self):
        self.assertEqual(encounter_budget.parse_cr('1/4'), Fraction(1, 4))
        self.assertEqual(encounter_budget.parse_cr('0.5'), Fraction(1, 2))
        for bad in ('', '1/3', '31', '-1', '1/0', 2):
            self.assertIsNone(encounter_budget.parse_cr(bad))
        self.assertEqual(encounter_budget.multiplier(4, 4), 2)
        self.assertEqual(encounter_budget.multiplier(4, 2), Fraction(5, 2))
        self.assertEqual(encounter_budget.multiplier(1, 6), Fraction(1, 2))
        self.assertEqual(encounter_budget.multiplier(15, 2), 5)

    def test_rating_sums_mixed_character_levels_instead_of_averaging(self):
        result = encounter_budget.rate(
            [
                {'name': 'Bugbear', 'count': 1, 'cr': '1'},
                {'name': 'Hobgoblin', 'count': 3, 'cr': '1/2'},
            ],
            party(3, 3, 3, 2),
        )
        self.assertEqual(
            result['thresholds'], {'easy': 275, 'medium': 550, 'hard': 825, 'deadly': 1400}
        )
        self.assertEqual(
            (result['xp'], result['adjusted_xp'], result['rating']), (500, 1000, 'hard')
        )
        self.assertEqual(result['party_size'], 4)

    def test_unknown_creature_or_party_never_gets_a_misleading_rating(self):
        goblin = {'name': 'Goblin', 'count': 4, 'cr': '1/4'}
        unknown = {'name': 'Unknown', 'count': 1, 'cr': ''}
        self.assertEqual(encounter_budget.rate([goblin], [])['status'], 'no_party')
        self.assertEqual(
            encounter_budget.rate([goblin], party(3) + [{'name': 'Unknown'}])['status'], 'no_party'
        )
        self.assertEqual(encounter_budget.rate([], party(3))['status'], 'no_creatures')
        partial = encounter_budget.rate([goblin, unknown], party(3, 3, 3, 3))
        self.assertEqual(
            (partial['status'], partial['rating'], partial['unrated']),
            ('missing_cr', None, ['Unknown']),
        )
        complete = encounter_budget.rate([goblin], party(3, 3, 3, 3))
        self.assertEqual(
            (complete['status'], complete['rating'], complete['adjusted_xp']),
            ('rated', 'easy', 400),
        )


if __name__ == '__main__':
    unittest.main()
