"""The upgrade wizard's last step: switch the live Foundry User Data to the audited migrated clone, and back.

The review repeats the migration audit and compares the original User Data with its offline backup, per
world, package or folder. The world being upgraded must be unchanged; the GM confirms every other change,
such as a test world uninstalled on purpose. The cutover renames the live User Data to a dated rollback
folder and copies the clone into its place, leaving out the worlds the GM confirmed as removed and stale
lock folders, and points `Config/options.json` back at the live folder. Rolling back renames the folders
back. Studio never deletes either User Data folder, and Foundry must stay closed throughout. Each step
saves its evidence beside the upgrade backup.
"""

import datetime
import json
import shutil
import uuid
from pathlib import Path

import config
import foundry_backup
import foundry_upgrade
import storage
from foundry_compat import core

REVIEW_FORMAT = 'campaign-studio-foundry-cutover-review'
CUTOVER_FORMAT = 'campaign-studio-foundry-cutover'
LIMIT = 1024 * 1024
# What the review repeats from the saved migration audit; any difference means the clone changed.
EVIDENCE = (
    'status',
    'backup_path',
    'clone_path',
    'v12_review_path',
    'v12_review_sha256',
    'plan_path',
    'target_build',
    'target_generation',
    'reported_build',
    'selected_system',
    'selected_modules',
    'installed_system_version',
    'installed_module_versions',
    'configured_enabled',
    'runtime_active',
    'manual_checks',
    'blockers',
)


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _closed(confirmed):
    if confirmed is not True or foundry_backup.running_foundry():
        raise ValueError('Close both the original and the upgraded Foundry installations first.')


def _confirmed(value):
    if value is None:
        return set()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ValueError('Confirmed changes must be a list of User Data areas.')
    return set(value)


def review(audit_path, confirmed_closed=False, confirmed_changes=(), *, save=True):
    """Recheck the migrated clone and list what changed in the original User Data since its backup.

    `confirmed_changes` names the changed areas the GM confirmed. The review is ready only when the
    world being upgraded is unchanged and every other change is confirmed; only then is it saved.
    """
    _closed(confirmed_closed)
    confirmed = _confirmed(confirmed_changes)
    backup, audit_file, audit, inventory = foundry_upgrade.passing_audit(audit_path)
    backup_path = Path(backup['path'])
    current = foundry_upgrade.audit_migration(
        audit.get('v12_review_path'), inventory, True, audit.get('manual_checks'), save=False
    )
    if any(audit.get(key) != current.get(key) for key in EVIDENCE):
        raise ValueError(
            'The migrated clone or its evidence changed after the audit. Audit it again.'
        )
    source = foundry_backup.compare_source(backup_path)
    world_area = foundry_backup.area(backup['world']['manifest_path'])
    original_world = (Path(source['path']) / storage.manifest_path(world_area)).resolve()
    selected = config.world_info(config.settings().get('world_path'))
    if (
        Path(selected['path']).resolve() != original_world
        or selected['id'] != backup['world']['id']
    ):
        raise ValueError('Select the original Foundry world in Settings before cutover review.')
    if foundry_backup.running_foundry():
        raise ValueError('Foundry started during cutover review. Close it and review again.')
    changes = source['changes']
    others = [change for change in changes if change['area'] != world_area]
    blockers = [
        f'The world being upgraded ({change["area"]}) changed after the backup: '
        f'{change["added"]} added, {change["removed"]} removed and {change["changed"]} changed. '
        'The migrated clone does not have those changes. Take a new backup and repeat the upgrade.'
        for change in changes
        if change['area'] == world_area
    ]
    unconfirmed = [change['area'] for change in others if change['area'] not in confirmed]
    status = 'blocked' if blockers else 'needs_confirmation' if unconfirmed else 'ready_for_cutover'
    result = {
        'format': REVIEW_FORMAT,
        'status': status,
        'cutover_ready': status == 'ready_for_cutover',
        'reviewed_at': _now().isoformat(),
        'audit_path': str(audit_file),
        'audit_sha256': storage.sha256_file(audit_file),
        'backup_path': str(backup_path),
        'backup_manifest_sha256': storage.sha256_file(backup_path / foundry_backup.MANIFEST),
        'original_user_data': source['path'],
        'clone_path': current['clone_path'],
        'original_build': backup['world']['core_version'],
        'target_build': current['target_build'],
        'target_generation': current['target_generation'],
        'world_id': inventory['world']['id'],
        'world_area': world_area,
        'changes': changes,
        'confirmed_changes': sorted(
            change['area'] for change in others if change['area'] in confirmed
        ),
        'unconfirmed_changes': unconfirmed,
        # The cutover leaves these out of the copy, since the GM removed them from the live folder.
        'removed_worlds': sorted(
            change['id']
            for change in others
            if change['area'] in confirmed
            and change['kind'] == 'world'
            and change['change'] == 'removed'
        ),
        'blockers': blockers,
    }
    if status == 'ready_for_cutover' and save:
        path = backup_path / f'cutover-review-{result["audit_sha256"][:24]}.json'
        result['review_path'] = str(path)
        with storage.file_lock(path):
            storage.atomic_json(path, result, durable=True)
    return result


