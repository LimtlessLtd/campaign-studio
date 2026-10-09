"""Submit one structured proposal to the OpenAI Responses API in a cancellable child process."""

import json
import os
import sys
import urllib.error
import urllib.request
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import arc_options
import request_workflow
import thread_ledger
import transcript_classifier
import workflow

RESPONSES_URL = 'https://api.openai.com/v1/responses'
MAX_RESPONSE = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None  # keep the Authorization header on the fixed API origin


def draft_schema(kind):
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


def parse_response(payload):
    if not isinstance(payload, dict):
        raise ValueError('OpenAI returned an invalid response.')
    if payload.get('status') != 'completed':
        detail = payload.get('incomplete_details') or payload.get('error') or {}
        raise ValueError('OpenAI response was not completed: ' + str(detail)[:300])
    texts = []
    for item in payload.get('output', []):
        if item.get('type') != 'message':
            continue
        for content in item.get('content', []):
            if content.get('type') == 'refusal':
                raise ValueError(
                    'OpenAI declined this draft: ' + str(content.get('refusal', ''))[:300]
                )
            if content.get('type') == 'output_text':
                texts.append(content.get('text', ''))
    if not texts:
        raise ValueError('OpenAI did not return a structured draft.')
    draft = json.loads(''.join(texts))
    if not isinstance(draft, dict):
        raise ValueError('OpenAI returned a proposal that is not an object.')
    return draft


def token_usage(payload):
    """Tokens the response reports. OpenAI states no price, so cost stays unknown."""
    used = payload.get('usage') if isinstance(payload, dict) else None
    used = used if isinstance(used, dict) else {}
    cached = (used.get('input_tokens_details') or {}).get('cached_tokens')
    return {
        'input_tokens': used.get('input_tokens', 0),
        'output_tokens': used.get('output_tokens', 0),
        'cache_read_input_tokens': cached if isinstance(cached, int) else 0,
    }


def generate(kind, model, key_env, prompt, opener=None):
    key = os.environ.get(key_env)
    if not key:
        raise ValueError('The configured OpenAI API key environment variable is not set.')
    if len(prompt) > 8 * 1024 * 1024:
        raise ValueError('The structured draft prompt is too large.')
    body = json.dumps(
        {
            'model': model,
            'input': prompt,
            'store': False,
            'tools': [],
            'text': {
                'format': {
                    'type': 'json_schema',
                    'name': 'campaign_proposal',
                    'strict': True,
                    'schema': draft_schema(kind),
                }
            },
        }
    ).encode('utf-8')
    request = urllib.request.Request(
        RESPONSES_URL,
        data=body,
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key},
    )
    client = opener or urllib.request.build_opener(NoRedirect())
    with client.open(request, timeout=600) as response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError('OpenAI returned a response larger than 8 MB.')
    payload = json.loads(raw)
    return parse_response(payload), token_usage(payload)


def main():
    if len(sys.argv) != 4:
        raise ValueError('Expected draft kind, model and key environment variable.')
    kind, model, key_env = sys.argv[1:]
    prompt = sys.stdin.read(8 * 1024 * 1024 + 1)
    draft, used = generate(kind, model, key_env, prompt)
    print(json.dumps({'structured_output': draft, 'usage': used}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        sys.exit(f'OpenAI drafting failed: HTTP {error.code}.')
    except (ValueError, urllib.error.URLError, OSError) as error:
        sys.exit('OpenAI drafting failed: ' + str(error))
