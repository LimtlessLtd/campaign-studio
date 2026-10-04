"""Read-only Foundry v12 inventory analysis and package compatibility planning.

Only Foundry's public release and package directory pages are consulted. No world
database or installed package is changed here; Foundry must migrate a test clone.
"""

import datetime
import copy
import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import config
import foundry_backup
import storage

INVENTORY_FORMAT = 'campaign-studio-foundry-upgrade-inventory'
CORE_RE = re.compile(r'^(\d{1,2})(?:\.(\d{1,4}))?$')
ID_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$')
RELEASES_URL = 'https://foundryvtt.com/releases/'
PACKAGE_URL = 'https://foundryvtt.com/packages/'
MAX_PAGE = 3 * 1024 * 1024
MAX_MANIFEST = 512 * 1024


def _core(value):
    match = CORE_RE.fullmatch(str(value or ''))
    if not match:
        raise ValueError(f'Invalid Foundry build: {value}')
    return int(match[1]), int(match[2]) if match[2] is not None else None


def _core_covers(bound, build, lower):
    if not bound:
        return True
    generation, number = _core(bound)
    floor = (generation, number or 0)
    ceiling = (generation, number if number is not None else 9999)
    return build >= floor if lower else build <= ceiling


def _compatible(compatibility, build):
    return _core_covers(compatibility.get('minimum'), build, True) and _core_covers(
        compatibility.get('maximum'), build, False
    )


def _verified(compatibility, build):
    value = compatibility.get('verified')
    return bool(value) and _core_covers(value, build, False)


def _version_key(value):
    # Publishers may omit trailing zero components. A suffix sorts below the
    # corresponding plain release, conservatively for dependency constraints.
    match = re.fullmatch(r'v?(\d+(?:\.\d+)*)(?:[-+.]([a-zA-Z][a-zA-Z0-9.-]*))?', str(value or ''))
    if not match:
        raise ValueError(f'Cannot compare package version: {value}')
    numbers = tuple(int(part) for part in match[1].split('.'))
    return numbers[:8] + (0,) * max(0, 8 - len(numbers)), 0 if match[2] else 1, match[2] or ''


def _version_covers(version, constraint):
    if not isinstance(constraint, dict):
        return True
    try:
        key = _version_key(version)
        minimum = _version_key(constraint['minimum']) if constraint.get('minimum') else None
        maximum = _version_key(constraint['maximum']) if constraint.get('maximum') else None
    except ValueError:
        return False
    return (minimum is None or key >= minimum) and (maximum is None or key <= maximum)


def _validate_url(url, official=False):
    parsed = urllib.parse.urlsplit(str(url))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Package metadata URL must use public HTTPS.')
    if official and parsed.hostname != 'foundryvtt.com':
        raise ValueError('Expected a Foundry directory URL.')
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise ValueError('Package metadata URL resolved to a private address.')
    return parsed


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _fetch(url, limit=MAX_PAGE, official=False):
    _validate_url(url, official=official)
    opener = urllib.request.build_opener(_SafeRedirect)
    request = urllib.request.Request(url, headers={'User-Agent': 'CampaignStudio/upgrade-report'})
    with opener.open(request, timeout=12) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('Package metadata exceeds the size limit.')
        return body.decode('utf-8')


def _plain(fragment):
    return html.unescape(re.sub(r'<[^>]+>', ' ', fragment)).strip()


def stable_builds(page):
    builds = []
    for row in re.findall(r'<li class="article release flexrow">(.*?)</li>', page, re.S):
        match = re.search(r'href="/releases/(\d+\.\d+)"', row)
        if match and re.search(r'class="release-tag stable"', row):
            builds.append(match[1])
    if not builds:
        raise ValueError('Foundry stable release listing could not be read.')
    return sorted(set(builds), key=lambda value: _core(value), reverse=True)


def package_releases(page, package_id):
    rows = re.findall(r'<li class="package-version flexrow">(.*?)</li>', page, re.S)
    releases = []
    for row in rows:
        version_match = re.search(r'<h4 class="package-title">\s*Version\s+([^<]+)</h4>', row)
        manifest_match = re.search(r'<a href="([^"]+)" title="Manifest Installation URL"', row)
        range_match = re.search(
            r'Foundry Version\s+(\d+(?:\.\d+)?)(\+|\s*-\s*(\d+(?:\.\d+)?))', _plain(row)
        )
        verified_match = re.search(r'\(Verified\s+(\d+(?:\.\d+)?)\)', _plain(row))
        if not (version_match and manifest_match and range_match):
            continue
        releases.append(
            {
                'id': package_id,
                'version': html.unescape(version_match[1]).strip(),
                'manifest': html.unescape(manifest_match[1]),
                'compatibility': {
                    'minimum': range_match[1],
                    'maximum': range_match[3] or '',
                    'verified': verified_match[1] if verified_match else '',
                },
            }
        )
    if rows and not releases:
        raise ValueError(f'No usable directory releases for {package_id}.')
    return releases


def _relationships(package):
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


def _inventory(inventory):
    if (
        not isinstance(inventory, dict)
        or inventory.get('format') != INVENTORY_FORMAT
        or inventory.get('schema') != 2
    ):
        raise ValueError('Import a v12 GM upgrade inventory export.')
    world = inventory.get('world')
    system = inventory.get('system')
    modules = inventory.get('modules')
    enabled_ids = inventory.get('enabledModuleIds')
    if not isinstance(world, dict) or not isinstance(system, dict) or not isinstance(modules, list):
        raise ValueError('Invalid upgrade inventory.')
    if (
        not isinstance(enabled_ids, list)
        or len(enabled_ids) != len(set(str(value) for value in enabled_ids))
        or any(not isinstance(value, str) or not ID_RE.fullmatch(value) for value in enabled_ids)
    ):
        raise ValueError('The GM export must include valid module configuration IDs.')
    selected = config.world_info(config.settings().get('world_path'))
    if (
        world.get('id') != selected['id']
        or world.get('system') != selected['system']
        or world.get('coreVersion') != selected['foundry_version']
        or system.get('id') != selected['system']
    ):
        raise ValueError('The GM inventory does not match the selected local world.')
    if _core(world['coreVersion'])[0] != 12:
        raise ValueError('The upgrade report currently accepts Foundry v12 inventories only.')
    if len(modules) > 500 or not ID_RE.fullmatch(str(system.get('id') or '')):
        raise ValueError('Upgrade inventory has too many or invalid packages.')
    seen = set()
    for package in [system, *modules]:
        package_id = package.get('id') if isinstance(package, dict) else None
        if not ID_RE.fullmatch(str(package_id or '')) or package_id in seen:
            raise ValueError('Duplicate or invalid package in GM inventory.')
        seen.add(package_id)
        if not isinstance(package.get('version'), str) or not package['version']:
            raise ValueError('GM inventory is missing an installed package version.')
        _relationships(package)
        if package is not system and not isinstance(package.get('enabled'), bool):
            raise ValueError('GM inventory must record enabled module states.')
    if not set(enabled_ids).issubset({package['id'] for package in modules}):
        raise ValueError('An enabled module is missing from the backup and GM inventory.')
    return selected


