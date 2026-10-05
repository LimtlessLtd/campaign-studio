# Changelog

## Unreleased

- The GM import macro now selects explicit Foundry v11 or v12/v13 scene and roof adapters, and a D&D 5e
  sheet or journal-only system adapter. Fixture contracts exercise import and reimport for each combination;
  untagged GM journal pages are no longer overwritten by a same-named generated page, and pins resolve their
  generated pages by ownership tag. A live GM checklist
  and the v14 limitation are documented.
- Package metadata downloads now connect to the public address the safety check approved, so DNS rebinding
  between the check and the connection cannot reach a private address. Each redirect is pinned the same way;
  environment proxies are bypassed so the connection always uses that address directly.
- Browser smoke tests (Playwright and axe-core) now cover the first-run wizard, pin editing, proposal
  review and narrow navigation, and run in CI. They found and fixed low-contrast sidebar captions and
  workflow steps, and unlabelled prep notes and new-request controls.
- Job cards stop polling through the page's own signal instead of the router's controller, and an unused
  battle-map form left over from the old studio is removed.
- Split the browser into document state/autosave, reusable controls and page controllers without a build
  step. Navigation now aborts stale page reads and keeps each page's view private until it is current.
- The generic document save (`PUT /api/doc/<name>`) now refuses `settings`, `workflows/*` and `jobs/*`
  with 403. Those change only through their own routes, which validate them; the browser never saved
  them through this route.
- `foundry_upgrade` is now the upgrade workflow module itself instead of a facade over it. The facade
  imported helpers and re-wrapped `report` only so tests could patch them; tests now patch
  `foundry_catalog.fetch` and import catalogue, solver and compatibility helpers from their own modules.
- Split Foundry upgrade metadata collection, compatibility solving and inventory/clone workflows into
  focused modules. The existing upgrade API and saved evidence formats are unchanged.
- HTTP requests now dispatch through a route table with small handlers. Invalid, missing and conflicting
  requests have distinct typed 400/404/409 responses; stale document saves still include the latest
  revision and document so the browser can merge edits. Existing workflow HTTP tests remain unchanged.
- `DM_HOME` runs Campaign Studio on a campaign folder outside the app folder. One `Campaign` object now
  locates every campaign file, and renders, generators and image jobs work on the campaign that queued
  them. Uploaded images, map imports, previews and checkpoints store paths relative to the campaign's
  real folder name instead of assuming `DM/`.
- Coding agents now audit every merged PR for design as well as bugs and fix what they confirm, track work
  in `docs/BACKLOG.md`, and coordinate claims and owner feedback in Slack (`docs/DEVELOPMENT.md`).
- Fixes from reviewing the journal, migration and autosave work:
  - Typing in a checklist item or loot row while an AI request added items to the same list could be
    lost after showing "Saved". List merges now keep the items being edited and count repeated values, so
    a second "Potion" added while another was removed elsewhere is kept. Editing or removing a goal or a
    linked character no longer affects a different entry after a merge moved it.
  - A finished render, image or AI draft is kept when an interrupted change cannot be completed at that
    moment, instead of being marked failed.
  - `python DM/migrate.py --apply` now leaves an interrupted change for the server, which can run the render
    it queues. A file the pre-migration backup cannot copy (such as a symbolic link) stops startup with a
    clear message instead of an error trace, and no partial backup is left.
  - A new campaign records its data schema with its first document, so an older build refuses it.
  - Documents written by a journaled change are flushed to disk before its record is deleted, and deleting
    that record rides out brief Windows file locks instead of repeating its render later.
- Each stored record (codex entry, thread, art brief, session prep, scene, handout, checklist item, loot,
  map key, location, journal entry and event) is now defined once in `DM/shapes.py`. AI content and request
  application, layout application, map import and the browser all build records from it, so records no
  longer differ by where they were created: new locations from layouts now have rooms and threads lists,
  new session preps have a handouts list and map events have a title. Schema 2 completes older records
  with any missing field; threads that had no status are now listed as open instead of disappearing from
  the threads page.
- Development process: coding agents no longer wait for a human review. Each agent reviews the PRs merged
  since the last `Reviewed-PR:` trailer, fixes what they broke, then merges its own PR once CI is green.
  Releases, CI permissions and publishing campaign data still need the owner. See `docs/DEVELOPMENT.md`.
- Fixed autosave merges losing work: when a request added goals, checklist items or loot to a session prep
  while it was open and edited, saving kept only one side. Lists without IDs now merge both sides, fields
  cleared on the server stay cleared, and edits typed while a save is in flight are no longer overwritten.
- A late job failure no longer marks an image or draft as failed after it has finished or moved on, and the
  Foundry `wotg-maps` index and scene files are written atomically by one shared writer.
- Internal: review-driven cleanup of module boundaries (shared file helpers in `storage`, no private
  cross-module calls, workflow orchestration in `campaign_core`). See the design review in
  `docs/CODE_REVIEW.md`.
- Changes that span several documents (content and request application, layout application, revision
  restore and generated-image links) now use a write-ahead journal. An interrupted change is completed when
  the server restarts or before the next change; if a document was edited meanwhile, nothing is overwritten
  and the dashboard asks the GM to review it. Map plan edits and restores are now atomic.
- Added a versioned data schema. On first start, existing 0.1.x data (schema 0) is copied to `DM/backups`,
  verified by SHA-256 and migrated to the current schema, which fills missing list/text fields in stored
  records. Data from a newer version is refused.
  `python DM/migrate.py` reports, applies or restores migrations.

  **Migration note:** keep `DM/backups` with your runtime folders. To return to 0.1.x, stop the server,
  run `python DM/migrate.py --restore DM/backups/<schema-0-to-2 folder>` and start the older version.

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
