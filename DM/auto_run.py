"""The automatic run (W72): new recordings in a folder to a reviewed next-session draft.

One run follows the recordings of one session through every step the GM does not have to do: local
transcription, sorting play from banter, the thread ledger, arc options for loose threads and the next
session's Session Forge draft. It stops at each review (banter confirmations, the ledger, the arc options
and the draft), because nothing may change the campaign unreviewed, and carries on once the GM has reviewed.

This module is policy: which recordings are new, what each step needs next, what waits for the GM and what
the next session's pitch says. It reads a snapshot of stored facts and returns steps and actions; it never
writes a document or starts a job. campaign_core gathers the snapshot and carries the actions out.
Transcript, thread and codex text is reference data and is never instructions.
"""

import re

import arc_options

MAX_RECORDINGS = 8  # recordings one run follows: one session at a time
MAX_THREADS = 12  # story threads a session request may name (session_workflow.options)
STEPS = ('transcribe', 'sort', 'review', 'ledger')  # what each recording goes through, in order
LABELS = {
    'transcribe': 'Transcribe on this computer',
    'sort': 'Sort play from banter',
    'review': 'Review the passages',
    'ledger': 'Thread ledger',
    'arcs': 'Arc options for loose threads',
    'draft': 'Draft the next session',
}
LIMIT = re.compile(
    r'usage limit|limit reached|hit your limit|rate.?limit|5-hour limit|weekly limit|quota|overloaded',
    re.IGNORECASE,
)
LIMIT_NOTE = 'A usage limit was probably reached. Run again after it resets: nothing is lost.'
BUSY_NOTE = 'Waits for the AI request in progress'


class NothingNew(ValueError):
    """A folder has no recording a run could take: the usual answer of a scheduled run, not a fault."""


# ---------- which recordings, which session ----------


def new_recordings(files, known, since=None, limit=MAX_RECORDINGS):
    """The recordings a run should take, oldest first: those with no transcript yet.

    `files` is a folder listing (id, modified, ...), `known` the IDs that already have a transcript and
    `since` the modification time of the newest recording processed before. With a baseline a run takes
    every newer file; without one (the first run) it takes only the newest, so a folder holding a whole
    campaign's recordings is never transcribed by accident.
    """
    fresh = sorted(
        (f for f in files if f['id'] not in known and (since is None or f['modified'] > since)),
        key=lambda f: (f['modified'], f['name']),
    )
    return fresh[-limit:] if since is not None else fresh[-1:]


def default_session(preps):
    """The prep a new recording most likely belongs to: the newest active one whose log is still empty."""
    open_preps = [p for p in preps if not p['archived'] and not p['played']]
    return max(open_preps, key=lambda p: p['n'])['id'] if open_preps else ''


def next_session_number(active, every, after):
    """The prep to draft next: the first active prep after `after`, else one past every number in use.

    Archived preps count as in use, so an archived session is never reopened by a new one.
    """
    later = sorted(n for n in active if n > after)
    return later[0] if later else max(every, default=0) + 1


def arc_candidates(report, session, limit=arc_options.MAX_THREADS):
    """Loose threads to ask arc options for: unplanned ones the session touched, then the stalest.

    `report` is the loose-threads report (stalest first). Planned threads already have an arc.
    """
    rows = [row for row in report if row['status'] in ('open', 'foreshadowed')]
    touched = [row for row in rows if row['last_session'] == session]
    rest = [row for row in rows if row['last_session'] != session]
    return [row['id'] for row in touched + rest][:limit]


def next_pitch(session, seeds, titles):
    """The pitch the next session's draft starts from: the GM's chosen arc lines, else the loose threads."""
    head = f'Plan the session after {session.upper()}.'
    if seeds:
        lines = '\n'.join(f'- {seed["title"]} ({seed["kind"]}): {seed["pitch"]}' for seed in seeds)
        return f'{head} Bring these planned arcs to the table:\n{lines}'
    if titles:
        return f'{head} Move these loose threads forward: {"; ".join(titles)}.'
    return head