def _evidence(path, label, prefix, expected_format):
    """A saved JSON record beside its upgrade backup, as `foundry_upgrade.sidecar` reads it."""
    selected = foundry_backup.absolute_folder(path, label)
    if not selected.is_file() or selected.stat().st_size > LIMIT:
        raise ValueError(f'Select a saved {label.lower()}.')
    preliminary = json.loads(selected.read_text(encoding='utf-8'))
    if not isinstance(preliminary, dict) or preliminary.get('format') != expected_format:
        raise ValueError(f'Select a saved {label.lower()}.')
    backup = foundry_backup.absolute_folder(preliminary.get('backup_path'), 'Backup folder')
    foundry_backup.read_manifest(backup)
    return foundry_upgrade.sidecar(selected, backup, prefix, LIMIT)


def _options(user_data):
    """The User Data folder's `Config/options.json`, or None when it has none."""
    path = user_data / 'Config' / 'options.json'
    if not path.is_file():
        return None
    try:
        options = json.loads(path.read_text(encoding='utf-8'))
    except ValueError as error:
        raise ValueError(f'Foundry options are not valid JSON: {path}') from error
    if not isinstance(options, dict):
        raise ValueError(f'Foundry options are not a JSON object: {path}')
    return options


def _port(options):
    port = (options or {}).get('port')
    return (
        port if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 else None
    )


def _point_options(user_data, live, port):
    """Point the copied clone's options at the live User Data path and the live installation's port.

    The GM opened the clone from its own folder, often on another port, so its options name both. With
    no port in the live options, Foundry's default applies, as it did before.
    """
    options = _options(user_data) or {}
    before = {'dataPath': options.get('dataPath'), 'port': options.get('port')}
    options['dataPath'] = str(live)
    if port is None:
        options.pop('port', None)
    else:
        options['port'] = port
    storage.atomic_json(user_data / 'Config' / 'options.json', options, durable=True)
    return {'before': before, 'after': {'dataPath': str(live), 'port': options.get('port')}}


def _save(path, record):
    with storage.file_lock(path):
        storage.atomic_json(path, record, durable=True)


def cut_over(review_path, confirmed_closed=False):
    """Switch the live User Data to a copy of the audited migrated clone, keeping the original.

    Repeats the review first and refuses if anything it compared changed since the GM read it. The copy
    is made and verified beside the live folder before either folder moves, then the live folder is
    renamed to a dated rollback folder and the copy takes its name. A receipt is saved beside the backup
    before the renames, so an interrupted switch can be rolled back.
    """
    _closed(confirmed_closed)
    review_file, saved = _evidence(review_path, 'Cutover review', 'cutover-review-', REVIEW_FORMAT)
    current = review(
        saved.get('audit_path'), True, saved.get('confirmed_changes') or [], save=False
    )
    if (
        saved.get('review_path') != str(review_file)
        or current['status'] != 'ready_for_cutover'
        or saved.get('status') != 'ready_for_cutover'
        or any(
            current[key] != saved.get(key)
            for key in (
                'audit_sha256',
                'backup_manifest_sha256',
                'original_user_data',
                'clone_path',
                'target_build',
                'world_id',
                'changes',
                'confirmed_changes',
                'removed_worlds',
            )
        )
    ):
        raise ValueError(
            'The live User Data, the clone or their evidence changed since the cutover review. '
            'Review cutover readiness again.'
        )
    live = Path(current['original_user_data'])
    clone = Path(current['clone_path'])
    if foundry_backup.within(live, clone) or foundry_backup.within(clone, live):
        raise ValueError('The live User Data and the migrated clone must be separate folders.')
    before = foundry_backup.listing(live)
    port = _port(_options(live))
    started = _now()
    rollback = live.with_name(
        f'{live.name}-v{core(current["original_build"])[0]}-before-v'
        f'{current["target_generation"]}-{started:%Y%m%d-%H%M%S}'
    )
    staging = live.with_name(f'.incomplete-{live.name}-cutover-{uuid.uuid4().hex[:8]}')
    if rollback.exists():
        raise ValueError(f'The rollback folder already exists: {rollback}. Try again in a minute.')
    locks = foundry_backup.stale_locks(clone)
    try:
        copied = foundry_backup.copy_folder(
            clone,
            staging,
            [f'Data/worlds/{world_id}' for world_id in current['removed_worlds']] + locks,
        )
        options = _point_options(staging, live, port)
        if foundry_backup.running_foundry() or foundry_backup.listing(live) != before:
            raise ValueError('Foundry started or the live User Data changed during the copy.')
    except (OSError, ValueError) as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError(f'{error} Nothing was switched.') from error
    receipt = Path(current['backup_path']) / f'live-cutover-{uuid.uuid4().hex}.json'
    record = {
        'format': CUTOVER_FORMAT,
        'status': 'switching',
        'started_at': started.isoformat(),
        'receipt_path': str(receipt),
        'review_path': str(review_file),
        'review_sha256': storage.sha256_file(review_file),
        'audit_path': current['audit_path'],
        'backup_path': current['backup_path'],
        'world_id': current['world_id'],
        'world_area': current['world_area'],
        'original_build': current['original_build'],
        'target_build': current['target_build'],
        'target_generation': current['target_generation'],
        'live_user_data': str(live),
        'rollback_user_data': str(rollback),
        'staging_user_data': str(staging),
        'clone_path': str(clone),
        'confirmed_changes': current['confirmed_changes'],
        'left_out_worlds': current['removed_worlds'],
        'stale_locks_left_out': locks,
        'files': copied['files'],
        'bytes': copied['bytes'],
        'options': options,
    }
    _save(receipt, record)
    try:
        live.rename(rollback)
    except OSError as error:
        shutil.rmtree(staging, ignore_errors=True)
        _save(receipt, {**record, 'status': 'failed', 'error': str(error)})
        raise ValueError(
            f'The live User Data could not be renamed ({error}). Close every program using it and '
            'try again. Nothing was switched.'
        ) from error
    try:
        staging.rename(live)
    except OSError as error:
        try:
            rollback.rename(live)
        except OSError:
            _save(receipt, {**record, 'error': str(error)})
            raise ValueError(
                f'The copy could not take the live folder name ({error}), and the original could '
                f'not be renamed back. Roll back with the receipt {receipt}, or rename {rollback} '
                f'to {live} by hand. The copy is at {staging}.'
            ) from error
        shutil.rmtree(staging, ignore_errors=True)
        _save(receipt, {**record, 'status': 'failed', 'error': str(error)})
        raise ValueError(
            f'The copy could not take the live folder name ({error}). The original is back in '
            'place; nothing was switched.'
        ) from error
    record.update(status='cut_over', switched_at=_now().isoformat())
    _save(receipt, record)
    return record


