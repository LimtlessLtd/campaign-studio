# Changelog

## Unreleased

- Added a read-only migrated-clone audit for Foundry v13/v14. A GM macro exports the isolated clone's build,
  package versions and activation; Studio compares them with the saved plan and records manual world checks.
- Added a GM-attested v12 clone module review after excluded modules are disabled. It checks both saved and
  active module states, installed package versions and the clone plan, then saves a review beside the backup.
- Added verified v12 clone preparation after a GM-confirmed restore test. The saved plan lists excluded
  modules, package releases and the target Foundry build; migration and cutover remain future steps.
- Fixed the v12 inventory macro to read `game.modules` as a Map and capture the world module configuration.
  The report merges installed module manifests from the verified backup, retains modules with conflicting
  activation evidence for GM review, and selects the newest eligible package release.
- Added a read-only v12 Foundry upgrade inventory macro and compatibility report. It compares stable builds,
  system and module releases, dependencies, directory exclusions and verification claims, retaining the
  report with a verified backup. Clone migration and cutover are still manual.
- Added a first-run Studio project and Foundry world picker, plus a read-only World Library for local media
  and GM-exported snapshots of scenes, journals, actors and items. Existing installations keep their setup.
- Added an offline Foundry User Data backup and isolated restore-test workflow in Settings. It checks for
  running Foundry processes, detects source changes during copying, verifies every file with SHA-256 and
  never overwrites live data. The actual Foundry version upgrade remains future work.
- General Requests now produce validated codex/session prep proposals for GM review before application.
  Claude runs without filesystem or shell tools; existing request records remain readable.
- Split the local server into HTTP routes, campaign operations, background job service and a small startup
  entry point. Existing HTTP APIs and stored job formats are unchanged.
- Added subprocess completion and restart recovery regression tests alongside the existing worker survival
  and integration checks.

## 0.1.1 — 2026-10-02

- Retry temporary Windows file replacement failures while holding the document lock; permanent failures
  still propagate after a bounded wait. Added sharing/permanent-failure regression tests.
- Ensure concurrent test subprocesses finish before temporary campaign cleanup, even on failure.

The v0.1.0 tag's release checks caught an intermittent Windows replacement failure and blocked publication.
0.1.1 is the first downloadable alpha release.

## 0.1.0 — 2026-10-02

Initial public alpha release.

- Map workspace with procedural layouts, imported images, location pins, linked content and checkpoints.
- Structured AI draft/review/refine/apply workflow and portable prompt/proposal exchange.
- Codex, story threads, session preparation, image queue, uploads and configurable image endpoint.
- Foundry v12 scene/journal export and D&D 5e NPC/item descriptions through a GM import macro.
- Self-contained source download with checksum, setup instructions, MIT license and privacy allowlist.
- Shared map catalogue process locking and recovery from job result-processing errors.
- Development agent instructions, architecture review, contribution templates and Windows/Linux CI.
