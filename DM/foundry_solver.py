"""Select a complete compatible Foundry build and package set."""

from foundry_catalog import Catalog
from foundry_compat import (
    ID_RE,
    compatible,
    core,
    core_covers,
    relationships,
    is_verified,
    version_covers,
    version_key,
)

SEARCH_LIMIT = 20000
CHECKED_RELEASES = 10  # Per package and build, newest first.


class _SearchLimit(Exception):
    pass


def _release_compatible(release, build):
    try:
        version_key(release['version'])
        return (
            not release.get('error')
            and compatible(release['compatibility'], build)
            and compatible(release.get('manifest_compatibility') or {}, build)
        )
    except ValueError:
        return False


def _directory_compatible(release, build):
    try:
        return compatible(release['compatibility'], build)
    except ValueError:
        return False


def _ends_before(release, build):
    try:
        maximum = (release.get('manifest_compatibility') or {}).get('maximum')
        return not core_covers(maximum, build, False)
    except ValueError:
        return False


def _newest_first(releases):
    ordered = []
    for release in releases:
        try:
            ordered.append((version_key(release['version']), release))
        except ValueError:
            continue  # A version that cannot be compared is never chosen.
    ordered.sort(key=lambda item: item[0], reverse=True)
    return [release for _key, release in ordered]


def _candidates(catalog, package_id, build, skip=lambda release: False, stops=None):
    """Yield the package's releases usable on `build`, newest first.

    `skip` rejects a release by version before its manifest is read; other manifests are read only
    as the walk reaches them. The walk ends early, saying why in `stops` where that matters:
    - at a release whose manifest support ends before `build`, since an older release does not
      support a newer Foundry than its successor;
    - at a manifest that cannot be read for now (a rate limit or timeout), so an older release never
      stands in for a newer one that may be fine;
    - after CHECKED_RELEASES releases, so a long history costs a bounded number of reads.
    """
    stops = [] if stops is None else stops
    checked, previous = 0, None
    for release in _newest_first(catalog.entry(package_id)['releases']):
        if not _directory_compatible(release, build) or skip(release):
            continue
        if checked == CHECKED_RELEASES:
            stops.append(
                f'{package_id}: only its newest {CHECKED_RELEASES} releases for this build were '
                f'checked (down to {previous}).'
            )
            return
        checked += 1
        previous = release['version']
        catalog.review(package_id, release)
        if _release_compatible(release, build):
            yield release
        elif release.get('unread'):
            stops.append(
                f'{package_id}: the {release["version"]} manifest could not be read '
                f'({release["error"]}); run the report again later.'
            )
            return
        elif _ends_before(release, build):
            return


def _system_clash(release, system_id, system_version):
    """None when the release supports the chosen system release, else the choices ruling it out."""
    systems = release.get('systems') or []
    if not systems:
        return None
    supported = [item for item in systems if isinstance(item, dict) and item.get('id') == system_id]
    if any(version_covers(system_version, item.get('compatibility') or {}) for item in supported):
        return None
    # No other system release can help a module written for a different game system.
    return {system_id} if supported else set()


def _older_than(floor):
    """Skip releases older than the installed version: a clone never downgrades a package."""
    return lambda release: floor is not None and version_key(release['version']) < floor[0]


def _unavailable(catalog, package_id, build, floor):
    """Why the package has no release to choose on `build` whatever else is chosen, or None."""
    entry = catalog.entry(package_id)
    if not entry or entry['status'] != 'listed':
        return f'{package_id}: directory status is {entry["status"] if entry else "unknown"}.'
    stops = []
    older = _older_than(floor)
    if next(_candidates(catalog, package_id, core(build), older, stops), None):
        return None
    if stops:
        return stops[0]
    listed = _newest_first(
        release for release in entry['releases'] if _directory_compatible(release, core(build))
    )
    if floor and listed and all(older(release) for release in listed):
        return (
            f'{package_id}: no release for Foundry {build} is as new as the installed {floor[1]}.'
        )
    return f'{package_id}: no eligible release for Foundry {build}.'


