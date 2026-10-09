"""The stored shape of each campaign record, defined once.

A shape names the fields a new record must be given and the defaults for the rest, and the shape of the
records in each of its lists. Apply paths build records with Shape.new, the schema migration completes
stored documents with Shape.fill_all, and the browser builds records from GET /api/shapes. Links and
provenance (map, area, codex, workflow, request) are optional extra fields, not part of a shape.

Adding a field changes fields_digest(). Stored records gain the field only through a migration, so a test
fails until schema.py has a new version that fills it.
"""

import hashlib
import json
from copy import deepcopy


class ShapeError(ValueError):
    pass


def json_type(value):
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float)):
        return 'number'
    return {str: 'string', list: 'array', dict: 'object'}.get(type(value), 'null')


class Shape:
    """A stored record type: required fields, defaults for the rest, and the shapes of its lists."""

    def __init__(self, name, required, defaults, rows=None):
        self.name = name
        self.required = tuple(required)
        self.defaults = defaults
        self.rows = rows or {}  # list field -> Shape of the records in it
        assert set(self.rows) <= set(defaults), name

    def new(self, **fields):
        """A complete new record: required fields first, then defaults, then any extra links."""
        missing = [key for key in self.required if key not in fields]
        if missing:
            raise TypeError(f'A new {self.name} needs {", ".join(missing)}.')
        record = {key: fields[key] for key in self.required}
        for key, default in self.defaults.items():
            record[key] = fields[key] if key in fields else deepcopy(default)
        record.update((key, value) for key, value in fields.items() if key not in record)
        return record

    def fill(self, record):
        """Give an existing record each missing (or null) default field, keeping every other value."""
        for key, default in self.defaults.items():
            if record.get(key) is None:
                record[key] = deepcopy(default)
        return record

    def fill_all(self, document, path):
        """Fill a stored document and every record in its lists. Rows that are not objects are kept."""
        if not isinstance(document, dict):
            raise ShapeError(f'{path} must contain a JSON object.')
        self.fill(document)
        for field, shape in self.rows.items():
            if not isinstance(document[field], list):
                raise ShapeError(f'{path}: {field} must be a list.')
            for row in document[field]:
                if isinstance(row, dict):
                    shape.fill_all(row, path)
        return document

    def problems(self, record, where=None):
        """Each field a record (or a record in its lists) lacks, or holds with another JSON type."""
        where = where or self.name
        if not isinstance(record, dict):
            return [f'{where} is not an object']
        found = [f'{where}.{key} is missing' for key in self.required if key not in record]
        for key, default in self.defaults.items():
            if key not in record:
                found.append(f'{where}.{key} is missing')
            elif json_type(record[key]) != json_type(default):
                found.append(f'{where}.{key} is {json_type(record[key])}, not {json_type(default)}')
        for field, shape in self.rows.items():
            for i, row in enumerate(
                record.get(field) if isinstance(record.get(field), list) else []
            ):
                found += shape.problems(row, f'{where}.{field}[{i}]')
        return found

    def describe(self):
        return {'required': list(self.required), 'defaults': deepcopy(self.defaults)}


CODEX_ENTRY = Shape(
    'codex_entry',
    ['id', 'type', 'name'],
    dict(group='', status='', public='', secrets='', notes='', image='', files=[], tags=[]),
)
THREAD_LOCATION = Shape('thread_location', ['id'], dict(map='', area=0))
THREAD_CLUE = Shape('thread_clue', ['id'], dict(text='', where='', status='planned'))
THREAD = Shape(
    'thread',
    ['id', 'title'],
    dict(
        status='open',
        detail='',
        pcs=[],
        source='',
        entries=[],
        sessions=[],
        maps=[],
        locations=[],
        clues=[],
    ),
    {'locations': THREAD_LOCATION, 'clues': THREAD_CLUE},
)
ART_ITEM = Shape(
    'art_item', ['id', 'prompt'], dict(title='', codex='', image='', status='queued', created=0)
)
SCENE_CLUE = Shape('scene_clue', [], dict(thread='', text=''))
SCENE = Shape(
    'scene',
    ['id'],
    dict(
        title='',
        purpose='',
        where='',
        map='',
        area=0,
        npcs=[],
        encounter='',
        encounter_detail=dict(creatures=[], difficulty='', terrain='', tactics='', resolution=''),
        clues=[],
        read_aloud='',
        notes='',
        done=False,
    ),
    {'clues': SCENE_CLUE},
)
HANDOUT = Shape('handout', ['id'], dict(title='', player_text='', secrets='', image_prompt=''))
CHECKLIST_ITEM = Shape('checklist_item', [], dict(text='', done=False))
LOOT = Shape('loot', [], dict(item='', where='', value=''))
JOURNAL = Shape('journal', ['id'], dict(title='', text='', secrets=''))
EVENT = Shape('event', ['id'], dict(title='', trigger='', effect=''))
AREA = Shape(
    'area',
    ['n', 'name', 'kind', 'at'],
    dict(
        rooms=[],
        text='',
        creatures='',
        loot=[],
        events=[],
        npcs=[],
        items=[],
        journal=[],
        images=[],
        threads=[],
    ),
    {'journal': JOURNAL, 'events': EVENT, 'loot': LOOT},
)
# Stored documents.
CODEX = Shape('codex', [], dict(entries=[]), {'entries': CODEX_ENTRY})
THREADS = Shape('threads', [], dict(threads=[]), {'threads': THREAD})
ART = Shape('art', [], dict(items=[]), {'items': ART_ITEM})
PREP = Shape(
    'prep',
    ['n', 'title'],
    dict(
        date='',
        status='planning',
        archived=False,
        pitch='',
        recap='',
        goals=[],
        threads=[],
        scenes=[],
        checklist=[],
        notes='',
        loot=[],
        handouts=[],
        log=dict(summary='', notes='', outcomes=[]),
    ),
    {'scenes': SCENE, 'handouts': HANDOUT, 'checklist': CHECKLIST_ITEM, 'loot': LOOT},
)
MAP_KEY = Shape(
    'map_key',
    [],
    dict(map='', areas=[], events=[], notes='', session='', stocked=False, images=[]),
    {'areas': AREA, 'events': EVENT},
)

