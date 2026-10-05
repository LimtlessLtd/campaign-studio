"""Foundry build, package version, and relationship rules shared by upgrade modules."""

import re

INVENTORY_FORMAT = 'campaign-studio-foundry-upgrade-inventory'
RELEASES_URL = 'https://foundryvtt.com/releases/'
CORE_RE = re.compile(r'^(\d{1,2})(?:\.(\d{1,4}))?$')
ID_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$')


def core(value):
    match = CORE_RE.fullmatch(str(value or ''))
    if not match:
        raise ValueError(f'Invalid Foundry build: {value}')
    return int(match[1]), int(match[2]) if match[2] is not None else None


def core_covers(bound, build, lower):
    if not bound:
        return True
    generation, number = core(bound)
    floor = (generation, number or 0)
    ceiling = (generation, number if number is not None else 9999)
    return build >= floor if lower else build <= ceiling


def compatible(compatibility, build):
    return core_covers(compatibility.get('minimum'), build, True) and core_covers(
        compatibility.get('maximum'), build, False
    )


def is_verified(compatibility, build):
    value = compatibility.get('verified')
    return bool(value) and core_covers(value, build, False)


def version_key(value):
    # Publishers may omit trailing zero components. A suffix sorts below the
    # corresponding plain release, conservatively for dependency constraints.
    match = re.fullmatch(r'v?(\d+(?:\.\d+)*)(?:[-+.]([a-zA-Z][a-zA-Z0-9.-]*))?', str(value or ''))
    if not match:
        raise ValueError(f'Cannot compare package version: {value}')
    numbers = tuple(int(part) for part in match[1].split('.'))
    return numbers[:8] + (0,) * max(0, 8 - len(numbers)), 0 if match[2] else 1, match[2] or ''


def version_covers(version, constraint):
    if not isinstance(constraint, dict):
        return True
    try:
        key = version_key(version)
        minimum = version_key(constraint['minimum']) if constraint.get('minimum') else None
        maximum = version_key(constraint['maximum']) if constraint.get('maximum') else None
    except ValueError:
        return False
    return (minimum is None or key >= minimum) and (maximum is None or key <= maximum)


def relationships(package):
    relationships = package.get('relationships') or {}
    if not isinstance(relationships, dict):
        raise ValueError('Invalid package relationships.')
    requires = relationships.get('requires') or []
    systems = relationships.get('systems') or []
    if not isinstance(requires, list) or not isinstance(systems, list):
        raise ValueError('Invalid package relationships.')
    normalized = []
    for entry in requires:
        if not isinstance(entry, dict) or not ID_RE.fullmatch(str(entry.get('id') or '')):
            raise ValueError('Invalid required package.')
        normalized.append(
            {
                'id': entry['id'],
                'type': entry.get('type') or 'module',
                'compatibility': entry.get('compatibility') or {},
            }
        )
    return normalized, systems
