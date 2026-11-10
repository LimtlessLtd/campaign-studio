"""The owner's three-part layout for GM scene prep: key points, one sentence to read aloud, and checks.

A scene shows the GM at a glance who is there and what is going on (`key_points`), the one sentence to read
to the players (`read_aloud`) and the rolls the players can make (`checks`: a skill and DC, and what
success gets them: information, an item, or help for an NPC). Both scene-drafting workflows share the
schema fields, the instruction, the validation and the stored fields built here, so the layout cannot drift
between them.
"""

import re
from copy import deepcopy

import shapes
import workflow

MAX_KEY_POINTS = 3
KEY_POINT_CHARS = 200
MAX_CHECKS = 6
SKILL_CHARS = 60
MIN_DC, MAX_DC = 1, 30
READ_ALOUD_WORDS = 50

SCHEMA_FIELDS = {
    'key_points': workflow.arr(workflow.STR, MAX_KEY_POINTS),
    'read_aloud': workflow.STR,
    'checks': workflow.arr(
        workflow.obj({'skill': workflow.STR, 'dc': workflow.INT, 'result': workflow.STR}),
        MAX_CHECKS,
    ),
}

INSTRUCTION = (
    'Lay every scene out for the GM at a glance, with nothing important buried under flavour text. '
    f'key_points are 1 to {MAX_KEY_POINTS} short bullets: who is there and what they want, what is going '
    'on, and any GM-only truth. '
    f'read_aloud is exactly one sentence of at most {READ_ALOUD_WORDS} words for the GM to read to the '
    'players: put detail in key_points or checks, not in read_aloud. '
    'checks are the rolls the players can make: each is one skill (such as Perception or Insight), a DC '
    f'from {MIN_DC} to {MAX_DC}, and what success gets them: information, an item, or help for an NPC. '
    'Give every scene at least one check unless nothing in it can be learned or gained. '
)

# A full stop after one of these does not end a sentence.
ABBREVIATIONS = {'mr', 'mrs', 'ms', 'dr', 'st', 'mt', 'vs', 'etc', 'no'}
# A sentence ends at a stop, then a space, then a capital, digit or opening quote or bracket.
BREAK = re.compile(r'([.!?]+)["”’\')\]]*\s+(?=["“‘\'(\[]?[A-Z0-9])')


def words(text):
    return str(text or '').split()


def sentence_count(text):
    """How many sentences the text holds (0 when it is blank). Quoted speech continues its sentence."""
    text = ' '.join(words(text))
    if not text:
        return 0
    count = 1
    for stop in BREAK.finditer(text):
        before = text[: stop.start()].split()[-1:] or ['']
        if stop.group(1) == '.' and (before[0].lower() in ABBREVIATIONS or len(before[0]) == 1):
            continue  # an abbreviation or an initial
        count += 1
    return count


def read_aloud_problem(text):
    """Why the text is not one short sentence to read to the players, or '' when it is."""
    if not words(text):
        return 'needs one sentence to read aloud'
    if sentence_count(text) != 1:
        return 'read-aloud must be one sentence'
    if len(words(text)) > READ_ALOUD_WORDS:
        return f'read-aloud must be at most {READ_ALOUD_WORDS} words'
    return ''


def staged_flags(draft):
    """One flag per drafted scene: True for a scene staged before this layout existed.

    Such a scene has neither key points nor checks. It stays reviewable and applicable as drafted, and
    `validate` is skipped for it; every other scene must follow the layout. The draft itself is not
    changed, so revalidating a stored draft gives the same answer.
    """
    scenes = draft.get('scenes') if isinstance(draft.get('scenes'), list) else []
    return [
        isinstance(scene, dict) and 'key_points' not in scene and 'checks' not in scene
        for scene in scenes
    ]


def schema_view(draft, flags):
    """A copy of the draft in which staged scenes have the layout's fields empty, to check against a schema."""
    view = deepcopy(draft)
    scenes = view.get('scenes') if isinstance(view.get('scenes'), list) else []
    for scene, old in zip(scenes, flags, strict=True):
        if old:
            scene.update(key_points=[], checks=[])
            scene.setdefault('read_aloud', '')  # a general request's scene had none
    return view


def validate(scene):
    """Raise ValueError unless a schema-valid drafted scene follows the layout."""
    points = scene['key_points']
    if not 1 <= len(points) <= MAX_KEY_POINTS or any(
        not point.strip() or len(point) > KEY_POINT_CHARS for point in points
    ):
        raise ValueError(
            f'A scene needs 1 to {MAX_KEY_POINTS} key points of at most {KEY_POINT_CHARS} characters.'
        )
    problem = read_aloud_problem(scene['read_aloud'])
    if problem:
        raise ValueError(f'A scene {problem}.')
    for check in scene['checks']:
        if (
            not check['skill'].strip()
            or len(check['skill']) > SKILL_CHARS
            or not MIN_DC <= check['dc'] <= MAX_DC
            or not check['result'].strip()
        ):
            raise ValueError(
                f'A scene check needs a skill, a DC from {MIN_DC} to {MAX_DC} and what success gets.'
            )


def layout_problems(scene):
    """What a stored scene lacks of the layout, for the prep readiness board (empty when it follows it)."""
    problems = []
    if not any(str(point).strip() for point in scene.get('key_points') or []):
        problems.append('no key points')
    if not words(scene.get('read_aloud')):
        problems.append('no sentence to read aloud')
    elif problem := read_aloud_problem(scene['read_aloud']):
        problems.append(problem)
    if not scene.get('checks'):
        problems.append('no checks')
    return problems


def stored(row):
    """The stored scene fields for a validated drafted scene."""
    return {
        'key_points': [point.strip() for point in row.get('key_points', [])],
        'read_aloud': row.get('read_aloud', '').strip(),
        'checks': [shapes.SCENE_CHECK.new(**check) for check in row.get('checks', [])],
    }
