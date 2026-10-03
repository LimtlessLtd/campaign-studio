# Foundry backup and restore test

Campaign Studio has backup and compatibility report steps for upgrading a **local** Foundry world. It does
not yet upgrade a world or run Foundry's migration. Foundry must perform its own world migration in a later,
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

## v12 compatibility report: systems and modules

The Settings screen now has a **Foundry upgrade compatibility report** below the backup controls. Download
the GM inventory Script macro, run it inside the original v12 world, and import its JSON export. The export
records the game system, Foundry v12's world-eligible modules, and the world's saved module configuration.
Studio adds modules missing from that API view by reading their `module.json` files in the verified backup.
It reports enabled state, versions, manifests, declared compatibility, relationships and locks. Missing
enabled modules or invalid installed manifests stop the report. Enter a verified backup folder of the same
world. The report reads
Foundry's public stable release list, official package directory release rows and linked release manifests.
It saves the original inventory and generated JSON report beside that backup, outside its `User Data` copy.
These pages and manifests require network access; unavailable or unreadable package data remains unknown and
cannot count as a full match. The report is metadata evidence, not an installer or runtime test.
If the saved module configuration and v12's active state disagree, the report flags the IDs and conservatively
counts either enabled indication as enabled until the GM resolves the mismatch in the clone.

The report lists every enabled module decision, all candidate stable builds and their blockers. To compare a
newer build after a conflict, the GM can explicitly enter comma-separated module IDs to disable or dependency
IDs to approve for the _future clone_ and run a new report. These choices do not change either world. Keep
each report beside the backup so the original inventory and choices remain reviewable.

The upgrade wizard must treat the game system as a prerequisite and produce a decision for **every module
enabled in the original world**. It must search stable Foundry builds from newest down to the world's current
v12 build, then recommend the newest build that can retain the world system and every eligible module
originally enabled. Record the exact target build and the selected system and module releases. Updating the
game system's version is allowed; changing its package ID or dropping an eligible module requires an explicit
GM choice.

Before changing a clone, a GM-side export must record the installed modules, which ones are enabled in the
world, their versions and manifest URLs, declared Foundry compatibility, required dependencies, system
relationships and package locks. A file scan alone cannot establish which modules are enabled. Keep the
pre-upgrade inventory and backup together with the migration report.

For this workflow, **directory-listed** means a package ID found in Foundry's official package directory;
it does not mean Foundry staff developed or tested the module. Packages absent from that directory are
excluded from automatic migration. Plutonium is explicitly excluded regardless of directory status. The
clone should disable excluded modules through Foundry v12's module controls **before its first launch in
the newer Foundry version**. An unknown listing result must be shown as unknown and must not be treated as
approval. Excluded modules remain in the untouched backup; the wizard must not delete their data or change
the live v12 world. If an eligible module requires an excluded module, report the conflict and its impact
before disabling that dependent module in the clone.

For each candidate Foundry build, find a compatible release of the game system and the newest available
release of each retained module using Foundry's package release information and installer. Check
`compatibility.minimum` and `compatibility.maximum`, required modules, game-system constraints and the
entire dependency chain together. Do not count a build as a full match if a retained module or required
dependency has no eligible release. Modules that were previously installed but disabled do not constrain
the search and must remain disabled, except dependencies approved for a retained module.

`compatibility.verified` is advisory, not a hard limit. If the highest full match has packages unverified on
that build, label it **needs clone testing** and also show the newest build on which all retained packages
declare verification, if one exists. The GM can compare those results before migration. If no newer stable
build can retain the system and eligible modules together, show the specific blockers and offer either to
stay on v12 or explicitly choose which modules to disable for a newer build. Never silently drop a retained
module or assume that an unverified module works.

The report must list original and selected versions, original and proposed enabled states, directory
status, compatibility evidence, dependencies, candidate Foundry builds, and each disabled reason. After
Foundry migrates the clone, the GM must test world launch, key scenes, journals, actors, items and module
behavior. Declared metadata cannot guarantee runtime compatibility. Cutover is offered only after the backup
and restore test, module report, isolated migration and GM checks pass. Rollback restores the v12 backup
using the retained v12
installation; it never attempts to open a migrated database in v12.

Foundry's [package management guide](https://foundryvtt.com/article/package-management/) documents the
directory, version selection and compatibility fields. Its [module management guide](https://foundryvtt.com/article/modules/)
documents activation and dependency behavior. Studio creates the v12 clone, while opening and inspecting
the restored copy, disabling excluded modules through v12 controls, installing selected package releases,
running Foundry migration and checking the migrated clone remain manual. Studio does not certify package
behavior.

## Prepare an isolated v12 clone

**Create restore test copy** now saves a receipt beside the backup. Open that copy with the retained v12
installation, inspect its scenes, journals, actors, items and assets, then return to Studio. The GM must confirm
this manual check; the receipt only proves the copy passed checksum verification when Studio made it.

In **Prepare an isolated v12 clone**, provide the saved report path, restore receipt path and a new clone
destination. Review the report and confirm the v12 restore test. Studio re-verifies the backup, checks that
the report and receipt belong to it, and makes another complete, verified copy. It saves a clone plan beside
the backup with the target build, selected package releases, module activation choices and the exact enabled
modules to disable. The clone remains at v12. No module or database changes are made by Studio.

Next, launch **only the new clone** with Foundry v12 using its separate User Data path. In Manage Modules,
disable the plan's excluded modules, including Plutonium, and save and reload. Check that they are off and
that required eligible modules remain on. Keep the backup and v12 installer intact. Installing selected
releases, starting a newer Foundry build and running its migration remain manual. Studio can audit the clone
after those steps, but live cutover is not implemented. Do not open the clone in a newer build before its v12
module review is complete.

## Review the v12 clone's module state

After saving and reloading the isolated clone in Foundry v12, run the GM inventory macro **in that clone**
again. In **Review v12 clone modules**, select the saved clone plan, import the fresh JSON, and confirm where
it was exported. Studio checks the saved module configuration against Foundry's active module state, the
plan's retained and excluded modules, and installed system/module versions. It saves the input and a review
beside the backup. A blocked review lists each mismatch; resolve it in the v12 clone and export again.

This review does not change packages or the world. The macro does not prove which User Data folder produced
its export, so the GM must confirm the clone source. A passing review establishes only that the v12 module
state and installed versions match the plan. It does not mark migration ready: package installation, the
newer Foundry launch, runtime checks and cutover are still separate steps.

## Audit the migrated clone

The passing v12 review shows the exact system and module release manifests selected by the compatibility
report. Use Foundry's package management in the isolated installation to install those releases, including
approved dependencies, and keep excluded modules disabled. Foundry itself must launch and migrate **only the
clone** in the selected newer build; Studio does not install releases, launch Foundry or edit database files.

After migration, run the **migrated-clone audit macro** as GM in the clone on Foundry v13 or v14. Import its
JSON in Studio with the passing v12 review. Inspect the migrated world and confirm launch, key scenes and
assets, journals, actors/items and retained module behavior. The audit compares the running build, world
manifest, installed system/module versions, and both configured and active module states with the saved
plan. It records blockers and keeps the audit beside the backup. An unknown or extra active module,
including Plutonium, blocks the audit.

An audit marked **reviewed** records metadata agreement and the GM's checks; it does not certify every
document or module feature and does not perform live cutover. Keep the verified v12 backup, restore test
receipt and v12 installation for rollback. Foundry's [v14 Game API](https://foundryvtt.com/api/v14/classes/foundry.Game.html)
documents the module collection and running version used by the GM macro.
