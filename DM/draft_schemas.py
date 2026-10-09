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


def for_kind(kind):
    """The strict schema for a draft kind, as the server names it when it queues the job."""
    if kind == 'request':
        return request_workflow.SCHEMA
    if kind == 'content':
        return workflow.CONTENT_SCHEMA
    if kind == 'classify':
        return transcript_classifier.SCHEMA
    if kind == 'thread-ledger':
        return thread_ledger.SCHEMA
    if kind == 'arc-options':
        return arc_options.SCHEMA
    if kind in ('layout', 'revision'):
        return strict_layout_schema()
    raise ValueError('Unknown structured draft kind.')
