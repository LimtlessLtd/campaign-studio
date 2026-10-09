"""AI usage ledger: tokens, cost and time per drafting job, read from the provider's own output.

A provider reports usage in the JSON its child process prints: Claude Code's result envelope carries
`usage`, `total_cost_usd` and `duration_ms`; the OpenAI worker prints `usage` beside the draft. The ledger
is the `usage` field of each finished job record, so it needs no separate store and survives with the job.
"""

import datetime
import json

TOKEN_FIELDS = (
    'input_tokens',
    'output_tokens',
    'cache_read_input_tokens',
    'cache_creation_input_tokens',
)
DRAFT_KINDS = ('request-draft', 'ai-workflow', 'classify', 'thread-ledger', 'arc-options')


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def from_output(raw, seconds=None):
    """The usage in a provider's JSON output, or None when it reported none."""
    try:
        envelope = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(envelope, dict):
        return None
    reported = envelope.get('usage')
    cost = envelope.get('total_cost_usd')
    if not isinstance(reported, dict) and cost is None:
        return None
    reported = reported if isinstance(reported, dict) else {}
    out = {field: _count(reported.get(field)) for field in TOKEN_FIELDS}
    out['cost_usd'] = (
        round(float(cost), 6)
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0
        else None
    )
    duration = envelope.get('duration_ms')
    out['seconds'] = round(_count(duration) / 1000, 1) if duration else seconds
    return out


def record(job, raw):
    """Attach the usage in `raw` to a finished drafting job. Does nothing for other jobs."""
    if job.get('kind') not in DRAFT_KINDS:
        return
    seconds = None
    if job.get('started') and job.get('ended'):
        seconds = round(job['ended'] - job['started'], 1)
    found = from_output(raw, seconds)
    if found:
        job['usage'] = found


def _blank(month=''):
    return {'month': month, 'jobs': 0, 'cost_usd': 0.0, 'seconds': 0.0, 'unpriced': 0} | {
        field: 0 for field in TOKEN_FIELDS
    }


def _add(row, used):
    row['jobs'] += 1
    for field in TOKEN_FIELDS:
        row[field] += _count(used.get(field))
    row['seconds'] = round(row['seconds'] + (used.get('seconds') or 0), 1)
    if used.get('cost_usd') is None:
        row['unpriced'] += 1
    else:
        row['cost_usd'] = round(row['cost_usd'] + used['cost_usd'], 6)


def totals(jobs):
    """Sum job usage per month (by job creation date, newest first) and overall."""
    months = {}
    for job in jobs:
        used = job.get('usage')
        if used:
            month = datetime.datetime.fromtimestamp(job.get('created', 0)).strftime('%Y-%m')
            _add(months.setdefault(month, _blank(month)), used)
    return {'months': sorted(months.values(), key=lambda row: row['month'], reverse=True)}


def combined(jobs):
    """The usage of `jobs` as one row, whatever months they span (for one run's report)."""
    row = _blank()
    for job in jobs:
        if job.get('usage'):
            _add(row, job['usage'])
    del row['month']
    return row