def _solve(build, roots, catalog, system_id, approved_dependencies, floors):
    """Find the newest compatible release of every root and required dependency on `build`.

    Packages are chosen in order, the game system first, each trying its newest release first, and
    every requirement is checked as soon as both of its packages are chosen. A failure reports the
    chosen packages that caused it, and the search goes straight back to the latest of them
    (conflict-directed backjumping): another release of an unrelated package cannot fix the failure,
    so those combinations are never tried. `floors` holds installed versions, which are never
    downgraded.
    """
    problems = set()
    # (package, other package) -> versions of the other that no release of the package works with
    mismatches = {}
    build_key = core(build)
    visited = 0

    def choose(package_id, release, remaining, selected, constraints, requirers, notes):
        """Return the search state after choosing `release`, or the choices that rule it out.

        Reasons go into `notes`: a message, or the (package, version) the release cannot work with.
        """
        chosen = {**selected, package_id: release}
        clash = None
        if package_id != system_id:
            system_version = selected[system_id]['version']
            clash = _system_clash(release, system_id, system_version)
            if clash is not None:
                notes.add((system_id, system_version))
        pending = list(remaining)
        constraints = {key: list(value) for key, value in constraints.items()}
        requirers = {key: set(value) for key, value in requirers.items()}
        for dependency in release.get('requires') or []:
            dep_id, kind, needed = dependency['id'], dependency['type'], dependency['compatibility']
            reason = None
            if kind not in ('module', 'system'):
                reason = f'{package_id} has an unsupported dependency type.'
            elif kind == 'system' and dep_id != system_id:
                reason = f'{package_id} requires a different game system: {dep_id}.'
            elif dep_id.casefold() == 'plutonium':
                reason = f'{package_id} requires excluded Plutonium.'
            elif dep_id not in roots and dep_id not in approved_dependencies:
                catalog.entry(dep_id)  # Its directory status is evidence for the GM's decision.
                reason = (
                    f'{package_id} requires {dep_id}; '
                    'GM approval to enable this dependency is needed.'
                )
            if reason:
                notes.add(reason)
                clash = clash or set()
            elif dep_id in chosen:
                if not version_covers(chosen[dep_id]['version'], needed):
                    notes.add((dep_id, chosen[dep_id]['version']))
                    clash = (clash or set()) | ({dep_id} - {package_id})
            else:
                constraints.setdefault(dep_id, []).append((package_id, needed))
                if dep_id not in roots:
                    requirers.setdefault(dep_id, set()).add(package_id)
                if dep_id not in pending:
                    pending.append(dep_id)
        if clash is not None:
            return None, clash
        return (pending, chosen, constraints, requirers), None

    def walk(pending, selected, constraints, requirers):
        """Return (selection, None), or (None, the chosen packages that caused the failure)."""
        nonlocal visited
        visited += 1
        if visited > SEARCH_LIMIT:
            raise _SearchLimit
        if not pending:
            return selected, None
        package_id, remaining = pending[0], pending[1:]
        # Had none of its requirers been chosen, this package would not be needed.
        conflict = set(requirers.get(package_id, ()))
        floor = floors.get(package_id)
        unavailable = _unavailable(catalog, package_id, build, floor)
        if unavailable:
            problems.add(unavailable)
            return None, conflict
        limits = constraints.get(package_id, [])
        older = _older_than(floor)

        def skip(release):
            if older(release):
                return True
            sources = {
                source
                for source, needed in limits
                if not version_covers(release['version'], needed)
            }
            conflict.update(sources)
            return bool(sources)

        tried = False
        notes = set()
        stops = []
        for release in _candidates(catalog, package_id, build_key, skip, stops):
            tried = True
            state, clash = choose(
                package_id, release, remaining, selected, constraints, requirers, notes
            )
            if state is None:
                conflict |= clash
                continue
            solution, failure = walk(*state)
            if solution:
                return solution, None
            if package_id not in failure:
                return None, failure  # No other release of this package can fix it.
            conflict |= failure - {package_id}
        if not tried and not stops:
            problems.add(
                f'{package_id}: no release for Foundry {build} has the version others require.'
            )
        problems.update(stops)
        for note in notes:
            if isinstance(note, str):
                problems.add(note)
            else:
                mismatches.setdefault((package_id, note[0]), []).append(note[1])
        return None, conflict

    try:
        solution, _conflict = walk(list(roots), {}, {}, {})
    except _SearchLimit:
        problems.add('Dependency search limit reached; no safe recommendation for this build.')
        solution = None
    for (package_id, other_id), versions in mismatches.items():
        problems.add(
            f'{package_id}: no release for Foundry {build} works with {other_id} '
            f'{", ".join(dict.fromkeys(versions))}.'
        )
    return solution, sorted(problems)


def analyze(inventory, catalog, disabled_modules=(), approved_dependencies=()):
    """Choose newest full match without silently dropping an eligible enabled module.

    `catalog` is a foundry_catalog.Catalog, or saved catalogue data that is complete as given.
    """
    if not isinstance(catalog, Catalog):
        catalog = Catalog(catalog)
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
    if disabled & approved:
        raise ValueError('A module cannot be both disabled and approved as a dependency.')
    if system_id in approved or 'plutonium' in {key.casefold() for key in approved}:
        raise ValueError('The game system and Plutonium cannot be approved as module dependencies.')
    if any(catalog.entry(key) is None for key in sorted(approved)):
        raise ValueError('Only discovered modules can be approved as dependencies.')
    decisions = []
    retained = []
    for module in modules.values():
        entry = catalog.entry(module['id']) or {'status': 'unknown', 'releases': []}
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
                'original_requires': relationships(module)[0],
                'original_systems': relationships(module)[1],
                'locked': bool(module.get('locked')),
            }
        )
    roots = [system_id, *retained]
    candidates = []
    best = None
    verified_best = None
    floors = {}
    for package in [inventory['system'], *modules.values()]:
        try:
            floors[package['id']] = (version_key(package['version']), package['version'])
        except ValueError:
            continue  # An installed version that cannot be compared sets no floor.
    for build in catalog.builds:
        solution, blockers = _solve(build, roots, catalog, system_id, approved, floors)
        if not solution:
            # The search stops at the first package that cannot be satisfied; list every such root.
            for package_id in roots:
                message = _unavailable(catalog, package_id, build, floors.get(package_id))
                if message and message not in blockers:
                    blockers.append(message)
            blockers.sort()
        verified = bool(solution) and all(
            is_verified(release['compatibility'], core(build))
            and is_verified(
                release.get('manifest_compatibility') or release['compatibility'], core(build)
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
            'original_requires': relationships(inventory['system'])[0],
            'locked': bool(inventory['system'].get('locked')),
            'directory_status': catalog.packages.get(system_id, {}).get('status', 'unknown'),
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
        'catalog_evidence': catalog.packages,
        'source': catalog.source,
    }
