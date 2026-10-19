"""The upgrade wizard's post-migration repair and readiness step, between the audit and cutover.

On the migrated clone named by a passing audit, with Foundry closed: a dry run lists what
`foundry_repair` would change; applying it backs the clone's world folder up, writes the reviewed plan
through Foundry's own LevelDB library and verifies the result against that backup, restoring the
changed databases if anything fails. The encounter smoke test then rolls the party's and the chosen
scenes' actions. Each step saves its evidence beside the upgrade backup, as the earlier steps do.
"""

import datetime
import json
import subprocess
import uuid
from pathlib import Path

import foundry_backup
import foundry_leveldb_write
import foundry_repair
import foundry_smoke
import foundry_upgrade
import storage

SCAN_FORMAT = 'campaign-studio-foundry-repair-scan'
REPAIR_FORMAT = 'campaign-studio-foundry-repair'
SMOKE_FORMAT = 'campaign-studio-foundry-smoke-test'
SCAN_LIMIT = 16 * 1024 * 1024


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _closed(confirmed):
    if confirmed is not True or foundry_backup.running_foundry():
        raise ValueError('Close both Foundry installations before checking or repairing the clone.')


def _save(folder, prefix, record, key):
    path = Path(folder) / f'{prefix}-{uuid.uuid4().hex}.json'
    record[key] = str(path)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2, ensure_ascii=False)
    return record


def scan(audit_path, confirmed_closed=False):
    """Dry run: what the repair would change in the migrated clone. Writes only its report."""
    _closed(confirmed_closed)
    backup, audit_file, audit, _ = foundry_upgrade.passing_audit(audit_path)
    clone, world = foundry_upgrade.clone_world(backup, audit)
    documents = foundry_repair.load(world, (*foundry_repair.COLLECTIONS, 'folders'))
    findings, _ = foundry_repair.plan(documents)
    record = {
        'format': SCAN_FORMAT,
        'scanned_at': _now(),
        'audit_path': str(audit_file),
        'audit_sha256': storage.sha256_file(audit_file),
        'backup_path': backup['path'],
        'clone_path': str(clone),
        'world_path': str(world),
        'target_build': audit['target_build'],
        'fingerprint': foundry_repair.fingerprint(
            {name: documents[name] for name in foundry_repair.COLLECTIONS}
        ),
        'summary': foundry_repair.summary(findings),
        'findings': findings,
        'smoke_choices': foundry_smoke.choices(documents),
    }
    return _save(backup['path'], 'clone-repair-scan', record, 'scan_path')


def _scan(scan_path):
    """A saved dry run and the passing audit it was made from, both unchanged."""
    selected = foundry_backup.absolute_folder(scan_path, 'Repair dry run')
    if not selected.is_file() or selected.stat().st_size > SCAN_LIMIT:
        raise ValueError('Select a saved repair dry run.')
    preliminary = json.loads(selected.read_text(encoding='utf-8'))
    if not isinstance(preliminary, dict) or preliminary.get('format') != SCAN_FORMAT:
        raise ValueError('Select a saved repair dry run.')
    backup, audit_file, audit, _ = foundry_upgrade.passing_audit(preliminary.get('audit_path'))
    clone, world = foundry_upgrade.clone_world(backup, audit)
    if (
        selected.parent != Path(backup['path'])
        or preliminary.get('scan_path') != str(selected)
        or preliminary.get('audit_sha256') != storage.sha256_file(audit_file)
        or preliminary.get('world_path') != str(world)
        or preliminary.get('target_build') != audit['target_build']
    ):
        raise ValueError('The dry run does not belong to this audit and clone. Run it again.')
    return selected, preliminary, backup, world


def _untouched(snapshot, world, touched):
    """Files of the world outside the written databases that differ from the snapshot."""
    manifest = foundry_backup.verify_snapshot(snapshot)
    prefixes = tuple(f'data/{name}/' for name in touched)
    return [
        record['path']
        for record in manifest['files']
        if not record['path'].startswith(prefixes)
        and (
            not (world / storage.manifest_path(record['path'])).is_file()
            or storage.sha256_file(world / storage.manifest_path(record['path']))
            != record['sha256']
        )
    ]


