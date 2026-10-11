"""Write one row of a map's content draft again, alone.

The same contract as a request's proposal (`redraft`): the AI answers with that one row, the row is spliced
in under its old ID and the whole draft is validated again, so every other row and location description
stays exactly as the GM reviewed it. A content draft covers a whole map, so the prompt carries an outline
of it, not every row's full text.
"""

import item_review
import redraft
import workflow


def outline(draft, row):
    """What of a content draft the AI needs to write `row` again, which is a row of that draft.

    The draft's summary, the description of the row's own location, and the ID, name and location of every
    other row (so nothing is written twice). The other rows' full text is left out to keep the prompt small.
    """
    return {
        'summary': draft['summary'],
        'location': next((a for a in draft['areas'] if a['n'] == row['area']), None),
        'other_rows': {
            kind: [
                {'id': r['id'], 'name': r.get('name') or r.get('title'), 'location': r['area']}
                for r in draft[kind]
                if r['id'] != row['id']
            ]
            for kind in workflow.CONTENT_ROWS
        },
    }


def pack(value, where, note, campaign_info):
    """What to ask the AI to write the row `where` names again, in a content workflow awaiting review.

    Returns the prompt, the schema of the answer and the draft kind a provider holds it to. The outline
    and the row ride in the prompt, so the workflow's own context gets that much less of the budget.
    """
    draft = workflow.staged_content(value)
    row = item_review.find(draft, where, workflow.CONTENT_ROWS)
    kind = where.partition(':')[0]
    note = redraft.clean_note(note)
    shown = outline(draft, row)
    base = workflow.prompt_pack(value, campaign_info, reserve=redraft.added_chars(shown, row, note))
    return {
        'prompt': redraft.prompt(base['prompt'], shown, where, row, note),
        'schema': redraft.envelope(base['schema'], kind),
        'draft_kind': redraft.provider_kind(value['kind'], kind),
    }


def write_again(value, where, output):
    """Write the row `where` names again from the AI's `output`, and stage the whole draft again.

    Nothing is stored unless the draft with the new row in it validates and the map has not changed
    since the draft was made.
    """
    draft = workflow.staged_content(value)
    row = redraft.new_row(workflow.CONTENT_SCHEMA, where.partition(':')[0], output)
    workflow.stage(value, item_review.replaced(draft, where, row, workflow.CONTENT_ROWS))
