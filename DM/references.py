"""Where a codex entry is used, and removing it without leaving dangling links.

Records point at codex entries by ID from several documents: other entries' `related` lists, thread `pcs` and `entries`,
art items, prep scenes, map-area `npcs`/`items`, and a request's focus entry. `scan` lists every use and
`remove` deletes the entry and unlinks them all in memory; the caller commits every changed document with
one `commit_docs`, so a deletion is never half applied. Both take a plain {document name: value} mapping
from `documents`, so the rules need no running campaign.
"""

CODEX = 'codex'


def documents(read_doc, prep_names, map_slugs):
    """Load every document that can reference a codex entry, keyed by its document name."""
    names = [CODEX, 'threads', 'art', 'inbox']
    names += [f'prep/{name}' for name in prep_names] + [f'mapkey/{slug}' for slug in map_slugs]
    return {name: value for name in names if isinstance(value := read_doc(name), dict)}


def _rows(doc, field):
    rows = doc.get(field)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _ids(row, field):
    value = row.get(field)
    return value if isinstance(value, list) else []


def _links(docs):
    """Each place that holds codex IDs: (document, label, owner record, field, list-or-scalar)."""
    for name, doc in docs.items():
        if name == CODEX:
            for entry in _rows(doc, 'entries'):
                yield (
                    name,
                    f'Related to {entry.get("name") or entry.get("id")}',
                    entry,
                    'related',
                    True,
                )
        elif name == 'threads':
            for thread in _rows(doc, 'threads'):
                for field in ('pcs', 'entries'):
                    yield (
                        name,
                        f'Thread: {thread.get("title") or thread.get("id")}',
                        thread,
                        field,
                        True,
                    )
        elif name == 'art':
            for item in _rows(doc, 'items'):
                yield name, f'Art: {item.get("title") or item.get("id")}', item, 'codex', False
        elif name == 'inbox':
            for item in _rows(doc, 'items'):
                yield (
                    name,
                    f'Request: {str(item.get("text") or item.get("id"))[:60]}',
                    item,
                    'codex',
                    False,
                )
        elif name.startswith('prep/'):
            for scene in _rows(doc, 'scenes'):
                label = f'Session {doc.get("n", name[6:])}, scene: {scene.get("title") or scene.get("id")}'
                yield name, label, scene, 'npcs', True
        elif name.startswith('mapkey/'):
            for area in _rows(doc, 'areas'):
                label = f'Map {name[7:]}, area {area.get("n")}: {area.get("name", "")}'
                for field in ('npcs', 'items'):
                    yield name, label, area, field, True


def scan(docs, entry_id):
    """Every use of entry_id outside its own record: [{doc, where}], in document order."""
    found = []
    for name, label, owner, field, is_list in _links(docs):
        if name == CODEX and owner.get('id') == entry_id:
            continue
        if (entry_id in _ids(owner, field)) if is_list else owner.get(field) == entry_id:
            found.append({'doc': name, 'where': label})
    return found


def remove(docs, entry_id):
    """Delete the entry from docs['codex'] and unlink it everywhere. Returns the names of changed docs."""
    codex = docs.get(CODEX)
    entries = codex.get('entries') if codex else None
    if not isinstance(entries, list) or not any(
        isinstance(e, dict) and e.get('id') == entry_id for e in entries
    ):
        raise KeyError(entry_id)
    codex['entries'] = [e for e in entries if not (isinstance(e, dict) and e.get('id') == entry_id)]
    changed = {CODEX}
    for name, _, owner, field, is_list in _links(docs):
        if is_list and entry_id in _ids(owner, field):
            owner[field] = [value for value in owner[field] if value != entry_id]
            changed.add(name)
        elif not is_list and owner.get(field) == entry_id:
            owner[field] = ''
            changed.add(name)
    return sorted(changed, key=lambda name: (name != CODEX, name))


def remove_many(docs, entry_ids):
    """Delete several entries and unlink their references in one pass over the documents."""
    codex = docs.get(CODEX)
    entries = codex.get('entries') if codex else None
    if not isinstance(entries, list):
        return []
    wanted = set(entry_ids)
    present = {
        e['id']
        for e in entries
        if isinstance(e, dict) and isinstance(e.get('id'), str) and e['id'] in wanted
    }
    if not present:
        return []
    codex['entries'] = [
        entry for entry in entries if not isinstance(entry, dict) or entry.get('id') not in present
    ]
    changed = {CODEX}
    for name, _, owner, field, is_list in _links(docs):
        if is_list:
            linked = _ids(owner, field)
            kept = [value for value in linked if not isinstance(value, str) or value not in present]
            if len(kept) != len(linked):
                owner[field] = kept
                changed.add(name)
        elif isinstance(owner.get(field), str) and owner[field] in present:
            owner[field] = ''
            changed.add(name)
    return sorted(changed, key=lambda name: (name != CODEX, name))


def _map_links(docs, slug, name):
    """Records that point at a map: prep scenes (by name or slug) and request or art items (by slug)."""
    for doc_name, doc in docs.items():
        if doc_name.startswith('prep/'):
            for scene in _rows(doc, 'scenes'):
                if scene.get('map') and scene['map'] in (slug, name):
                    yield doc_name, scene
        elif doc_name in ('inbox', 'art'):
            for item in _rows(doc, 'items'):
                if item.get('map') == slug:
                    yield doc_name, item


def map_uses(docs, slug, name):
    """Where a map is used: [{doc, where}]."""
    return [
        {'doc': doc_name, 'where': f'{doc_name}: {owner.get("title") or owner.get("id")}'}
        for doc_name, owner in _map_links(docs, slug, name)
    ]


def unlink_map(docs, slug, name):
    """Clear every reference to the map in memory.

    Returns (changed document names, links) where each link is {doc, id, value} so that restoring the
    map can put the same values back.
    """
    links = []
    for doc_name, owner in _map_links(docs, slug, name):
        links.append({'doc': doc_name, 'id': owner.get('id'), 'value': owner['map']})
        owner['map'] = ''
    return sorted({link['doc'] for link in links}), links


def relink_map(docs, links):
    """Undo unlink_map where the record still exists and has no map set. Returns changed document names."""
    changed = set()
    for link in links:
        doc = docs.get(link['doc'])
        field_rows = (
            _rows(doc, 'scenes' if link['doc'].startswith('prep/') else 'items') if doc else []
        )
        for row in field_rows:
            if row.get('id') == link['id'] and not row.get('map'):
                row['map'] = link['value']
                changed.add(link['doc'])
    return sorted(changed)