def apply(scan_path, installation, choices=None, confirmed_closed=False, confirmed_reviewed=False):
    """Back up the clone's world, write the reviewed repairs and verify them against the backup."""
    _closed(confirmed_closed)
    if confirmed_reviewed is not True:
        raise ValueError('Review the dry run before repairing the clone.')
    scan_file, saved, backup, world = _scan(scan_path)
    install = foundry_leveldb_write.installation(installation, saved['target_build'])
    documents = foundry_repair.load(world)
    if foundry_repair.fingerprint(documents) != saved['fingerprint']:
        raise ValueError('The clone changed after the dry run. Run the dry run again.')
    findings, changes = foundry_repair.plan(documents, choices)
    record = {
        'format': REPAIR_FORMAT,
        'status': 'nothing_to_repair',
        'repaired_at': _now(),
        'scan_path': str(scan_file),
        'scan_sha256': storage.sha256_file(scan_file),
        'backup_path': backup['path'],
        'world_path': str(world),
        'target_build': saved['target_build'],
        'choices': choices if isinstance(choices, dict) else {},
        'summary': foundry_repair.summary(findings),
        'written': {name: len(rows) for name, rows in changes.items()},
    }
    if not changes:
        return _save(backup['path'], 'clone-repair', record, 'repair_path')
    found = foundry_leveldb_write.runtime(install)  # Fails before anything is copied or written.
    record['runtime'] = {'classic_level': found['classic_level'], 'node': found['node']}
    evidence = Path(backup['path']) / f'clone-repair-{uuid.uuid4().hex}'
    snapshot = foundry_backup.snapshot(world, evidence / 'world-before')
    record['snapshot_path'] = snapshot['path']
    _closed(True)  # Foundry may have started while the snapshot was copied.
    batches = foundry_repair.operations(documents, changes)
    try:
        result = foundry_leveldb_write.write(
            [(world / 'data' / name, ops) for name, ops in batches], install, found
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # The writer stopped without saying what it wrote, so treat every database as changed.
        result = {'ok': False, 'committed': list(changes), 'error': str(error)}
    record['error'] = result['error']
    record['verification'] = None
    if result['ok']:
        record['verification'] = _verify(documents, changes, snapshot['path'], world)
    if result['ok'] and record['verification']['ok']:
        record['status'] = 'repaired'
    elif not result['ok'] and not result['committed']:
        record['status'] = 'failed'  # Nothing was written; the databases hold what they held.
    else:
        record['status'] = 'restored'
        try:
            record['restored'] = foundry_backup.restore_snapshot(
                snapshot['path'], [f'data/{name}' for name in changes], evidence / 'replaced'
            )
            restored = foundry_repair.load(world)
            record['restore_verified'] = foundry_repair.fingerprint(
                restored
            ) == foundry_repair.fingerprint(documents)
        except (OSError, ValueError) as error:
            record['status'] = 'restore_failed'
            record['error'] = (
                f'{record["error"]} Restoring the backup failed too ({error}). With Foundry '
                f'closed, copy the world folder back from {snapshot["path"]}.'
            ).strip()
    return _save(backup['path'], 'clone-repair', record, 'repair_path')


def _verify(documents, changes, snapshot, world):
    """Check the written world against the documents it was planned from and the snapshot."""
    try:
        verification = foundry_repair.verify(documents, foundry_repair.load(world), changes)
        changed = _untouched(snapshot, world, changes)
    except (OSError, ValueError) as error:
        return {
            'ok': False,
            'checks': [{'name': 'Read the repaired world', 'ok': False, 'detail': [str(error)]}],
        }
    verification['checks'].append(
        {
            'name': 'Other world files unchanged',
            'ok': not changed,
            'detail': changed[:20] or ['Every other file matches the backup'],
        }
    )
    verification['ok'] = verification['ok'] and not changed
    return verification


def smoke_test(scan_path, party_folder, scene_ids):
    """Roll the party's and the chosen scenes' NPCs' actions in the clone. Reads only."""
    selected = foundry_backup.absolute_folder(scan_path, 'Repair dry run')
    backup = selected.parent
    foundry_backup.read_manifest(backup)
    _, saved = foundry_upgrade.sidecar(selected, backup, 'clone-repair-scan-', SCAN_LIMIT)
    world = foundry_backup.absolute_folder(saved.get('world_path'), 'Clone world')
    clone = foundry_backup.absolute_folder(saved.get('clone_path'), 'Upgrade clone')
    if saved.get('format') != SCAN_FORMAT or not foundry_backup.within(world, clone):
        raise ValueError('Select a saved repair dry run.')
    report = foundry_smoke.run(world, clone, party_folder, scene_ids)
    record = {
        'format': SMOKE_FORMAT,
        'tested_at': _now(),
        'scan_path': str(selected),
        'world_path': str(world),
        **report,
    }
    return _save(backup, 'clone-smoke-test', record, 'report_path')
