"""Stored record shapes are defined once, and every writer builds records from them."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import schema
import shapes

BROWSER = sorted((ROOT / 'DM/app').glob('*.js'))


class ShapeTests(unittest.TestCase):
    def test_a_shape_change_comes_with_a_migration(self):
        self.assertEqual(
            shapes.fields_digest(),
            schema.SHAPES_DIGEST,
            'A stored shape changed. Add schema version CURRENT + 1 whose migration is fill_campaign, '
            'so stored records gain the new fields, then set SHAPES_DIGEST to shapes.fields_digest().',
        )

    def test_new_records_are_complete_and_independent(self):
        first = shapes.CODEX_ENTRY.new(id='a', type='npc', name='A', map='harbour')
        second = shapes.CODEX_ENTRY.new(id='b', type='npc', name='B')
        first['tags'].append('harbour')
        self.assertEqual(second['tags'], [])
        self.assertEqual(list(first)[:3], ['id', 'type', 'name'])
        self.assertEqual(list(first)[-1], 'map')
        self.assertEqual(shapes.CODEX_ENTRY.problems(first), [])
        with self.assertRaisesRegex(TypeError, 'needs type, name'):
            shapes.CODEX_ENTRY.new(id='c')

    def test_fill_completes_records_without_changing_their_values(self):
        area = {'n': 1, 'name': 'Pier', 'kind': 'dock', 'at': [0, 0], 'text': 'Kept', 'custom': 1}
        area['journal'] = [{'id': 'j1', 'title': 'Tide table'}]
        key = {'map': 'Harbour', 'areas': [area, 'not a record'], 'events': None}

        shapes.MAP_KEY.fill_all(key, 'key.json')

        self.assertEqual((area['text'], area['custom'], area['rooms']), ('Kept', 1, []))
        self.assertEqual(
            area['journal'][0], {'id': 'j1', 'title': 'Tide table', 'text': '', 'secrets': ''}
        )
        self.assertEqual(
            (key['events'], key['areas'][1], key['stocked']), ([], 'not a record', False)
        )
        self.assertEqual(shapes.MAP_KEY.problems(dict(key, areas=[area])), [])
        with self.assertRaisesRegex(shapes.ShapeError, 'key.json: areas must be a list'):
            shapes.MAP_KEY.fill_all({'areas': {}}, 'key.json')

    def test_problems_name_missing_and_mistyped_fields(self):
        thread = {'id': 't', 'title': 'T', 'status': 'open', 'detail': '', 'pcs': 'Ash'}
        self.assertEqual(
            shapes.THREADS.problems({'threads': [thread]}),
            ['threads.threads[0].pcs is string, not array', 'threads.threads[0].source is missing'],
        )

    def test_browser_builds_records_from_known_shapes(self):
        source = '\n'.join(path.read_text(encoding='utf-8') for path in BROWSER)
        kinds = set(re.findall(r"blank\('(\w+)'", source))
        self.assertLessEqual(
            {'codex_entry', 'thread', 'art_item', 'prep', 'scene', 'handout', 'map_key', 'area'},
            kinds,
        )
        self.assertLessEqual(kinds, set(shapes.SHAPES))
        # Record defaults are spelled out only in DM/shapes.py.
        spelled = re.findall(
            r'\b(?:secrets|player_text|creatures|encounter|recap|trigger|effect|files|tags|rooms'
            r"|journal|handouts): (?:''|\[\])",
            source,
        )
        self.assertEqual(spelled, [])


if __name__ == '__main__':
    unittest.main()
