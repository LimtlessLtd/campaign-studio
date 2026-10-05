"""Foundry upgrade workflow: verify evidence, report compatibility, and plan or audit isolated clones."""

import copy
import datetime
import json
import uuid
from pathlib import Path

import config
import foundry_backup
import storage
from foundry_catalog import MAX_MANIFEST, collect_catalog
from foundry_compat import ID_RE, INVENTORY_FORMAT, core, relationships
from foundry_solver import analyze


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
    if core(world['coreVersion'])[0] != 12:
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
        relationships(package)
        if package is not system and not isinstance(package.get('enabled'), bool):
            raise ValueError('GM inventory must record enabled module states.')
    if not set(enabled_ids).issubset({package['id'] for package in modules}):
        raise ValueError('An enabled module is missing from the backup and GM inventory.')
    return selected


def complete_inventory(inventory, backup_path):
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


def report(
    inventory,
    backup_path,
    disabled_modules=(),
    approved_dependencies=(),
    catalog=None,
):
    backup = foundry_backup.verify(backup_path)
    completed_inventory = complete_inventory(inventory, backup['path'])
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
    if not build or core(build) <= core(world['coreVersion']):
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
        or core(restored_world.get('coreVersion'))[0] != 12
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
        target = core(plan['target_build'])
        current = core(world.get('coreVersion'))
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
