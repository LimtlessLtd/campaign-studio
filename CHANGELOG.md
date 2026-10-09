# Changelog

## Unreleased

- **Story thread links and clues (W34):** threads can link maps and their numbered location pins, keep clues with a planned/planted/found state and where to find them, and sort by linked hero. A pin link opens its location on the map. Map trash removes those links and restore puts them back. Schema 15 fills older threads with empty lists; a verified migration backup precedes any changed record. Synthetic storage, route and phone browser checks passed.
- **Codex as a draft provider (W73):** Settings can choose **Codex** beside Claude Code and the OpenAI API. It drafts on your ChatGPT subscription through the signed-in `codex` command, with no API key: the key variables are removed from Codex's environment and a Codex signed in with an API key is refused. Codex is a coding agent with no switch that removes its tools, so `DM/tools/codex_worker.py` starts it with the tool features off, a read-only sandbox, no user configuration and an empty working folder, then reads its event stream and fails the draft at the first event that is not the model thinking or answering. The result is the same envelope as the other providers (draft plus tokens; no price), so review, Cancel, usage and the automatic run work unchanged. The kind-to-strict-schema table moved from the OpenAI worker to `DM/draft_schemas.py` so both providers share it. Page text that named Claude Code now says "your signed-in AI". Tested with a fake `codex` command only (login, features, event stream, a tool request, a failure, a timeout); nothing was run against a real Codex, so W76 records what a live draft shows.
- **Review #88:** audited the automatic run; two defects fixed. A finished run's AI-request count and cost kept growing with the GM's other drafts (it counted every AI job created after the run began; it now counts the jobs on the run's own recordings, arc proposal and request). Retrying a failed arc proposal removed it first, so a refusal to start the new one (for example Claude Code missing) left the run pointing at a removed proposal, which it read as the GM skipping arcs and went on to draft the next session without them.
- **Automatic run (W72):** the Recordings page has an **Automatic run** that takes the new recordings of one session through local transcription, sorting play from banter, the thread ledger, arc options for loose threads and a Session Forge draft of the next session, and stops at every review (passages, ledger, arc options, draft): applying a review starts the next step, and nothing changes the campaign before you apply. **Plan a run** shows the files (newer than the last recording transcribed; only the newest the first time), the session (the newest prep with no log) and the sorting requests already known before anything starts. It asks Claude for one request at a time, never retries a failure or a usage limit by itself (**Try again** resumes where it stopped), reports requests, tokens and the cost Claude Code reports, and treats steps done by hand as done. A run is one small document (`auto-runs/<id>`, schema 14); progress is read from the transcripts, ledgers, arcs and request, so it cannot disagree with them. `DM/tools/auto_run_client.py` presses Run from a scheduled task and prints what is working, what waits for you and why a run stopped (exit 2 when stopped). `usage.combined` totals jobs across months. Synthetic tests cover the policy, a whole session with fake Whisper and Claude, failures, limits, restarts of a step, the routes and the client, and a phone and desktop Chromium flow with axe; a real recording and a live Claude run have not been checked.
- **Review #85 and #87:** audited the thread ledger and the story arcs; no defects. One design fix: each new draft kind had copied an `if` branch into both `finish_job` and `settle_failed_job` (classify, thread ledger, arc options, and the ledger's failure handling inline). Both now dispatch through tables (`FINISHERS` by job kind, `SETTLERS` by the record field a job carries), so the next kind adds a row. No behaviour change.
- **Story arcs (W71):** the Story arcs page lets you choose up to five loose threads and asks the signed-in Claude Code for a resolution, an escalation and a twist for each, naming the codex entries and heroes they use. You reword and choose at most one option per thread; applying plans those threads (the plan and its hook go into the thread's details) in one recoverable change, refuses a thread edited since, and the options' pitch lines are offered in the Session Forge pitch box. Schema 13 adds `arcs/<id>`. The OpenAI provider now knows the thread-ledger kind (it failed the job before) and a test keeps every queued draft kind in step with it; radio buttons no longer stretch across the page. No live Claude draft was checked.
- **Review #84 and a page-load race:** audited the merged transcript sorting; its two defects were fixed before it merged. A browser request that was cancelled while its answer was still arriving (leaving a page mid-load) used to read as an empty answer and could replace the codex or thread lists with an object, the likely cause of one intermittent CI failure in the World Library test; it now rejects and a list that is not a list is refused. `tools/check_state.cjs` covers both.
- **Thread ledger (W70):** from GM-confirmed in-game passages only, Claude proposes evidence-backed thread status changes, codex notes and session outcomes in bounded windows. Each proposed item must quote its passage exactly; nothing writes before the GM selects and applies items. Apply checks transcript and target revisions, then commits selected changes with the applied marker as one recoverable operation. A stale draft can be explicitly redrafted. The Recordings page shows unresolved threads by staleness and hero with quoted timestamps. Existing transcripts can be linked to a prep without transcription again. Schema 12 adds separate ledger documents. Synthetic unit and phone/desktop Chromium checks passed; no live Claude or long-recording run was made.
- **Play or table banter (W69):** each transcript on the Recordings page can be sorted by the signed-in Claude Code (`claude -p`, no API key). Studio shows the number of requests and characters first and asks; it then reads the transcript window by window (up to 30,000 characters each, within the context budget), one queued job per window so Cancel, the usage ledger and restart recovery work as they do for other drafts. A window that fails stops there and **Continue sorting** resumes from it. Claude proposes passages marked in-game, table banter or unclear; whatever it leaves out becomes unclear, so nothing is read as play by omission. The GM reviews them in a paged list (excerpt, time range, **In-game**, **Table banter**, **Read in context**, **Undo**, and a bulk confirm) and nothing counts as in-game until confirmed: `transcript_classifier.confirmed_play` is the only output of this step, for the thread ledger (W70). Confirming banter with a note saves it as table lore (`table-lore`, at most 200 notes): later runs show the notes to Claude, which marks matching passages as known lore that the review list sets aside; removing a note, or undoing the banter that saved it, unlinks every passage that matched it. Transcribing a sorted recording again asks first because it discards the passages. Schema 11 adds `passages` and sorting progress to each transcript (verified backup first). Tested with a fake Claude runner on a synthetic session (a made-up horse form against a destroyed church), including failure, cancel, restart and resume, and in Chromium at phone and desktop widths with axe; no live Claude call was made, so how well real Claude separates banter from play is not yet checked.
- **Review #82:** audited the local transcription change; no issues. (Noted for W69: transcribing again replaces a transcript, which would discard its passages; that now needs an explicit confirmation.)
- **Session recordings, local transcription (W68):** a Recordings page lists the videos and audio in a folder (default: the campaign's `Session recordings` folder) and transcribes one on this computer with faster-whisper or whisper.cpp, chosen in Settings. No account, API key or upload is involved: the recording is read where it lies and never copied (extracted audio lives in a temporary folder removed when the job ends), and a faster-whisper model is downloaded only if you allow it. The job runs on its own `transcribe` lane with progress and Cancel, which now stops the engine's child programs too; a restart requeues a waiting job and fails one that was running. The result is a bounded, timestamped transcript (`transcripts/<id>`, schema 10 with the usual verified backup), one per recording file, read in pages and removable without touching the recording. `storage.local_path` is now shared by memory import and recordings, and reports a missing path as a normal error. Engines are faked in tests (a stand-in `faster_whisper`, whisper.cpp and ffmpeg programs); a real model run and real recording are not yet checked.
- **Session recap memory (W30):** Session Forge recap prompts use the newest three earlier session logs as canon. The selected prep's own log and any later session logs are excluded. A synthetic request-pack regression covers the prompt context; no paid draft was run.
- **Plan: session recordings to next session (W68–W73):** the roadmap and backlog now cover local Whisper transcription, banter-versus-play classification, a thread ledger with evidence, arc and resolution proposals, an automatic between-sessions run and a Codex provider check, all subscription-only. Docs only; the old W68 row is replaced and W37's encounter-budget remainder is parked.
- **Review #80:** audited the party-in-drafts change; no issues.
- **Party in AI drafts (W37, second slice):** once a world is imported, every general, session and map draft prompt carries `campaign.party` (members, size, average level) and asks for encounters sized to it; maps made by Session Forge take the party's rounded average level as their brief's party level (5 when no party is known). Budget checks against the party remain on W37.
- **Session Forge (W32):** a prep's "Plan whole session" action takes a pitch, length, combat/social mix and selected threads. A bounded structured proposal contains a recap, three to six linked scenes, up to two new map briefs, cast, handouts, thread changes, loot and a checklist. The GM reviews the whole draft before one recoverable apply; IDs and an apply marker prevent duplicates. New maps wait as linked layout workflows and art waits in the image queue. Schema 9 fills new prep, scene and handout fields after a verified backup. Synthetic apply, crash recovery, browser and map workflow checks pass; no paid AI or live Foundry call was made.
- **Memory import relay fix (#75):** malformed legacy record types and session IDs now produce sanitised candidates or a clear validation error instead of a server error.
- **Party roster from Foundry characters (W37, first slice):** reading a world folder now also reads each character actor's class items (`!actors.items!`; other embedded items are dropped as they are read) and stores `stats` on the snapshot record: total level, classes with levels, armor class and maximum hit points. `foundry_party.party()` totals the roster and average level. The World Library shows a character's stats line. Snapshots made by the export macro carry no stats, and older snapshots still load. Using the roster in encounter budgets and map briefs remains on W37.
- **Thread links and staleness (W34, first slice):** each thread can link codex entries and the sessions it touched, and shows "Last touched in session N". The threads page has an Order menu (by title or stalest first; `GET /api/records/threads?sort=stale`), and a codex entry lists the threads, scenes and map areas that use it. Deleting an entry also unlinks it from threads. Campaign data moves to schema 8 on start-up, with the usual verified backup first. Links to maps and locations, clues and sort by hero remain on W34.
- **Review #78 integration:** Session Forge ranks threads using their touched sessions and marks new or changed threads with the planned session. Its shape migration follows W34 as schema 9.
- **Item-by-item proposal review (W33, first slice):** each new entry, thread, scene and handout in a general request's proposal has an "Include when applying" tick box. `POST /api/requests/<id>/apply` takes `rejected` (`kind:id` keys); only the rest is written, scene links to a rejected NPC and focus links to a rejected entry are dropped, an unknown key or an empty remainder is refused, and the proposal is validated again. Editing stays in "Edit proposal" (JSON), which validates again. Before/after diffs, "redraft this one" and map-workflow and memory-import proposals are still open under W33.
- **Review #76 integration:** a revised general request clears old item choices. Session Forge remains a whole-proposal review until W33 covers its linked proposal; the API refuses item exclusions for sessions.
- **Reviewed memory import (W31):** the Import memory page previews older Studio/DM-screen codex and threads, JSON/Markdown session summaries, and selected Foundry journal folders. The GM selects candidates before one recoverable apply; stable IDs and log collision checks make reruns safe. Sources and Foundry remain read only.
- **World removal review:** removing imported data now leaves the World Library snapshot absent across navigation instead of immediately reading it back. The remove button disappears when no imported data remains, and bulk unlinking scans references once for the whole removed set.
- **Remove an imported world (W67, second slice):** the World Library page has "Remove imported data" (`POST /api/foundry/world/remove` with the world key). One recoverable commit deletes the world's codex entries (each unlinked from other entries, threads, preps, art and requests) and its library snapshot, whose last copy stays in history. The Foundry world folder is never written to. Entries imported from other worlds and entries made in Studio stay. Review relay: owner feedback posts are now marked with a reaction once folded in.
- **Map drafts read session logs (W30, second slice):** AI map drafts (layout and content) receive the newest three session logs in the campaign block, and the prompt tells the AI to treat them as canon.
- **Session log (W30, first slice):** each prep has a "Session log" (what the players did, GM notes, outcomes). General request drafts receive the newest three logs from other sessions as `recent_session_logs`, and the prompt tells the AI to treat them as canon. Campaign data moves to schema 6 on start-up, with the usual verified backup first (schema 7 followed with W28). New row W68 (transcribe session videos).
- **Remove a world map (W67, first slice):** the world map page has "Remove world map", which deletes only the Studio nomination and its pins after a confirmation. Battle maps and Foundry are untouched. New rows W66 (AI pins for named scenes) and W67 (never delete from Foundry; unlink everywhere).
- **Rename a world map (W29, last slice):** the world map page has "Rename map". Pins reference the map by id, so they keep working. Preps and map trash, codex delete and prep archive were earlier slices; world maps still have no trash.
- **Selected record highlighted (W62, World Library):** the entry you open in a World Library list (scenes, journals, actors, items, media) is highlighted and marked `aria-pressed`, per category, and stays highlighted after changing page, searching or reloading the tab (kept in session storage). Other lists open their record on its own page, where the sidebar already marks the current section.
- **Per-record codex and thread storage (W28):** schema 7 migrates legacy collection files into individual records after a verified backup. Codex and thread pages now list bounded pages, and an edit PUTs only its record with revision conflict handling and per-record history. Foundry import and AI applies keep multi-record changes in the recovery journal; codex deletion also unlinks references through that journal. Review of PR #60 fixed interrupted map trash moves so restart or the next write reconciles map folders with the catalogue.
- **Prep action spacing (W60):** the add-scene, add-handout and add-loot controls now clear the next section heading at desktop and phone widths.
- **Prep archive review:** starting a new session now skips every existing session number, including archived preps, so archiving the last active prep cannot reopen an archived one.
- **Archive session preps (W29, fourth slice):** a prep page has "Archive session" and "Restore session" (a prep's `archived` flag; nothing is deleted). Archived preps leave the session-prep navigation, the dashboard's next session and the default prep opened, and are listed as "Archived sessions" on the other prep pages; `GET /api/state` returns `prep_archived`. Session numbers still count archived preps, and map/request session pickers still list them. Prep titles were already editable. Campaign data moves to schema 5 on start-up, with the usual verified backup first. World maps still have no rename.
- **Buttons for the map trash (W29, third slice):** a map's Foundry tab has "Move map to trash", and the Maps page lists the trash with Restore and "Delete for good" (`POST /api/maps/trash/<id>/discard`, which removes the stored folder and cannot be undone). Restore now also returns the documents it re-linked so the browser drops stale copies. Preps and world maps still have no trash or rename.
- **Deleting a map goes to a restorable trash (W29, second slice):** `POST /api/maps/<slug>/delete` moves the map's folder, catalogue entry and brief to `trash/maps/<id>/` in the campaign folder and clears every prep scene `map` (matched by name or id) and request or art item `map` that pointed at it, in one recoverable commit. `GET /api/maps/trash` lists the trash and `POST /api/maps/trash/<id>/restore` puts the map back and re-links the records that still have no map. There is no button yet, and preps and world maps still have no trash.
- **Deleting a codex entry unlinks it (W29, first slice):** the entry page asks the server where the entry is used (`GET /api/codex/<id>/uses`: other entries' related lists, thread `pcs`, art items, requests, prep scenes and map areas), lists those places in the confirmation, and deletes through `POST /api/codex/<id>/delete`, which removes the entry and every reference in one recoverable commit. Maps, preps and world maps still have no trash or rename.
- **AI usage ledger (W44, first slice):** each finished AI draft job (general request or map workflow) now records tokens, cost and time in its job record, read from the provider's own output: the Claude Code result envelope reports all three, and the OpenAI worker reports tokens (OpenAI states no price, so its cost stays unknown and is counted as unpriced). Settings shows this month's drafts, tokens, cost and time; `GET /api/usage` returns totals per month. Jobs from before this change have no usage. Per-session totals and asking before a large prompt are still open.
- **Imported entries keep a hash, not a copy (W24):** a codex entry made by the Foundry import stores `foundry.hash` (a fingerprint of the imported name, folder, notes and image) and `foundry.image` instead of a full copy of those values, so imported entries no longer carry their text twice. Studio edits are still detected and kept on reimport. Campaign data moves to schema 4 on start-up, with the usual verified backup first.
- **Bounded draft context:** map workflows and general requests now select linked and pinned codex records,
  active thread summaries, name matches and a compact index under a configurable character budget. Prompt
  packs and background jobs share the same selector. A preview shows counts and size, and the GM can pin
  up to 20 entries before another draft. For a large map the preview shows the next AI batch. Provenance
  and file paths stay out of the reference data. If an entry was deleted after it was pinned, the preview
  names the stale pin and lets the GM clear it without blocking the next draft.
- **World Library review fixes:** previously saved snapshots without a compendium flag still open in the folder picker and import. Macro exports also detect legacy `flags.core.sourceId` when `_stats.compendiumSource` is empty.
- **Folder picker for the world import (W24):** the World Library page offers "Choose folders to import" before the one-click import. Folders made only of compendium copies start unticked; leaving the picker alone imports as before, and unticked documents stay searchable in the library.
- **Relay record check (W45):** `tools/check_relay.py` fails CI when a commit has a `Reviewed-PR:` line that git does not read as a trailer (for example above a separate `Co-Authored-By` paragraph), so a review can no longer be lost silently. Run it locally with `python tools/check_relay.py origin/main..HEAD`.
- **Macro snapshots mark compendium copies (W24):** the Foundry library export macro now sends the `compendium` flag for each document, so a world imported from a macro snapshot skips compendium copies by default, like one read from the world folder. Re-run the macro to refresh an older snapshot.
- **Big-map stocking in batches (W26):** a map with more than 30 areas is drafted in AI batches of up to 25 notable areas (anything but plain houses, plus areas with linked threads or entries); plain houses take a rollable template description and keep their starter loot. Counts in the brief are now maximums, shared across the batches, and a short draft applies with a note in its summary. A batch the validator rejects is rerun once with the validation message before the workflow fails, and a failed workflow resumes at its current batch. Small maps behave as before. The manual prompt and paste route still describes the whole map in one response.
- **Linked Foundry documents (W25):** when an export goes to the world an entry was imported from, the export carries the entry's Foundry UUID and Foundry-relative image path. The import macro links GM pages with `@UUID[...]` and leaves the existing actor or item untouched instead of creating a description-only copy. Entries from another world, or made in Studio, are created as before.
- **Audit fixes (W27):** linked entries in the map inspector show Foundry-imported portraits through the asset route, Handouts lists the campaign's real uploads folder under any `DM_HOME` name while keeping nested folder names visible (including Windows short-path aliases), and the forge folder joins `sys.path` once instead of on every request.
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
