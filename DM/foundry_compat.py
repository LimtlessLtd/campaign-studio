"""Foundry build, package version, and relationship rules shared by upgrade modules."""

import re

INVENTORY_FORMAT = 'campaign-studio-foundry-upgrade-inventory'
RELEASES_URL = 'https://foundryvtt.com/releases/'
CORE_RE = re.compile(r'^(\d{1,2})(?:\.(\d{1,4}))?$')
# Before v9 a Foundry version had three parts (0.8.6). Long-lived packages such as libWrapper still
# declare one as their minimum; it only ever bounds compatibility and sorts below every v9+ build.
LEGACY_CORE_RE = re.compile(r'^0\.(\d{1,2})\.\d{1,4}$')
# Foundry library packages may start with an underscore (Custom CSS requires `_CodeMirror`).
# No dots or slashes, so an ID never names another folder.
ID_RE = re.compile(r'^[a-zA-Z0-9_][a-zA-Z0-9_-]{0,99}$')
# The newest Foundry generation Studio's import macro adapts to (`CORE_ADAPTERS` in
# DM/forge/foundry-import-macro.js; a test keeps the two equal). An upgrade targets it unless the GM
# chooses another generation, so a migration does not leave a world Studio cannot import.
IMPORT_GENERATION = 13


def upgrade_target(value):
    """The Foundry generation an upgrade targets: the GM's choice, else IMPORT_GENERATION."""
    if value is None:
        return IMPORT_GENERATION
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 99:
        raise ValueError('The target Foundry generation must be a whole number, such as 13.')
    return value


def core(value):
    match = CORE_RE.fullmatch(str(value or ''))
    if not match:
        raise ValueError(f'Invalid Foundry build: {value}')
    return int(match[1]), int(match[2]) if match[2] is not None else None


def core_bound(value):
    """Parse a compatibility bound: a build (12, 12.343) or a pre-v9 version (0.8.6)."""
    legacy = LEGACY_CORE_RE.fullmatch(str(value or ''))
    return (0, int(legacy[1])) if legacy else core(value)


def core_covers(bound, build, lower):
    if not bound:
        return True
    generation, number = core_bound(bound)
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


def _bound_key(value, upper):
    # Publishers write "4.4.x" to mean any 4.4 release, and Foundry accepts 4.4.4 below it.
    filler = '999999999' if upper else '0'
    return version_key(re.sub(r'(?<=\.)[xX*](?=\.|$)', filler, str(value)))


def version_covers(version, constraint):
    if not isinstance(constraint, dict):
        return True
    try:
        key = version_key(version)
        minimum = _bound_key(constraint['minimum'], False) if constraint.get('minimum') else None
        maximum = _bound_key(constraint['maximum'], True) if constraint.get('maximum') else None
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
