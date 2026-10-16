"""Rate a planned encounter with the D&D 5e (2014) XP thresholds.

The estimate uses creature CR and every imported character's level. A blank or unknown CR makes the
result incomplete rather than silently producing a misleading difficulty.
"""

from fractions import Fraction

# D&D Basic Rules (2014), Experience Points by Challenge Rating.
XP_BY_CR = {
    Fraction(0): 10,  # CR 0 can award 0 or 10 XP; use 10 as a conservative estimate.
    Fraction(1, 8): 25,
    Fraction(1, 4): 50,
    Fraction(1, 2): 100,
    **dict(
        zip(
            range(1, 31),
            (200, 450, 700, 1100, 1800, 2300, 2900, 3900, 5000, 5900, 7200, 8400, 10000, 11500)
            + (13000, 15000, 18000, 20000, 22000, 25000, 33000, 41000, 50000, 62000, 75000)
            + (90000, 105000, 120000, 135000, 155000),
        )
    ),
}
# Easy, medium, hard, deadly XP for one character at each level from 1 to 20.
THRESHOLDS = (
    (25, 50, 75, 100),
    (50, 100, 150, 200),
    (75, 150, 225, 400),
    (125, 250, 375, 500),
    (250, 500, 750, 1100),
    (300, 600, 900, 1400),
    (350, 750, 1100, 1700),
    (450, 900, 1400, 2100),
    (550, 1100, 1600, 2400),
    (600, 1200, 1900, 2800),
    (800, 1600, 2400, 3600),
    (1000, 2000, 3000, 4500),
    (1100, 2200, 3400, 5100),
    (1250, 2500, 3800, 5700),
    (1400, 2800, 4300, 6400),
    (1600, 3200, 4800, 7200),
    (2000, 3900, 5900, 8800),
    (2100, 4200, 6300, 9500),
    (2400, 4900, 7300, 10900),
    (2800, 5700, 8500, 12700),
)
LEVELS = ('easy', 'medium', 'hard', 'deadly')
MULTIPLIERS = (Fraction(1, 2), 1, Fraction(3, 2), 2, Fraction(5, 2), 3, 4, 5)


def parse_cr(value):
    """Return a supported challenge rating, or None for a blank or unrecognised value."""
    if not isinstance(value, str) or len(value) > 8:
        return None
    try:
        cr = Fraction(value.strip())
    except (ValueError, ZeroDivisionError):
        return None
    return cr if cr in XP_BY_CR else None


def multiplier(monsters, party_size):
    """2014 group multiplier, adjusted one step for parties outside the 3–5 range."""
    step = sum(monsters >= start for start in (1, 2, 3, 7, 11, 15))
    step += 1 if party_size < 3 else -1 if party_size >= 6 else 0
    return MULTIPLIERS[max(0, min(step, len(MULTIPLIERS) - 1))]


def rate(creatures, members):
    """Rate complete creature rows against known levels; do not rate a partial encounter."""
    if not members or any(
        type(member.get('level')) is not int or not 1 <= member['level'] <= 20 for member in members
    ):
        return {'status': 'no_party', 'rating': None}
    levels = [member['level'] for member in members]
    limits = {
        name: sum(THRESHOLDS[level - 1][index] for level in levels)
        for index, name in enumerate(LEVELS)
    }
    if not creatures:
        return {
            'status': 'no_creatures',
            'rating': None,
            'party_size': len(levels),
            'thresholds': limits,
        }
    xp = 0
    monsters = 0
    unrated = []
    for creature in creatures:
        cr = parse_cr(creature.get('cr', ''))
        if cr is None:
            unrated.append(creature['name'])
            continue
        xp += XP_BY_CR[cr] * creature['count']
        monsters += creature['count']
    if unrated:
        return {
            'status': 'missing_cr',
            'rating': None,
            'party_size': len(levels),
            'thresholds': limits,
            'unrated': unrated,
        }
    adjusted = int(xp * multiplier(monsters, len(levels)))
    reached = [name for name, limit in limits.items() if adjusted >= limit]
    return {
        'status': 'rated',
        'rating': reached[-1] if reached else 'trivial',
        'party_size': len(levels),
        'xp': xp,
        'adjusted_xp': adjusted,
        'thresholds': limits,
    }
