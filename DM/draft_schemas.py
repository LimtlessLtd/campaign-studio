"""The JSON schema each structured-draft kind is held to by providers that enforce a strict one.

OpenAI's strict structured outputs require every object property in `required` and no extra properties;
the OpenAI and Codex workers both send these schemas, so a new draft kind adds its schema here once.
Claude Code is passed the schema the caller built and needs no conversion.
"""

from copy import deepcopy

import arc_options
import request_workflow
import thread_ledger
import transcript_classifier
import workflow


def strict_layout_schema():
    """Constrain each operation to its own complete strict-output object."""
    schema = deepcopy(workflow.LAYOUT_SCHEMA)
    fields = schema['properties']['operations']['items']['properties']
    variants = {
        'rect': ('row', 'col', 'width', 'height', 'fill', 'border'),
        'path': ('points', 'width', 'char'),
        'stamp': ('row', 'col', 'rows'),
        'scatter': ('row', 'col', 'width', 'height', 'count', 'char', 'replace'),
    }
    schema['properties']['operations']['items'] = {
        'anyOf': [
            workflow.obj(
                {'type': {'type': 'string', 'enum': [kind]}}
                | {field: fields[field] for field in names}
            )
            for kind, names in variants.items()
        ]
    }
    return schema


# Draft kind (as the server names it when it queues the job) -> builder of its strict schema. A request
# is drafted under its request kind when that kind has a workflow of its own (`request_workflow.draft_kind`).
BUILDERS = {
    'request': lambda: request_workflow.SCHEMA,
    **{
        kind: (lambda module=module: module.SCHEMA)
        for kind, module in request_workflow.WORKFLOWS.items()
    },
    'content': lambda: workflow.CONTENT_SCHEMA,
    'classify': lambda: transcript_classifier.SCHEMA,
    'thread-ledger': lambda: thread_ledger.SCHEMA,
    'arc-options': lambda: arc_options.SCHEMA,
    'layout': strict_layout_schema,
    'revision': strict_layout_schema,
}


def for_kind(kind):
    """The strict schema for a draft kind. A new kind adds one row to `BUILDERS`."""
    build = BUILDERS.get(kind)
    if build is None:
        raise ValueError('Unknown structured draft kind.')
    return build()
