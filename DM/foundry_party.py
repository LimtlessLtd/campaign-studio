"""Party facts for Foundry character actors: classes, level, armor class and hit points.

dnd5e keeps a character's levels on embedded class items (`!actors.items!<actor>.<item>`), so the
World Library reader hands this module the actor document and its class items.
"""

MAX_CLASSES = 20


def _whole(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if low <= value <= high else None


def character_stats(document, class_items):
    """Level, classes, AC and HP of one character actor; {} when it holds none of them."""
    classes = []
    for item in class_items:
        levels = _whole(_dig(item, 'system', 'levels'), 1, 20)
        if levels and isinstance(item.get('name'), str) and len(classes) < MAX_CLASSES:
            classes.append({'name': item['name'][:80], 'levels': levels})
    classes.sort(key=lambda found: (-found['levels'], found['name'].casefold()))
    level = sum(found['levels'] for found in classes)
    if not level:  # a world exported without class items may still record the total
        level = _whole(_dig(document, 'system', 'details', 'level'), 1, 20) or 0
    armor = _dig(document, 'system', 'attributes', 'ac')
    armor_class = _whole(armor.get('value') if isinstance(armor, dict) else None, 0, 99)
    if armor_class is None and isinstance(armor, dict):
        armor_class = _whole(armor.get('flat'), 0, 99)
    health = _dig(document, 'system', 'attributes', 'hp')
    health = health if isinstance(health, dict) else {}
    stats = {
        'level': level,
        'classes': classes,
        'ac': armor_class,
        'hp': _whole(health.get('max'), 0, 9999),
    }
    return (
        {} if not level and not classes and stats['ac'] is None and stats['hp'] is None else stats
    )


def clean_stats(value):
    """Stats read back from a saved snapshot, bounded the same way as freshly read ones."""
    if not isinstance(value, dict):
        return {}
    found = value.get('classes') if isinstance(value.get('classes'), list) else []
    classes = [
        {'name': entry.get('name'), 'system': {'levels': entry.get('levels')}}
        for entry in found
        if isinstance(entry, dict)
    ]
    document = {
        'system': {
            'details': {'level': value.get('level')},
            'attributes': {'ac': {'value': value.get('ac')}, 'hp': {'max': value.get('hp')}},
        }
    }
    return character_stats(document, classes)


def _dig(document, *keys):
    for key in keys:
        document = document.get(key) if isinstance(document, dict) else None
    return document


def party(actors):
    """Characters in a snapshot that carry stats, with the party's level range for budgets."""
    members = [actor for actor in actors if actor.get('stats') and actor.get('type') == 'character']
    levels = [member['stats']['level'] for member in members if member['stats']['level']]
    return {
        'members': [
            {'id': member['id'], 'name': member['name'], **member['stats']} for member in members
        ],
        'size': len(members),
        'average_level': round(sum(levels) / len(levels), 1) if levels else 0,
    }