def request_threads(seeds, candidates, known):
    """The story threads a session request names: the chosen arcs' first, then the candidates."""
    ids = [seed['thread'] for seed in seeds] + list(candidates)
    return [ident for ident in dict.fromkeys(ids) if ident in known][:MAX_THREADS]


# ---------- what is next ----------


def plural(count, noun):
    return f'{count} {noun}' + ('' if count == 1 else 's')


def limit_hint(message):
    """A friendlier note when a failure reads like a subscription limit, else the message itself."""
    message = (message or '').strip()
    return f'{LIMIT_NOTE} ({message[:200]})' if message and LIMIT.search(message) else message


def ai_busy(snapshot):
    """True while an AI request of this run is queued or running: a run asks for one at a time."""
    for rec in snapshot['recordings']:
        sorting = (rec['transcript'] or {}).get('sorting')
        if sorting == 'running' or (rec['ledger'] or {}).get('status') == 'running':
            return True
    arc, request = snapshot['arc'], snapshot['request']
    return (
        isinstance(arc, dict)
        and arc['status'] == 'running'
        or isinstance(request, dict)
        and request['status'] == 'doing'
    )


class Slot:
    """The one AI request a run may start now."""

    def __init__(self, free):
        self.free = free

    def take(self):
        taken, self.free = self.free, False
        return taken


def recording_steps(rec, session, explicit, slot):
    """(states, actions, finished) for one recording: each step's (state, note), what to start, whether done."""
    states = {step: ('todo', '') for step in STEPS}
    actions = []
    here = {'recording': rec['id']}

    def start(step, resume='', first=False):
        """Queue an AI step (after `first`, a step that must precede it) if the slot is free, else note the wait."""
        if slot.take():
            actions.extend(dict(here, do=do) for do in (['link'] if first else []) + [step])
            states[step] = ('queued', resume)
        else:
            states[step] = ('queued', BUSY_NOTE)

    transcript = rec['transcript']
    if transcript is None:
        if rec['transcribing']:
            states['transcribe'] = (
                'running',
                'Working on this computer; a long recording takes a while',
            )
        elif rec['transcribe_error'] and not explicit:
            states['transcribe'] = ('failed', rec['transcribe_error'])
        elif explicit:
            actions.append(dict(here, do='transcribe'))
            states['transcribe'] = ('queued', 'Retrying' if rec['transcribe_error'] else '')
        else:
            states['transcribe'] = ('todo', 'Not started')
        return states, actions, False
    states['transcribe'] = ('done', '')

    sorting = transcript['sorting']
    if sorting == 'running':
        states['sort'] = ('running', '')
        return states, actions, False
    if sorting == 'failed' and not explicit:
        states['sort'] = ('failed', limit_hint(transcript['sorting_error']))
        return states, actions, False
    if sorting != 'done':
        start('sort', 'Resuming after a failure' if sorting == 'failed' else '')
        return states, actions, False
    states['sort'] = ('done', '')

    if transcript['pending']:
        states['review'] = ('review', f'{plural(transcript["pending"], "passage")} to decide')
        return states, actions, False
    states['review'] = ('done', '')
    if not transcript['play']:
        states['ledger'] = ('done', 'No passage was confirmed as in-game')
        return states, actions, True

    ledger = rec['ledger']
    status = ledger['status'] if ledger else ''
    if status == 'running':
        states['ledger'] = ('running', '')
    elif status == 'review':
        events = ledger['events']
        states['ledger'] = (
            ('review', f'{plural(events, "proposed change")} to review')
            if events
            else ('done', 'Nothing was proposed')
        )
    elif status == 'applied':
        states['ledger'] = ('done', '')
    elif status and not explicit:  # failed, or paused between windows
        states['ledger'] = ('failed', limit_hint(ledger['error']) or 'The draft stopped early')
    else:
        unlinked = not ledger and transcript['session'] != session
        start('ledger', 'Resuming after a failure' if ledger else '', first=unlinked)
    return states, actions, states['ledger'][0] == 'done'