def _complete_inventory(inventory, backup_path):
    """Add installed packages absent from v12's world-eligible game.modules Map."""
    if not isinstance(inventory, dict) or inventory.get('schema') != 2:
        raise ValueError('Re-export the inventory with the current v12 GM macro.')
    enabled = inventory.get('enabledModuleIds')
    if (
        not isinstance(enabled, list)
        or any(not isinstance(value, str) or not ID_RE.fullmatch(value) for value in enabled)
        or len(enabled) != len(set(enabled))
    ):
        raise ValueError('The GM export is missing module configuration.')
    result = copy.deepcopy(inventory)
    modules = result.get('modules')
    if not isinstance(modules, list):
        raise ValueError('Invalid upgrade inventory.')
    if any(
        not isinstance(item, dict) or not ID_RE.fullmatch(str(item.get('id') or ''))
        for item in modules
    ):
        raise ValueError('Invalid package in GM inventory.')
    by_id = {item['id']: item for item in modules}
    if len(by_id) != len(modules):
        raise ValueError('Duplicate or invalid package in GM inventory.')
    root = Path(backup_path) / 'User Data' / 'Data' / 'modules'
    missing_from_game = set()
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            manifest_path = folder / 'module.json'
            if not folder.is_dir():
                continue
            if not manifest_path.is_file():
                raise ValueError(f'Installed module manifest is missing: {folder.name}')
            if manifest_path.stat().st_size > MAX_MANIFEST:
                raise ValueError(f'Installed module manifest is too large: {folder.name}')
            data = json.loads(manifest_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('id') != folder.name:
                raise ValueError(f'Invalid installed module manifest: {folder.name}')
            package_id = data['id']
            if package_id in by_id:
                if by_id[package_id].get('version') != data.get('version'):
                    raise ValueError(f'GM export and backup disagree on {package_id} version.')
                continue
            entry = {
                'id': package_id,
                'title': data.get('title') or package_id,
                'version': data.get('version'),
                'manifest': data.get('manifest') or '',
                'compatibility': data.get('compatibility') or {},
                'relationships': data.get('relationships') or {},
                'locked': bool(data.get('locked')),
                'enabled': package_id in enabled,
                'inventory_source': 'backup module manifest',
            }
            modules.append(entry)
            by_id[package_id] = entry
            if package_id in enabled:
                missing_from_game.add(package_id)
    result['activation_discrepancies'] = sorted(
        missing_from_game
        | {item['id'] for item in modules if item.get('enabled') != (item['id'] in enabled)}
    )
    active_in_game = {item['id'] for item in modules if item.get('enabled') is True}
    conservatively_enabled = set(enabled) | active_in_game
    for item in modules:
        item['enabled'] = item['id'] in conservatively_enabled
    return result


def _directory(package_id):
    url = PACKAGE_URL + urllib.parse.quote(package_id, safe='') + '/'
    try:
        page = _fetch(url, official=True)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {'status': 'unlisted', 'url': url, 'releases': []}
        return {'status': 'unknown', 'url': url, 'releases': [], 'error': str(error)}
    except (OSError, ValueError, UnicodeError) as error:
        return {'status': 'unknown', 'url': url, 'releases': [], 'error': str(error)}
    try:
        releases = package_releases(page, package_id)
    except ValueError as error:
        return {'status': 'unknown', 'url': url, 'releases': [], 'error': str(error)}
    return {'status': 'listed', 'url': url, 'releases': releases}


def collect_catalog(inventory):
    """Read the official directory and release manifests; unresolved data stays unknown."""
    builds = stable_builds(_fetch(RELEASES_URL, official=True))
    original = _core(inventory['world']['coreVersion'])
    builds = [value for value in builds if _core(value) >= original]
    if not builds:
        raise ValueError('No stable Foundry build is available at or above this v12 build.')
    catalog = {}
    queue = [inventory['system']['id']] + [m['id'] for m in inventory['modules'] if m['enabled']]
    manifest_reads = 0
    while queue:
        package_id = queue.pop(0)
        if package_id in catalog:
            continue
        if len(catalog) >= 150:
            raise ValueError('Dependency catalogue exceeds the package limit.')
        entry = _directory(package_id)
        catalog[package_id] = entry
        if entry['status'] != 'listed':
            continue
        if len(entry['releases']) > 200:
            entry['status'] = 'unknown'
            entry['error'] = 'Directory release history exceeds the review limit.'
            entry['releases'] = []
            continue
        for release in entry['releases']:
            if not any(_compatible(release['compatibility'], _core(build)) for build in builds):
                continue
            if manifest_reads >= 600:
                release['error'] = 'Manifest review limit reached; compatibility remains unknown.'
                continue
            manifest_reads += 1
            try:
                manifest = json.loads(_fetch(release['manifest'], MAX_MANIFEST))
                if not isinstance(manifest, dict) or manifest.get('id') != package_id:
                    raise ValueError('Manifest package ID differs from the directory.')
                release['requires'], release['systems'] = _relationships(manifest)
                manifest_compat = manifest.get('compatibility') or {}
                if not isinstance(manifest_compat, dict):
                    raise ValueError('Invalid manifest compatibility.')
                # Apply both directory and manifest hard bounds. Directory data can
                # sidegrade, so retain each source for review.
                release['manifest_compatibility'] = manifest_compat
                release['manifest_version'] = manifest.get('version', '')
                if manifest.get('version') != release['version']:
                    raise ValueError('Manifest release version differs from the directory.')
                for dependency in release['requires']:
                    if dependency['type'] == 'module' and dependency['id'] not in catalog:
                        queue.append(dependency['id'])
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
                release['error'] = str(error)
    for module in inventory['modules']:
        if module['id'] not in catalog:
            entry = _directory(module['id'])
            entry['releases'] = []  # Disabled packages do not constrain the search.
            catalog[module['id']] = entry
    return {'builds': builds, 'packages': catalog, 'source': RELEASES_URL}


def _release_compatible(release, build):
    try:
        _version_key(release['version'])
        return (
            not release.get('error')
            and _compatible(release['compatibility'], build)
            and _compatible(release.get('manifest_compatibility') or {}, build)
        )
    except ValueError:
        return False


def _system_allowed(release, system_id, system_version):
    systems = release.get('systems') or []
    if not systems:
        return True
    return any(
        isinstance(entry, dict)
        and entry.get('id') == system_id
        and _version_covers(system_version, entry.get('compatibility') or {})
        for entry in systems
    )


def _solve(build, roots, catalog, system_id, approved_dependencies):
    problems = set()
    build_key = _core(build)
    visited = 0

    def walk(pending, selected, constraints):
        nonlocal visited
        visited += 1
        if visited > 20000:
            problems.add('Dependency search limit reached; no safe recommendation for this build.')
            return None
        if not pending:
            if any(
                not all(_version_covers(selected[key]['version'], item) for item in values)
                for key, values in constraints.items()
            ):
                problems.add(
                    'Selected system or module version conflicts with a required dependency.'
                )
                return None
            system_version = selected[system_id]['version']
            if all(
                _system_allowed(release, system_id, system_version) for release in selected.values()
            ):
                return selected
            problems.add('No selected module release supports the selected system version.')
            return None
        package_id = pending[0]
        remaining = pending[1:]
        if package_id in selected:
            if all(
                _version_covers(selected[package_id]['version'], constraint)
                for constraint in constraints.get(package_id, [])
            ):
                return walk(remaining, selected, constraints)
            problems.add(f'{package_id}: required package version conflicts with another release.')
            return None
        entry = catalog.get(package_id)
        if not entry or entry['status'] != 'listed':
            problems.add(
                f'{package_id}: directory status is {entry["status"] if entry else "unknown"}.'
            )
            return None
        choices = [
            release
            for release in entry['releases']
            if _release_compatible(release, build_key)
            and all(_version_covers(release['version'], c) for c in constraints.get(package_id, []))
        ]
        choices.sort(key=lambda release: _version_key(release['version']), reverse=True)
        if not choices:
            problems.add(f'{package_id}: no eligible release for Foundry {build}.')
        for release in choices:
            next_pending = list(remaining)
            next_constraints = {key: list(value) for key, value in constraints.items()}
            invalid = False
            for dependency in release.get('requires') or []:
                dep_id = dependency['id']
                if dependency['type'] == 'system':
                    if dep_id != system_id:
                        problems.add(f'{package_id} requires a different game system: {dep_id}.')
                        invalid = True
                    else:
                        next_constraints.setdefault(system_id, []).append(
                            dependency['compatibility']
                        )
                    continue
                if dependency['type'] != 'module':
                    problems.add(f'{package_id} has an unsupported dependency type.')
                    invalid = True
                    continue
                if dep_id.casefold() == 'plutonium':
                    problems.add(f'{package_id} requires excluded Plutonium.')
                    invalid = True
                elif dep_id not in roots and dep_id not in approved_dependencies:
                    problems.add(
                        f'{package_id} requires {dep_id}; GM approval to enable this dependency is needed.'
                    )
                    invalid = True
                else:
                    next_constraints.setdefault(dep_id, []).append(dependency['compatibility'])
                    if dep_id not in next_pending:
                        next_pending.append(dep_id)
            if invalid:
                continue
            result = walk(next_pending, {**selected, package_id: release}, next_constraints)
            if result:
                return result
        return None

    solution = walk(list(roots), {}, {})
    return solution, sorted(problems)


def analyze(inventory, catalog, disabled_modules=(), approved_dependencies=()):
    """Choose newest full match without silently dropping an eligible enabled module."""
    if not isinstance(disabled_modules, (list, tuple)) or not isinstance(
        approved_dependencies, (list, tuple)
    ):
        raise ValueError('GM package choices must be lists of package IDs.')
    if any(
        not isinstance(value, str) or not ID_RE.fullmatch(value)
        for value in [*disabled_modules, *approved_dependencies]
    ):
        raise ValueError('Invalid GM package choice.')
    system_id = inventory['system']['id']
    modules = {module['id']: module for module in inventory['modules']}
    disabled = set(disabled_modules)
    approved = set(approved_dependencies)
    if not disabled <= {key for key, module in modules.items() if module['enabled']}:
        raise ValueError('Only originally enabled modules can be explicitly disabled.')
    if not approved <= set(catalog['packages']):
        raise ValueError('Only discovered modules can be approved as dependencies.')
    if disabled & approved:
        raise ValueError('A module cannot be both disabled and approved as a dependency.')
    if system_id in approved or 'plutonium' in {key.casefold() for key in approved}:
        raise ValueError('The game system and Plutonium cannot be approved as module dependencies.')
    decisions = []
    retained = []
    for module in modules.values():
        entry = catalog['packages'].get(module['id'], {'status': 'unknown', 'releases': []})
        reason = ''
        if not module['enabled']:
            reason = 'Originally disabled; remains disabled.'
        elif module['id'].casefold() == 'plutonium':
            reason = 'Plutonium is excluded from automatic migration.'
        elif entry['status'] != 'listed':
            reason = f'Directory status {entry["status"]}; not approved for automatic migration.'
        elif module['id'] in disabled:
            reason = 'GM explicitly chose to disable this module in the clone.'
        else:
            retained.append(module['id'])
        decisions.append(
            {
                'id': module['id'],
                'original_version': module['version'],
                'original_enabled': module['enabled'],
                'directory_status': entry['status'],
                'directory_url': entry.get('url', ''),
                'proposed_enabled': not reason,
                'disabled_reason': reason,
                'original_manifest': module.get('manifest') or '',
                'original_compatibility': module.get('compatibility') or {},
                'original_requires': _relationships(module)[0],
                'original_systems': _relationships(module)[1],
                'locked': bool(module.get('locked')),
            }
        )
    roots = [system_id, *retained]
    candidates = []
    best = None
    verified_best = None
    for build in catalog['builds']:
        solution, blockers = _solve(build, roots, catalog['packages'], system_id, approved)
        if not solution:
            for package_id in roots:
                entry = catalog['packages'].get(package_id)
                if not entry or entry['status'] != 'listed':
                    message = f'{package_id}: directory status is {entry["status"] if entry else "unknown"}.'
                elif not any(
                    _release_compatible(release, _core(build)) for release in entry['releases']
                ):
                    message = f'{package_id}: no eligible release for Foundry {build}.'
                else:
                    continue
                if message not in blockers:
                    blockers.append(message)
            blockers.sort()
        verified = bool(solution) and all(
            _verified(release['compatibility'], _core(build))
            and _verified(
                release.get('manifest_compatibility') or release['compatibility'], _core(build)
            )
            for release in solution.values()
        )
        candidates.append(
            {
                'build': build,
                'full_match': bool(solution),
                'all_verified': verified,
                'blockers': [] if solution else blockers,
            }
        )
        if solution and best is None:
            best = (build, solution)
        if verified and verified_best is None:
            verified_best = build
    selected = best[1] if best else {}
    for decision in decisions:
        release = selected.get(decision['id'])
        if best is None and decision['proposed_enabled']:
            decision['proposed_enabled'] = None
        if release and not decision['original_enabled'] and decision['id'] in approved:
            decision['proposed_enabled'] = True
            decision['disabled_reason'] = ''
            decision['activation_reason'] = 'GM approved this required dependency for the clone.'
        decision['selected_version'] = release['version'] if release else None
        decision['selected_manifest'] = release['manifest'] if release else None
        decision['selected_compatibility'] = release['compatibility'] if release else None
        decision['selected_requires'] = release.get('requires', []) if release else []
    locked_changes = []
    if (
        inventory['system'].get('locked')
        and system_id in selected
        and selected[system_id]['version'] != inventory['system']['version']
    ):
        locked_changes.append(system_id)
    locked_changes.extend(
        item['id']
        for item in decisions
        if item['locked']
        and item['selected_version']
        and item['selected_version'] != item['original_version']
    )
    return {
        'world': inventory['world'],
        'inventory_exported_at': inventory.get('exportedAt'),
        'system': {
            'id': system_id,
            'original_version': inventory['system']['version'],
            'original_manifest': inventory['system'].get('manifest') or '',
            'original_compatibility': inventory['system'].get('compatibility') or {},
            'original_requires': _relationships(inventory['system'])[0],
            'locked': bool(inventory['system'].get('locked')),
            'directory_status': catalog['packages'].get(system_id, {}).get('status', 'unknown'),
            'selected_version': selected[system_id]['version'] if system_id in selected else None,
            'selected_manifest': selected[system_id]['manifest'] if system_id in selected else None,
            'selected_compatibility': selected[system_id]['compatibility']
            if system_id in selected
            else None,
            'selected_requires': selected[system_id].get('requires', [])
            if system_id in selected
            else [],
        },
        'modules': decisions,
        'dependencies': [
            {
                'id': key,
                'version': release['version'],
                'manifest': release['manifest'],
                'requires': release.get('requires', []),
            }
            for key, release in selected.items()
            if key not in roots
        ],
        'candidates': candidates,
        'recommended_build': best[0] if best else None,
        'newest_all_verified_build': verified_best,
        'needs_clone_testing': bool(best) and verified_best != best[0],
        'requires_gm_choice': bool(disabled or approved)
        or bool(locked_changes)
        or any(item['original_enabled'] and item['disabled_reason'] for item in decisions),
        'locked_changes': locked_changes,
        'approved_dependencies': sorted(approved),
        'explicitly_disabled': sorted(disabled),
        'catalog_evidence': catalog['packages'],
        'source': catalog.get('source', RELEASES_URL),
    }


def report(inventory, backup_path, disabled_modules=(), approved_dependencies=(), catalog=None):
    backup = foundry_backup.verify(backup_path)
    completed_inventory = _complete_inventory(inventory, backup['path'])
    selected_world = _inventory(completed_inventory)
    if (
        backup['world']['id'] != selected_world['id']
        or backup['world']['core_version'] != selected_world['foundry_version']
        or backup['world']['system'] != selected_world['system']
    ):
        raise ValueError('The verified backup does not match the inventory world.')
    catalog = catalog if catalog is not None else collect_catalog(completed_inventory)
    result = analyze(completed_inventory, catalog, disabled_modules, approved_dependencies)
    result['backup_path'] = backup['path']
    result['activation_discrepancies'] = completed_inventory['activation_discrepancies']
    if result['activation_discrepancies']:
        result['requires_gm_choice'] = True
        result['needs_clone_testing'] = True
    result['generated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result['migration_ready'] = False
    # Keep the export and report next to the verified backup, outside User Data.
    folder = Path(backup['path'])
    suffix = (
        datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
        + '-'
        + uuid.uuid4().hex[:8]
    )
    inventory_path = folder / f'upgrade-inventory-{suffix}.json'
    report_path = folder / f'upgrade-report-{suffix}.json'
    with inventory_path.open('x', encoding='utf-8') as stream:
        json.dump(inventory, stream, indent=2, ensure_ascii=False)
    result['inventory_path'] = str(inventory_path)
    result['inventory_sha256'] = storage.sha256_file(inventory_path)
    result['report_path'] = str(report_path)
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    return result


def _sidecar(path, backup, prefix, limit):
    item = foundry_backup.absolute_folder(path, 'Upgrade evidence file')
    if item.parent != backup or not item.name.startswith(prefix) or item.suffix != '.json':
        raise ValueError('Upgrade evidence must be a JSON sidecar beside the backup.')
    if not item.is_file() or item.stat().st_size > limit:
        raise ValueError('Upgrade evidence is missing or exceeds its size limit.')
    value = json.loads(item.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Invalid upgrade evidence.')
    return item, value


def prepare_clone(
    report_path,
    restore_receipt_path,
    destination,
    confirmed_v12_restore=False,
    confirmed_report=False,
):
    """Copy a reviewed, verified v12 backup to a separate pre-migration clone."""
    if confirmed_v12_restore is not True or confirmed_report is not True:
        raise ValueError('Confirm the v12 restore test and review the compatibility report first.')
    selected_report = foundry_backup.absolute_folder(report_path, 'Compatibility report')
    if not selected_report.is_file() or selected_report.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('Select a saved compatibility report.')
    preliminary = json.loads(selected_report.read_text(encoding='utf-8'))
    if not isinstance(preliminary, dict):
        raise ValueError('Invalid compatibility report.')
    backup = foundry_backup.verify(preliminary.get('backup_path'))
    backup_path = Path(backup['path'])
    report_file, report_data = _sidecar(
        selected_report, backup_path, 'upgrade-report-', 20 * 1024 * 1024
    )
    inventory_file, inventory_data = _sidecar(
        report_data.get('inventory_path'), backup_path, 'upgrade-inventory-', 2 * 1024 * 1024
    )
    if (
        report_data.get('report_path') != str(report_file)
        or report_data.get('backup_path') != str(backup_path)
        or inventory_data.get('schema') != 2
        or report_data.get('inventory_sha256') != storage.sha256_file(inventory_file)
    ):
        raise ValueError('The compatibility report and v12 inventory do not match.')
    world = report_data.get('world') or {}
    if (
        not isinstance(world, dict)
        or world.get('id') != backup['world']['id']
        or world.get('system') != backup['world']['system']
        or world.get('coreVersion') != backup['world']['core_version']
    ):
        raise ValueError('The compatibility report is for a different backup world.')
    build = report_data.get('recommended_build')
    if not build or _core(build) <= _core(world['coreVersion']):
        raise ValueError('The report has no newer full-match Foundry build to prepare.')
    if not any(
        item.get('build') == build and item.get('full_match') is True
        for item in report_data.get('candidates') or []
        if isinstance(item, dict)
    ):
        raise ValueError('The selected Foundry build is not a full match in the report.')
    modules = report_data.get('modules')
    system = report_data.get('system')
    if not isinstance(system, dict) or not system.get('selected_version'):
        raise ValueError('The report has no selected game system release.')
    if not isinstance(modules, list) or any(
        not isinstance(item, dict)
        or (item.get('original_enabled') and item.get('proposed_enabled') is None)
        or (item.get('proposed_enabled') is True and not item.get('selected_version'))
        or (
            item.get('original_enabled')
            and item.get('proposed_enabled') is False
            and not item.get('disabled_reason')
        )
        for item in modules
    ):
        raise ValueError('The report has unresolved module decisions.')
    receipt_file, receipt = _sidecar(restore_receipt_path, backup_path, 'restore-test-', 64 * 1024)
    restore = foundry_backup.absolute_folder(receipt.get('restore_path'), 'Restore test copy')
    backup_manifest_hash = storage.sha256_file(backup_path / foundry_backup.MANIFEST)
    if (
        receipt.get('format') != 'campaign-studio-foundry-restore-test'
        or receipt.get('backup_path') != str(backup_path)
        or receipt.get('backup_manifest_sha256') != backup_manifest_hash
        or receipt.get('world') != backup['world']
        or not restore.is_dir()
    ):
        raise ValueError('The restore-test receipt does not match this verified backup.')
    backup_manifest = foundry_backup.read_manifest(backup_path)
    original_user_data = foundry_backup.absolute_folder(
        backup_manifest.get('source_user_data'), 'Original Foundry User Data'
    )
    if (
        foundry_backup.within(restore, original_user_data)
        or foundry_backup.within(restore, backup_path)
        or foundry_backup.within(backup_path, restore)
    ):
        raise ValueError('The restore test copy must be separate from live data and the backup.')
    relative_world = storage.manifest_path(backup['world']['manifest_path'])
    restored_world = json.loads((restore / relative_world).read_text(encoding='utf-8'))
    if (
        not isinstance(restored_world, dict)
        or restored_world.get('id') != world['id']
        or restored_world.get('system') != world['system']
        or _core(restored_world.get('coreVersion'))[0] != 12
    ):
        raise ValueError('The restore test copy is no longer the original v12 world.')
    target = foundry_backup.absolute_folder(destination, 'Upgrade clone destination')
    if foundry_backup.within(target, restore) or foundry_backup.within(restore, target):
        raise ValueError('The upgrade clone must be separate from the restore test copy.')
    clone = foundry_backup.rehearse(str(backup_path), str(target))
    to_disable = [
        {'id': item['id'], 'reason': item['disabled_reason']}
        for item in modules
        if item.get('original_enabled') and item.get('proposed_enabled') is False
    ]
    to_enable = [
        item['id']
        for item in modules
        if not item.get('original_enabled') and item.get('proposed_enabled') is True
    ]
    plan = {
        'format': 'campaign-studio-foundry-clone-plan',
        'status': 'awaiting_v12_module_review',
        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'world': world,
        'target_build': build,
        'backup_path': str(backup_path),
        'report_path': str(report_file),
        'report_sha256': storage.sha256_file(report_file),
        'restore_receipt_path': str(receipt_file),
        'clone_path': clone['path'],
        'clone_receipt_path': clone['receipt_path'],
        'disable_in_v12': to_disable,
        'enable_after_review': to_enable,
        'locked_changes': report_data.get('locked_changes') or [],
        'selected_system': report_data['system'],
        'selected_modules': [
            {
                'id': item['id'],
                'version': item.get('selected_version'),
                'manifest': item.get('selected_manifest'),
            }
            for item in modules
            if item.get('proposed_enabled') is True
        ],
        'selected_dependencies': report_data.get('dependencies') or [],
        'migration_ready': False,
    }
    plan_path = backup_path / f'upgrade-clone-plan-{uuid.uuid4().hex}.json'
    with plan_path.open('x', encoding='utf-8') as stream:
        json.dump(plan, stream, indent=2, ensure_ascii=False)
    plan['plan_path'] = str(plan_path)
    return plan


def _installed_clone_packages(clone, kind):
    """Read package manifests in the clone without following links out of it."""
    root = clone / 'Data' / kind
    if (
        not root.is_dir()
        or root.is_symlink()
        or getattr(root, 'is_junction', lambda: False)()
        or not foundry_backup.within(root.resolve(), clone)
    ):
        raise ValueError(f'The clone has no safe {kind} directory.')
    packages = {}
    filename = 'system.json' if kind == 'systems' else 'module.json'
    for folder in root.iterdir():
        if (
            not folder.is_dir()
            or folder.is_symlink()
            or getattr(folder, 'is_junction', lambda: False)()
            or not foundry_backup.within(folder.resolve(), clone)
            or not ID_RE.fullmatch(folder.name)
        ):
            raise ValueError(f'Invalid or linked package folder in the clone: {folder.name}')
        manifest = folder / filename
        if (
            manifest.is_symlink()
            or not manifest.is_file()
            or not foundry_backup.within(manifest.resolve(), clone)
            or manifest.stat().st_size > MAX_MANIFEST
        ):
            raise ValueError(f'Missing, linked or oversized clone manifest: {folder.name}')
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if (
            not isinstance(data, dict)
            or data.get('id') != folder.name
            or not isinstance(data.get('version'), str)
            or not data['version']
        ):
            raise ValueError(f'Invalid clone package manifest: {folder.name}')
        packages[folder.name] = data['version']
    return packages


def review_clone(plan_path, inventory, confirmed_clone=False):
    """Compare a new GM v12 export with the saved plan and untouched package versions.

    The export's origin is GM-attested: Foundry's inventory does not expose a
    trustworthy User Data path. A match is a gate for later clone work, not proof
    that migration or runtime module behavior will succeed.
    """
    if confirmed_clone is not True:
        raise ValueError('Confirm that this inventory was exported from the isolated v12 clone.')
    selected_plan = foundry_backup.absolute_folder(plan_path, 'Clone plan')
    if not selected_plan.is_file() or selected_plan.stat().st_size > 256 * 1024:
        raise ValueError('Select a saved clone plan.')
    preliminary = json.loads(selected_plan.read_text(encoding='utf-8'))
    if not isinstance(preliminary, dict):
        raise ValueError('Invalid clone plan.')
    backup = foundry_backup.verify(preliminary.get('backup_path'))
    backup_path = Path(backup['path'])
    plan_file, plan = _sidecar(selected_plan, backup_path, 'upgrade-clone-plan-', 256 * 1024)
    report_file, report_data = _sidecar(
        plan.get('report_path'), backup_path, 'upgrade-report-', 20 * 1024 * 1024
    )
    receipt_file, receipt = _sidecar(
        plan.get('clone_receipt_path'), backup_path, 'restore-test-', 64 * 1024
    )
    clone = foundry_backup.absolute_folder(plan.get('clone_path'), 'Upgrade clone')
    backup_manifest = foundry_backup.read_manifest(backup_path)
    original_root = foundry_backup.absolute_folder(
        backup_manifest.get('source_user_data'), 'Original Foundry User Data'
    )
    report_modules = report_data.get('modules')
    if not isinstance(report_modules, list) or any(
        not isinstance(item, dict) for item in report_modules
    ):
        raise ValueError('The saved compatibility report has invalid module decisions.')
    expected_selected = [
        {
            'id': item['id'],
            'version': item.get('selected_version'),
            'manifest': item.get('selected_manifest'),
        }
        for item in report_modules
        if item.get('proposed_enabled') is True
    ]
    expected_disabled = [
        {'id': item['id'], 'reason': item['disabled_reason']}
        for item in report_modules
        if item.get('original_enabled') and item.get('proposed_enabled') is False
    ]
    expected_later = [
        item['id']
        for item in report_modules
        if not item.get('original_enabled') and item.get('proposed_enabled') is True
    ]
    plan_world = plan.get('world')
    if (
        plan.get('format') != 'campaign-studio-foundry-clone-plan'
        or plan.get('status') != 'awaiting_v12_module_review'
        or plan.get('backup_path') != str(backup_path)
        or plan.get('report_sha256') != storage.sha256_file(report_file)
        or plan.get('target_build') != report_data.get('recommended_build')
        or not isinstance(plan_world, dict)
        or plan_world != report_data.get('world')
        or plan_world.get('id') != backup['world']['id']
        or plan_world.get('system') != backup['world']['system']
        or plan_world.get('coreVersion') != backup['world']['core_version']
        or plan.get('selected_system') != report_data.get('system')
        or plan.get('selected_modules') != expected_selected
        or plan.get('selected_dependencies') != (report_data.get('dependencies') or [])
        or plan.get('disable_in_v12') != expected_disabled
        or plan.get('enable_after_review') != expected_later
        or plan.get('migration_ready') is not False
        or receipt.get('format') != 'campaign-studio-foundry-restore-test'
        or receipt.get('backup_path') != str(backup_path)
        or receipt.get('backup_manifest_sha256')
        != storage.sha256_file(backup_path / foundry_backup.MANIFEST)
        or receipt.get('restore_path') != str(clone)
        or receipt.get('world') != backup['world']
        or not clone.is_dir()
        or foundry_backup.within(clone, original_root)
        or foundry_backup.within(clone, backup_path)
        or foundry_backup.within(backup_path, clone)
    ):
        raise ValueError('The clone plan, report, receipt and verified backup do not match.')
    if (
        not isinstance(inventory, dict)
        or len(json.dumps(inventory).encode('utf-8')) > 2 * 1024 * 1024
    ):
        raise ValueError('Import a v12 clone inventory under 2 MB.')
    _inventory(inventory)
    world = plan['world']
    if any(
        inventory['world'].get(key) != world.get(key) for key in ('id', 'system', 'coreVersion')
    ):
        raise ValueError('The inventory is for a different world or Foundry build.')
    world_manifest = clone / storage.manifest_path(backup['world']['manifest_path'])
    if (
        world_manifest.is_symlink()
        or not world_manifest.is_file()
        or not foundry_backup.within(world_manifest.resolve(), clone)
        or world_manifest.stat().st_size > MAX_MANIFEST
    ):
        raise ValueError('The clone world manifest is missing or linked.')
    clone_world = json.loads(world_manifest.read_text(encoding='utf-8'))
    if not isinstance(clone_world, dict):
        raise ValueError('Invalid clone world manifest.')
    systems = _installed_clone_packages(clone, 'systems')
    modules = _installed_clone_packages(clone, 'modules')
    expected = {item['id'] for item in plan['selected_modules']} - set(plan['enable_after_review'])
    configured = set(inventory['enabledModuleIds'])
    active = {item['id'] for item in inventory['modules'] if item['enabled']}
    exported = {item['id']: item['version'] for item in inventory['modules']}
    problems = []
    if (
        clone_world.get('id') != world['id']
        or clone_world.get('system') != world['system']
        or clone_world.get('coreVersion') != world['coreVersion']
    ):
        problems.append('The clone world manifest no longer describes the original v12 world.')
    if (
        backup['world'].get('system_version')
        and clone_world.get('systemVersion') != backup['world']['system_version']
    ):
        problems.append('The clone world manifest no longer has its original game system version.')
    original_system = report_data['system']
    if systems.get(world['system']) != original_system['original_version']:
        problems.append('The clone game system no longer has its original v12 version.')
    if inventory['system']['version'] != systems.get(world['system']):
        problems.append('The GM system version disagrees with the clone system manifest.')
    original_modules = {item['id']: item['original_version'] for item in report_data['modules']}
    for package_id in sorted(set(original_modules) | set(modules) | set(exported)):
        if modules.get(package_id) != original_modules.get(package_id):
            problems.append(f'{package_id}: installed module differs from the original v12 backup.')
        if package_id in exported and exported[package_id] != modules.get(package_id):
            problems.append(f'{package_id}: GM module version differs from the clone manifest.')
    for package_id in sorted(configured ^ active):
        problems.append(f'{package_id}: saved configuration and active state disagree.')
    for package_id in sorted(expected - configured):
        problems.append(f'{package_id}: retained module is not enabled in the clone.')
    for package_id in sorted(configured - expected):
        problems.append(f'{package_id}: module must remain disabled at the v12 review stage.')
    selected_releases = [
        {
            'type': 'system',
            'id': world['system'],
            'version': plan['selected_system']['selected_version'],
            'manifest': plan['selected_system']['selected_manifest'],
        }
    ]
    seen = set()
    for item in [*plan['selected_modules'], *plan['selected_dependencies']]:
        if item['id'] not in seen:
            selected_releases.append(
                {
                    'type': 'module',
                    'id': item['id'],
                    'version': item['version'],
                    'manifest': item['manifest'],
                }
            )
            seen.add(item['id'])
    result = {
        'format': 'campaign-studio-foundry-clone-review',
        'status': 'v12_modules_reviewed' if not problems else 'blocked',
        'reviewed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'backup_path': str(backup_path),
        'clone_path': str(clone),
        'plan_path': str(plan_file),
        'plan_sha256': storage.sha256_file(plan_file),
        'report_path': str(report_file),
        'clone_receipt_path': str(receipt_file),
        'target_build': plan['target_build'],
        'expected_enabled': sorted(expected),
        'configured_enabled': sorted(configured),
        'runtime_active': sorted(active),
        'disabled_in_v12': plan['disable_in_v12'],
        'locked_changes': plan['locked_changes'],
        'selected_releases': selected_releases,
        'blockers': problems,
        'confirmed_clone_export': True,
        'migration_ready': False,
    }
    suffix = uuid.uuid4().hex
    inventory_file = backup_path / f'clone-review-inventory-{suffix}.json'
    review_file = backup_path / f'clone-review-{suffix}.json'
    with inventory_file.open('x', encoding='utf-8') as stream:
        json.dump(inventory, stream, indent=2, ensure_ascii=False)
    result['inventory_path'] = str(inventory_file)
    result['inventory_sha256'] = storage.sha256_file(inventory_file)
    result['review_path'] = str(review_file)
    with review_file.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    return result


def audit_migration(review_path, inventory, confirmed_clone=False, manual_checks=None):
    """Audit a GM-exported migrated clone against its saved target package plan."""
    if confirmed_clone is not True:
        raise ValueError('Confirm that this export came from the isolated migrated clone.')
    selected_review = foundry_backup.absolute_folder(review_path, 'v12 clone review')
    if not selected_review.is_file() or selected_review.stat().st_size > 256 * 1024:
        raise ValueError('Select a passing v12 clone review.')
    preliminary = json.loads(selected_review.read_text(encoding='utf-8'))
    if not isinstance(preliminary, dict):
        raise ValueError('Invalid v12 clone review.')
    backup = foundry_backup.verify(preliminary.get('backup_path'))
    backup_path = Path(backup['path'])
    review_file, review = _sidecar(selected_review, backup_path, 'clone-review-', 256 * 1024)
    plan_file, plan = _sidecar(
        review.get('plan_path'), backup_path, 'upgrade-clone-plan-', 256 * 1024
    )
    report_file, report_data = _sidecar(
        plan.get('report_path'), backup_path, 'upgrade-report-', 20 * 1024 * 1024
    )
    reviewed_inventory_file, _ = _sidecar(
        review.get('inventory_path'), backup_path, 'clone-review-inventory-', 2 * 1024 * 1024
    )
    clone_receipt_file, clone_receipt = _sidecar(
        plan.get('clone_receipt_path'), backup_path, 'restore-test-', 64 * 1024
    )
    clone = foundry_backup.absolute_folder(plan.get('clone_path'), 'Upgrade clone')
    backup_manifest = foundry_backup.read_manifest(backup_path)
    original_root = foundry_backup.absolute_folder(
        backup_manifest.get('source_user_data'), 'Original Foundry User Data'
    )
    plan_modules = plan.get('selected_modules')
    report_modules = report_data.get('modules')
    if (
        not isinstance(plan_modules, list)
        or any(
            not isinstance(item, dict) or not ID_RE.fullmatch(str(item.get('id') or ''))
            for item in plan_modules
        )
        or not isinstance(report_modules, list)
        or any(not isinstance(item, dict) for item in report_modules)
    ):
        raise ValueError('The saved clone plan has invalid module decisions.')
    selected_from_report = [
        {
            'id': item['id'],
            'version': item.get('selected_version'),
            'manifest': item.get('selected_manifest'),
        }
        for item in report_modules
        if item.get('proposed_enabled') is True
    ]
    v12_enabled = sorted(
        {item['id'] for item in plan_modules} - set(plan.get('enable_after_review') or [])
    )
    if (
        review.get('format') != 'campaign-studio-foundry-clone-review'
        or review.get('status') != 'v12_modules_reviewed'
        or review.get('blockers') != []
        or review.get('backup_path') != str(backup_path)
        or review.get('review_path') != str(review_file)
        or review.get('plan_sha256') != storage.sha256_file(plan_file)
        or review.get('report_path') != str(report_file)
        or review.get('disabled_in_v12') != plan.get('disable_in_v12')
        or review.get('inventory_sha256') != storage.sha256_file(reviewed_inventory_file)
        or review.get('expected_enabled') != v12_enabled
        or review.get('configured_enabled') != v12_enabled
        or review.get('runtime_active') != v12_enabled
        or review.get('clone_path') != str(clone)
        or review.get('target_build') != plan.get('target_build')
        or review.get('confirmed_clone_export') is not True
        or review.get('migration_ready') is not False
        or plan.get('format') != 'campaign-studio-foundry-clone-plan'
        or plan.get('status') != 'awaiting_v12_module_review'
        or plan.get('migration_ready') is not False
        or plan.get('report_sha256') != storage.sha256_file(report_file)
        or plan.get('target_build') != report_data.get('recommended_build')
        or plan.get('world') != report_data.get('world')
        or plan_modules != selected_from_report
        or clone_receipt.get('format') != 'campaign-studio-foundry-restore-test'
        or clone_receipt.get('backup_path') != str(backup_path)
        or clone_receipt.get('restore_path') != str(clone)
        or clone_receipt.get('backup_manifest_sha256')
        != storage.sha256_file(backup_path / foundry_backup.MANIFEST)
        or clone_receipt.get('world') != backup['world']
        or review.get('clone_receipt_path') != str(clone_receipt_file)
        or not clone.is_dir()
        or foundry_backup.within(clone, original_root)
        or foundry_backup.within(clone, backup_path)
        or foundry_backup.within(backup_path, clone)
    ):
        raise ValueError('The passing v12 review, clone plan and verified backup do not match.')
    if (
        not isinstance(inventory, dict)
        or len(json.dumps(inventory).encode('utf-8')) > 2 * 1024 * 1024
        or inventory.get('format') != INVENTORY_FORMAT
        or inventory.get('schema') != 2
        or inventory.get('phase') != 'migrated-clone'
    ):
        raise ValueError('Import a migrated-clone GM inventory under 2 MB.')
    world = inventory.get('world')
    system = inventory.get('system')
    module_rows = inventory.get('modules')
    enabled_ids = inventory.get('enabledModuleIds')
    if (
        not isinstance(world, dict)
        or not isinstance(system, dict)
        or not isinstance(module_rows, list)
        or not isinstance(enabled_ids, list)
        or len(module_rows) > 500
        or len(enabled_ids) != len(set(str(item) for item in enabled_ids))
        or any(not isinstance(item, str) or not ID_RE.fullmatch(item) for item in enabled_ids)
        or any(
            not isinstance(item, dict)
            or not ID_RE.fullmatch(str(item.get('id') or ''))
            or not isinstance(item.get('version'), str)
            or not isinstance(item.get('enabled'), bool)
            for item in module_rows
        )
        or len({item['id'] for item in module_rows}) != len(module_rows)
        or not isinstance(system.get('version'), str)
    ):
        raise ValueError('Invalid migrated-clone GM inventory.')
    if world.get('id') != plan['world']['id'] or world.get('system') != plan['world']['system']:
        raise ValueError('The migrated-clone export is for a different world or game system.')
    try:
        target = _core(plan['target_build'])
        current = _core(world.get('coreVersion'))
    except ValueError as error:
        raise ValueError('Invalid Foundry target or migrated-clone build.') from error
    if target[0] not in (13, 14):
        raise ValueError('Migration audit currently supports Foundry v13 and v14 targets.')
    checks = manual_checks if isinstance(manual_checks, dict) else {}
    check_names = ('launch', 'scenes', 'journals', 'actors_items', 'modules')
    checks = {name: checks.get(name) is True for name in check_names}
    world_manifest = clone / storage.manifest_path(backup['world']['manifest_path'])
    if (
        world_manifest.is_symlink()
        or not world_manifest.is_file()
        or not foundry_backup.within(world_manifest.resolve(), clone)
        or world_manifest.stat().st_size > MAX_MANIFEST
    ):
        raise ValueError('The migrated clone world manifest is missing or linked.')
    clone_world = json.loads(world_manifest.read_text(encoding='utf-8'))
    if not isinstance(clone_world, dict):
        raise ValueError('Invalid migrated clone world manifest.')
    systems = _installed_clone_packages(clone, 'systems')
    modules = _installed_clone_packages(clone, 'modules')
    expected_versions = {}
    for item in [*(plan.get('selected_modules') or []), *(plan.get('selected_dependencies') or [])]:
        package_id, version = item['id'], item['version']
        if package_id in expected_versions and expected_versions[package_id] != version:
            raise ValueError('The clone plan has conflicting selected module releases.')
        expected_versions[package_id] = version
    configured = set(enabled_ids)
    active = {item['id'] for item in module_rows if item['enabled']}
    exported = {item['id']: item['version'] for item in module_rows}
    expected_enabled = set(expected_versions)
    problems = []
    if current != target:
        problems.append(
            f'The running Foundry build is {world.get("coreVersion")}, not {plan["target_build"]}.'
        )
    if (
        clone_world.get('id') != world['id']
        or clone_world.get('system') != world['system']
        or clone_world.get('coreVersion') != plan['target_build']
    ):
        problems.append('The clone world manifest does not match the target Foundry build.')
    selected_system = plan['selected_system']
    if systems.get(world['system']) != selected_system['selected_version']:
        problems.append('The selected game system release is not installed in the clone.')
    if system.get('id') != world['system'] or system['version'] != systems.get(world['system']):
        problems.append('The GM system version disagrees with the clone system manifest.')
    if clone_world.get('systemVersion') != selected_system['selected_version']:
        problems.append('The world manifest does not record the selected game system version.')
    for package_id, version in sorted(expected_versions.items()):
        if modules.get(package_id) != version:
            problems.append(
                f'{package_id}: selected release {version} is not installed in the clone.'
            )
        if exported.get(package_id) != version:
            problems.append(f'{package_id}: GM export does not show selected release {version}.')
    for package_id in sorted(configured ^ active):
        problems.append(f'{package_id}: saved configuration and active state disagree.')
    for package_id in sorted(expected_enabled - configured):
        problems.append(f'{package_id}: selected module is not enabled after migration.')
    for package_id in sorted(configured - expected_enabled):
        problems.append(f'{package_id}: unplanned module is enabled after migration.')
    for name in check_names:
        if not checks[name]:
            problems.append(f'GM has not confirmed the {name.replace("_", "/")} check.')
    result = {
        'format': 'campaign-studio-foundry-migration-audit',
        'status': 'reviewed' if not problems else 'blocked',
        'audited_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'backup_path': str(backup_path),
        'clone_path': str(clone),
        'v12_review_path': str(review_file),
        'v12_review_sha256': storage.sha256_file(review_file),
        'plan_path': str(plan_file),
        'target_build': plan['target_build'],
        'reported_build': world.get('coreVersion'),
        'selected_system': {'id': world['system'], 'version': selected_system['selected_version']},
        'selected_modules': expected_versions,
        'installed_system_version': systems.get(world['system']),
        'installed_module_versions': {key: modules.get(key) for key in expected_versions},
        'configured_enabled': sorted(configured),
        'runtime_active': sorted(active),
        'manual_checks': checks,
        'blockers': problems,
        'confirmed_clone_export': True,
        'cutover_ready': False,
    }
    suffix = uuid.uuid4().hex
    inventory_file = backup_path / f'migration-audit-inventory-{suffix}.json'
    audit_file = backup_path / f'migration-audit-{suffix}.json'
    with inventory_file.open('x', encoding='utf-8') as stream:
        json.dump(inventory, stream, indent=2, ensure_ascii=False)
    result['inventory_path'] = str(inventory_file)
    result['inventory_sha256'] = storage.sha256_file(inventory_file)
    result['audit_path'] = str(audit_file)
    with audit_file.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    return result