TRANSCRIPT_SEGMENT = Shape('transcript_segment', [], dict(start=0, end=0, text=''))
# A run of segments (first..last, inclusive) read as one thing: kind 'play' is the story, 'banter' is table
# talk, 'unclear' needs the GM. `confirmed` is the GM's decision; nothing leaves the step unconfirmed.
# `remember` is the table-lore note a confirmed banter passage saves; `lore` names the saved item it matches.
TRANSCRIPT_PASSAGE = Shape(
    'transcript_passage',
    ['id'],
    dict(first=0, last=0, kind='unclear', gist='', remember='', lore='', confirmed=False),
)
TRANSCRIPT = Shape(
    'transcript',
    ['id'],
    dict(
        title='',
        session='',
        source=dict(name='', path='', size=0, modified=0),
        provider='',
        model='',
        language='',
        duration=0,
        created=0,
        truncated=False,
        segments=[],
        passages=[],
        classification=dict(status='', cursor=0, job='', error=''),
    ),
    {'segments': TRANSCRIPT_SEGMENT, 'passages': TRANSCRIPT_PASSAGE},
)
# Gags and invented fiction the GM confirmed are not the campaign, so later runs skip them.
TABLE_LORE_ITEM = Shape(
    'table_lore_item', ['id'], dict(text='', transcript='', passage='', added=0)
)
TABLE_LORE = Shape('table_lore', [], dict(items=[]), {'items': TABLE_LORE_ITEM})

# A ledger draft is separate from a transcript: classification can be reviewed without
# accidentally applying campaign changes. Evidence stays with the accepted proposal.
LEDGER_EVENT = Shape(
    'ledger_event',
    ['id'],
    dict(kind='', target='', title='', status='', text='', pcs=[], passage='', quote='', at=0),
)
LEDGER = Shape(
    'ledger',
    ['id'],
    dict(
        session='',
        source='',
        status='',
        cursor=0,
        job='',
        error='',
        events=[],
        selected=[],
        applied=0,
    ),
    {'events': LEDGER_EVENT},
)

# Options proposed for loose threads (W71). The GM picks at most one per thread; nothing changes a thread
# until the GM applies. `base_revs` holds each proposed thread's revision, so an edit made meanwhile is noticed.
ARC_OPTION = Shape(
    'arc_option',
    ['id'],
    dict(thread='', kind='', title='', summary='', hook='', pitch='', entries=[], pcs=[]),
)
ARC = Shape(
    'arc',
    ['id'],
    dict(
        status='',
        threads=[],
        options=[],
        choices=[],
        base_revs={},
        job='',
        error='',
        created=0,
        applied=0,
    ),
    {'options': ARC_OPTION},
)

# The automatic run (W72): which recordings of one session it follows and the records it made along the way.
# Everything else (what is done, what waits for the GM) is read from the transcripts, ledgers, arcs and
# request, so the run holds no state that could disagree with them.
AUTO_RUN_RECORDING = Shape('auto_run_recording', ['id'], dict(name='', path='', size=0, modified=0))
AUTO_RUN = Shape(
    'auto_run',
    ['id'],
    dict(
        session='',
        folder='',
        recordings=[],
        arc='',
        next_session='',
        request='',
        note='',
        created=0,
        finished=0,
    ),
    {'recordings': AUTO_RUN_RECORDING},
)

WORLD_PIN = Shape('world_pin', ['id'], dict(label='', map='', x=0.5, y=0.5, note=''))
WORLD_MAP = Shape('world_map', ['id', 'name'], dict(image='', pins=[]), {'pins': WORLD_PIN})
WORLD_MAPS = Shape('world_maps', [], dict(maps=[]), {'maps': WORLD_MAP})

SHAPES = {
    shape.name: shape
    for shape in (
        CODEX_ENTRY,
        THREAD,
        THREAD_LOCATION,
        THREAD_CLUE,
        ART_ITEM,
        SCENE_CLUE,
        SCENE,
        HANDOUT,
        CHECKLIST_ITEM,
        LOOT,
        JOURNAL,
        EVENT,
        AREA,
        CODEX,
        THREADS,
        ART,
        PREP,
        MAP_KEY,
        WORLD_PIN,
        WORLD_MAP,
        WORLD_MAPS,
        TRANSCRIPT_SEGMENT,
        TRANSCRIPT_PASSAGE,
        TRANSCRIPT,
        TABLE_LORE_ITEM,
        TABLE_LORE,
        LEDGER_EVENT,
        LEDGER,
        ARC_OPTION,
        ARC,
        AUTO_RUN_RECORDING,
        AUTO_RUN,
    )
}


def describe():
    """Every shape, as GET /api/shapes serves it to the browser."""
    return {name: shape.describe() for name, shape in SHAPES.items()}


def fields_digest():
    """A fingerprint of every shape's fields; it changes when a field is added or removed."""
    fields = {
        name: [sorted(shape.required), sorted(shape.defaults), sorted(shape.rows)]
        for name, shape in SHAPES.items()
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode('utf-8')).hexdigest()