def _original(folder, receipt):
    """Refuse a rollback folder that no longer holds the original world at its original build."""
    world = folder / storage.manifest_path(receipt['world_area']) / 'world.json'
    try:
        data = json.loads(world.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = None
    if (
        folder.is_symlink()
        or not isinstance(data, dict)
        or data.get('id') != receipt['world_id']
        or (data.get('coreVersion') or (data.get('compatibility') or {}).get('verified'))
        != receipt['original_build']
    ):
        raise ValueError(
            f'The rollback folder no longer holds the original world {receipt["world_id"]} at Foundry '
            f'{receipt["original_build"]}: {folder}'
        )


def roll_back(cutover_path, confirmed_closed=False):
    """Put the original User Data back in place; the upgraded one is kept under a dated name."""
    _closed(confirmed_closed)
    receipt_file, receipt = _evidence(
        cutover_path, 'Cutover receipt', 'live-cutover-', CUTOVER_FORMAT
    )
    with storage.file_lock(receipt_file):
        receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
        live = foundry_backup.absolute_folder(receipt.get('live_user_data'), 'Live User Data')
        rollback = foundry_backup.absolute_folder(
            receipt.get('rollback_user_data'), 'Rollback User Data'
        )
        if receipt.get('receipt_path') != str(receipt_file) or rollback.parent != live.parent:
            raise ValueError('Invalid cutover receipt.')
        if receipt.get('status') not in ('cut_over', 'switching'):
            raise ValueError(f'Nothing to roll back: this cutover is {receipt.get("status")}.')
        _original(rollback, receipt)
        set_aside = None
        if live.exists():
            if live.is_symlink() or not live.is_dir():
                raise ValueError(f'The live User Data is not a folder: {live}')
            set_aside = live.with_name(
                f'{live.name}-v{receipt["target_generation"]}-rolled-back-{_now():%Y%m%d-%H%M%S}'
            )
            if set_aside.exists():
                raise ValueError(f'{set_aside} already exists. Try again in a minute.')
            try:
                live.rename(set_aside)
            except OSError as error:
                raise ValueError(
                    f'The upgraded User Data could not be renamed ({error}). Close every program '
                    'using it and try again. Nothing was rolled back.'
                ) from error
        try:
            rollback.rename(live)
        except OSError as error:
            if set_aside:
                set_aside.rename(live)
            raise ValueError(
                f'The original User Data could not be renamed back ({error}). Nothing was rolled '
                'back.'
            ) from error
        staging = receipt.get('staging_user_data')
        if (
            staging
            and Path(staging).parent == live.parent
            and Path(staging).name.startswith('.incomplete-')
        ):
            shutil.rmtree(staging, ignore_errors=True)  # A copy left by an interrupted switch.
        receipt.update(
            status='rolled_back',
            rolled_back_at=_now().isoformat(),
            set_aside_user_data=str(set_aside) if set_aside else None,
        )
        storage.atomic_json(receipt_file, receipt, durable=True)
    return receipt
