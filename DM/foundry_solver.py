"""Select a complete compatible Foundry build and package set."""

from foundry_compat import (
    ID_RE,
    RELEASES_URL,
    compatible,
    core,
    relationships,
    is_verified,
    version_covers,
    version_key,
)


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


def _system_allowed(release, system_id, system_version):
    systems = release.get('systems') or []
    if not systems:
        return True
    return any(
        isinstance(entry, dict)
        and entry.get('id') == system_id
        and version_covers(system_version, entry.get('compatibility') or {})
        for entry in systems
    )


def _solve(build, roots, catalog, system_id, approved_dependencies):
    problems = set()
    build_key = core(build)
    visited = 0

    def walk(pending, selected, constraints):
        nonlocal visited
        visited += 1
        if visited > 20000:
            problems.add('Dependency search limit reached; no safe recommendation for this build.')
            return None
        if not pending:
            if any(
                not all(version_covers(selected[key]['version'], item) for item in values)
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
                version_covers(selected[package_id]['version'], constraint)
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
            and all(version_covers(release['version'], c) for c in constraints.get(package_id, []))
        ]
        choices.sort(key=lambda release: version_key(release['version']), reverse=True)
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
                'original_requires': relationships(module)[0],
                'original_systems': relationships(module)[1],
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
                    _release_compatible(release, core(build)) for release in entry['releases']
                ):
                    message = f'{package_id}: no eligible release for Foundry {build}.'
                else:
                    continue
                if message not in blockers:
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
