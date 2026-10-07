# Changelog

## Unreleased

- **World Library review fixes:** previously saved snapshots without a compendium flag still open in the folder picker and import. Macro exports also detect legacy `flags.core.sourceId` when `_stats.compendiumSource` is empty.
- **Folder picker for the world import (W24):** the World Library page offers "Choose folders to import" before the one-click import. Folders made only of compendium copies start unticked; leaving the picker alone imports as before, and unticked documents stay searchable in the library.
- **Relay record check (W45):** `tools/check_relay.py` fails CI when a commit has a `Reviewed-PR:` line that git does not read as a trailer (for example above a separate `Co-Authored-By` paragraph), so a review can no longer be lost silently. Run it locally with `python tools/check_relay.py origin/main..HEAD`.
- **Macro snapshots mark compendium copies (W24):** the Foundry library export macro now sends the `compendium` flag for each document, so a world imported from a macro snapshot skips compendium copies by default, like one read from the world folder. Re-run the macro to refresh an older snapshot.
- **Big-map stocking in batches (W26):** a map with more than 30 areas is drafted in AI batches of up to 25 notable areas (anything but plain houses, plus areas with linked threads or entries); plain houses take a rollable template description and keep their starter loot. Counts in the brief are now maximums, shared across the batches, and a short draft applies with a note in its summary. A batch the validator rejects is rerun once with the validation message before the workflow fails, and a failed workflow resumes at its current batch. Small maps behave as before. The manual prompt and paste route still describes the whole map in one response.
- **Linked Foundry documents (W25):** when an export goes to the world an entry was imported from, the export carries the entry's Foundry UUID and Foundry-relative image path. The import macro links GM pages with `@UUID[...]` and leaves the existing actor or item untouched instead of creating a description-only copy. Entries from another world, or made in Studio, are created as before.
- **Audit fixes (W27):** linked entries in the map inspector show Foundry-imported portraits through the asset route, Handouts lists the campaign's real uploads folder under any `DM_HOME` name, and the forge folder joins `sys.path` once instead of on every request.
- **OpenAI API drafting:** Settings can choose Claude Code or an OpenAI Responses model for structured map and request proposals. The OpenAI worker uses the same cancellable job lane and GM review, sends no tools, and reads its API key from a named environment variable rather than saved settings. Restart recovery now scans every job record, so older queued jobs are not stranded after many newer jobs.
- **Foundry macros from any campaign location:** the import macro and upgrade scripts are served from the application folder at `/forge-scripts/<name>.js`; their download links no longer assume the campaign sits beside `DM/`.
- **Job provider seam:** `JobService` starts work through a runner (`SubprocessRunner` by default) with `start`, `feed`, `wait` and `terminate`, so another provider can replace the child process. Tests use a fake provider for success, failure, start failure with retry, and cancellation. No behaviour change for existing jobs.
- **Restart keeps queued jobs:** a job still waiting when the DM site stopped is requeued on the next start from a saved launch record (command and stdin only). A job that was already running is still marked failed.
- **Phone editing:** world-map pins can be placed at the center and adjusted in one-percent steps without dragging.
  Map pins and common controls have larger touch targets at phone widths; a touch-emulated Chromium flow checks
  world and battle-map placement. The README now gives a Caddy local-certificate HTTPS setup for phone access.
- **Large pushed snapshots:** an exported or live Foundry snapshot with more than 5,000 documents of one kind is now
  trimmed to a stable subset and reported as omitted, as the world-folder reader does, instead of being rejected.
- **Live World Library:** a GM-run Script macro opens a paired Studio tab. Approve it there to read the
  running Foundry client's permitted documents; later Foundry changes prompt an explicit refresh. Snapshots
  retain world provenance and use the existing Studio-edit conflict rule. No Foundry documents are written.
- **Job progress**: a child process can print `PROGRESS 3/10 label` or `PROGRESS 40% label`; `GET /api/jobs/<id>` returns the latest as `progress: {percent, label}` and the job card's bar uses it (it showed a guessed width before).
  Map rendering now emits these reports during painting, roofing and export; the latest report remains visible after later log output.
- **Foundry upgrade cutover review:** after a passing v12 module review, launch only the isolated clone in
  the selected newer Foundry build to let Foundry migrate it. The migrated-clone audit checks the installed
  releases and GM inspection. A final review rechecks that audit, the clone, the verified backup and the
  unchanged original User Data, then saves a manual cutover receipt. Studio does not move User Data or run
  Foundry; keep the v12 installation and backup for rollback.
- **Cancel** button on a running or queued job card. A queued job fails at once; a running one has its process terminated. The workflow, image brief or request it was producing is marked failed and can be retried. API: `POST /api/jobs/<id>/cancel`.
- **Draft related content** button on a codex entry: queues an `expand` request that Claude drafts in the background. The proposal can add public text and secrets to the entry, an illustration brief for the image queue, links to existing or new related entries, and new related entries. Nothing changes until you apply it, and applying twice adds nothing twice. Entries show their related entries as links (stored as extra fields, no schema change).
- **Phone and network access** (opt-in): start the server with `DM_BIND` and an `DM_ACCESS_CODE` (8+ characters)
  and other devices sign in once with the code (session cookie `HttpOnly`, `SameSite=Strict`; wrong codes lock
  an address for a minute). Without a code the site still answers only on localhost, and it refuses to listen
  on another address without one. The connection is plain HTTP; the README says how to add TLS.
  The login failure table is capped; when every slot is active, new addresses wait until a slot expires.
- **World map** page: upload a world map image, drop pins on it and link each pin to a battle map, then open
  the battle map from the pin. Pins are stored as fractions of the image in `data/world-maps.json`
  (data schema 3), and the document holds a list of world maps, so several can be added later.
- **Import world into Studio** (World Library, and the end of first-run setup) is one action: it reads the
  world folder and adds its actors, items and scenes to the codex with their Foundry UUIDs, reports how many
  were added, refreshed or kept, and counts the media available. Later imports refresh only entries you have
  not edited in Studio. The first-run result now stays visible, and an unreadable folder reports the macro
  fallback. Importing the macro snapshot also fills the codex. Imported entries are scoped to their source
  world, supported local Foundry images display through the read-only asset route, and skipped documents or
  media limits are reported. Foundry's files are only read.
- The World Library's LevelDB reader now follows `CURRENT` and `MANIFEST` to read only live tables and
  logs. A retired file left on disk during compaction can no longer make a deleted document reappear;
  manifest changes also trigger a fresh read when the page is reopened.
- After an interrupted schema migration, the retry's backup is marked `partly_migrated` and names
  the first attempt's complete backup (`first_attempt_backup`). A pending marker left after the schema
  version was recorded is cleared on the next start and cannot mislabel a later migration's backup.
- The migrated-clone audit now shows each selected module and dependency release alongside its installed
  version and saved/active state for package-by-package GM review.
- The World Library now reads a world's scenes, journals (with their pages), actors and items straight from
  its database files when you open the page, so the export macro is no longer needed. It reads Foundry 11+
  LevelDB folders with a new standard-library reader and Foundry 10-and-earlier `.db` files, opens nothing
  for writing, re-reads when the files change, and offers the macro as a fallback when a world cannot be
  read. A snapshot imported from the macro is never replaced automatically.
- Foundry media paths and backup manifests now share one relative-path rule (`storage.posix_parts`), and
  `forge.forge` reads a map's `key.json` once per run instead of three times.
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
- The generic document save (`PUT /api/doc/<name>`) now refuses `settings`, `foundry-library`, `workflows/*` and `jobs/*`
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
