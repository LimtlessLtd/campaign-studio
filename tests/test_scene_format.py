import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'DM'))

import scene_format  # noqa: E402


class ReadAloudTests(unittest.TestCase):
    def test_counts_sentences(self):
        for text, count in {
            '': 0,
            '   ': 0,
            'The tide rises.': 1,
            'The tide rises': 1,
            'The tide rises. A bell tolls.': 2,
            'Who goes there? Stand and be known!': 2,
            'The door creaks... A voice answers.': 2,
            'The tide rises, a bell tolls; the gulls scatter.': 1,
            # Quoted speech and abbreviations do not end the sentence.
            '"Halt!" the guard shouts, lifting her lantern.': 1,
            'Dr. Vane waves from the pier of St. Brin.': 1,
            'J. Rook grins, then waves you on.': 1,
            'She whispers, "Go now." The door shuts.': 2,
            'Line one\nline two of the same sentence.': 1,
        }.items():
            with self.subTest(text):
                self.assertEqual(scene_format.sentence_count(text), count)

    def test_read_aloud_is_one_short_sentence(self):
        ok = 'A watcher steps out of the gatehouse and raises a lantern.'
        self.assertEqual(scene_format.read_aloud_problem(ok), '')
        self.assertIn('needs one sentence', scene_format.read_aloud_problem(' '))
        self.assertIn('one sentence', scene_format.read_aloud_problem('It rains. It pours.'))
        long = ' '.join(['word'] * (scene_format.READ_ALOUD_WORDS + 1)) + '.'
        self.assertIn('at most 50 words', scene_format.read_aloud_problem(long))
        short = ' '.join(['word'] * scene_format.READ_ALOUD_WORDS) + '.'
        self.assertEqual(scene_format.read_aloud_problem(short), '')

    def test_layout_problems_name_what_a_stored_scene_lacks(self):
        self.assertEqual(
            scene_format.layout_problems({}),
            ['no key points', 'no sentence to read aloud', 'no checks'],
        )
        self.assertEqual(
            scene_format.layout_problems(
                {
                    'key_points': ['Rook wants quiet.'],
                    'read_aloud': 'It rains. It pours.',
                    'checks': [{'skill': 'Insight', 'dc': 10, 'result': 'Rook is afraid.'}],
                }
            ),
            ['read-aloud must be one sentence'],
        )
        self.assertEqual(
            scene_format.layout_problems(
                {
                    'key_points': ['Rook wants quiet.'],
                    'read_aloud': 'It rains.',
                    'checks': [{'skill': 'Insight', 'dc': 10, 'result': 'Rook is afraid.'}],
                }
            ),
            [],
        )

    def test_staged_drafts_without_the_layout_are_flagged_not_changed(self):
        old = {'id': 'a', 'read_aloud': 'One. Two.'}
        new = {'id': 'b', 'key_points': [], 'checks': [], 'read_aloud': ''}
        draft = {'scenes': [old, new]}
        flags = scene_format.staged_flags(draft)
        self.assertEqual(flags, [True, False])
        self.assertEqual(draft, {'scenes': [old, new]})
        view = scene_format.schema_view(draft, flags)
        self.assertEqual(view['scenes'][0], {**old, 'key_points': [], 'checks': []})
        self.assertNotIn('key_points', draft['scenes'][0])


if __name__ == '__main__':
    unittest.main()