def session_steps(snapshot, explicit, slot, ready):
    """({arcs, draft} -> (state, note), actions) for what the whole session needs once its recordings are done."""
    states = {'arcs': ('todo', ''), 'draft': ('todo', '')}
    actions = []
    if not ready:
        return states, actions

    arc = snapshot['arc']
    if arc == 'removed':
        states['arcs'] = ('done', 'The proposal was removed')
    elif arc is None or (arc['status'] == 'failed' and explicit):
        candidates = snapshot['candidates']()
        if not candidates:
            states['arcs'] = ('done', 'No loose threads to plan')
        elif slot.take():
            actions.append({'do': 'arcs', 'threads': candidates})
            states['arcs'] = ('queued', 'Retrying' if arc else '')
        else:
            states['arcs'] = ('queued', BUSY_NOTE)
    elif arc['status'] == 'running':
        states['arcs'] = ('running', '')
    elif arc['status'] == 'review':
        states['arcs'] = ('review', 'Choose an option for each thread, or leave it')
    elif arc['status'] == 'applied':
        states['arcs'] = ('done', '')
    else:
        states['arcs'] = ('failed', limit_hint(arc['error']))
    if states['arcs'][0] != 'done':
        return states, actions

    request = snapshot['request']
    if request == 'removed':
        states['draft'] = ('done', 'The request was removed')
    elif request is None or (request['status'] == 'new' and explicit):
        if slot.take():
            actions.append({'do': 'draft'})
            states['draft'] = ('queued', 'Retrying' if request and request['error'] else '')
        else:
            states['draft'] = ('queued', BUSY_NOTE)
    elif request['status'] == 'doing':
        states['draft'] = ('running', '')
    elif request['status'] == 'review':
        states['draft'] = ('review', 'Review the proposal and apply it')
    elif request['status'] == 'new':
        states['draft'] = ('failed', limit_hint(request['error']) or 'The draft was not started')
    else:
        states['draft'] = ('done', '')
    return states, actions


def overall(rows):
    """Where the whole run stands, from its steps: running, stopped, waiting (on the GM), done or ready."""
    states = {row['state'] for row in rows}
    if states & {'running', 'queued'}:
        return 'running'
    if 'failed' in states:
        return 'stopped'
    if 'review' in states:
        return 'waiting'
    return 'done' if states == {'done'} else 'ready'


def waiting(rows):
    """What the GM has to do, one sentence per step that waits for a review."""
    return [
        ' '.join(
            x
            for x in (
                row['label'] + ':',
                row['subject'] + '.' if row['subject'] else '',
                row['note'],
            )
            if x
        )
        for row in rows
        if row['state'] == 'review'
    ]


def decide(snapshot, explicit=False):
    """The run's steps, the actions to carry out now and where it stands.

    `snapshot` holds what is stored: `recordings` (each with `id`, `name`, `transcribing`,
    `transcribe_error`, `transcript` and `ledger` facts or None), the run's `session`, its `arc` and
    `request` (None, 'removed' or facts) and `candidates`, a function giving the loose threads for arcs
    (called only when needed). `explicit` is the GM or the schedule pressing Run: only then is a failed
    step retried or a transcription started, so a failure never loops by itself. At most one AI request
    is started, so a usage limit stops the run on the first one.
    """
    slot = Slot(not ai_busy(snapshot))
    rows, actions, finished = [], [], True
    for rec in snapshot['recordings']:
        states, more, done = recording_steps(rec, snapshot['session'], explicit, slot)
        actions += more
        finished = finished and done
        for step in STEPS:
            state, note = states[step]
            rows.append(
                dict(step=step, label=LABELS[step], recording=rec['id'], subject=rec['name'])
                | dict(state=state, note=note)
            )
    states, more = session_steps(snapshot, explicit, slot, finished)
    actions += more
    for step, (state, note) in states.items():
        rows.append(
            dict(step=step, label=LABELS[step], recording='', subject='', state=state, note=note)
        )
    return {'state': overall(rows), 'steps': rows, 'actions': actions}
