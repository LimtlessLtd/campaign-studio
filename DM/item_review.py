"""Item-by-item review of a draft: the GM accepts or rejects each row of a proposal.

A workflow names the kinds of rows a GM can decide on one at a time, such as `entries` or `scenes`. The
browser addresses a row by the key `kind:id`. `without` drops the rejected rows and reports which IDs went,
so the workflow can remove whatever linked to them from the rows that stay.
"""


def key(kind, row):
    return f'{kind}:{row["id"]}'


def keys(draft, kinds):
    """Every key the GM may reject in `draft`."""
    return {key(kind, row) for kind in kinds for row in draft.get(kind, [])}


def without(draft, rejected, kinds):
    """The draft minus the rejected rows, and the IDs removed from each kind.

    Raises ValueError for a key that names no row of this draft, so a stale page cannot reject by accident.
    """
    rejected = set(rejected or ())
    if rejected - keys(draft, kinds):
        raise ValueError('A rejected item is not in this proposal.')
    gone = {
        kind: {row['id'] for row in draft.get(kind, []) if key(kind, row) in rejected}
        for kind in kinds
    }
    kept = {
        **draft,
        **{
            kind: [row for row in draft.get(kind, []) if row['id'] not in gone[kind]]
            for kind in kinds
        },
    }
    return kept, gone
