# Foundry backup and restore test

Campaign Studio now has the first safety step for upgrading a **local** Foundry world. It does not yet
upgrade a world or run Foundry's migration. Foundry must perform its own world migration in a later,
isolated test copy before any live cutover.

## Before creating a backup

1. In **Settings & Foundry**, save the local world folder under `User Data/Data/worlds/<world-id>`.
2. In Foundry Setup, take a **Snapshot**. Foundry recommends one before a major version upgrade.
3. Close Foundry completely. A manual copy of an active world database can be inconsistent.
4. Keep the currently working Foundry installer or portable installation. A data backup alone cannot
   run the world if the new Foundry version has migrated it.

Foundry Snapshots cover installed Worlds, Systems and Modules, but can miss assets outside those package
folders. Campaign Studio's offline copy includes **every regular file and empty directory in the selected
world's entire User Data folder**, including `Data`, `Config`, `Logs`, `Backups` and assets elsewhere in that
folder. Links and special files cause a failure rather than a silent omission. Assets outside User Data
must be backed up separately.

## Create and verify

Open **Foundry backup and restore test** in Settings, review the scanned world, file count and size, choose a backup
destination outside User Data, and confirm Foundry is closed. **Create and verify full backup** creates a
new timestamped folder without overwriting an earlier one. The app checks for recognizable running local
Foundry processes, checks that source files did not change during the copy, and SHA-256 verifies every file
in the backup. It also retains empty directories. If the copy fails, a `.incomplete-*` folder remains clearly
marked and cannot be used for restore testing. You can move a successful backup to another drive and use
**Verify backup again** before relying on it.

This is an integrity check, not proof that Foundry can open the world. Process detection cannot guarantee
that no custom server is writing the data, so close any Foundry service using this User Data path yourself.

## Test a restore before upgrading

Enter a **new, nonexistent folder** and click **Create restore test copy**. Campaign Studio verifies the
backup, copies it to the separate folder, and verifies the result. It never overwrites live User Data. Then
launch the original Foundry version with the restored folder as its separate User Data path (Foundry's
`--dataPath` option is one way to do this). Open the world and inspect scenes, journals, systems, modules
and assets. For a v12 world, test with the working Foundry **v12** installation before a later
v12-to-latest upgrade. Only after this manual launch and inspection is rollback actually tested.

If a later upgrade fails, stop the new Foundry version, preserve its changed User Data separately, restore
the verified pre-upgrade User Data copy, and launch the original Foundry version. Changes made after the
backup will not appear in the restored world. Campaign Studio does **not** yet automate this live rollback.

Foundry's own instructions: [Backups and Snapshots](https://foundryvtt.com/article/backups/),
[Backing Up and Moving User Data](https://foundryvtt.com/article/user-data-backup/), and
[Installation Guide](https://foundryvtt.com/article/installation/).
