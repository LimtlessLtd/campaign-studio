# Initial structure review

Reviewed for the first public alpha on 2026-10-02. This is a source and local behavior review, not a live
Foundry compatibility certification. Tests use synthetic campaigns and fake providers.

## Findings addressed for publication

| Finding                                                                                 | Resolution                                                                                 |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Download omitted files required to rebuild its own archive                              | Complete explicit manifest; extracted startup/repackaging regression test                  |
| Atomic map catalogue writes could still lose concurrent read/modify/write updates       | Shared Windows/POSIX process lock used by server imports/content/exports and forge         |
| Unhandled result-processing errors could stop a worker lane                             | Worker recovery boundary; lane survival regression test                                    |
| A second server attempted job recovery before discovering the port was occupied         | Bind first, then recover/start workers                                                     |
| Duplicate workflow starts and result transitions could race                             | Reread start state under the server lock; finish results under that lock                   |
| Public source carried private campaign examples and hardcoded notes                     | Generic examples, local notes path, opt-in legacy references; manifest/private-path checks |
| New UI and legacy UI implementations overlapped                                         | Removed unreachable dashboard/map/art renderers; formatted JS/CSS/Python                   |
| Agent guidance described content generation but lacked development/review/release gates | AGENTS, development/architecture docs, PR templates, regression tests and CI               |
| HTTP routes and job lifecycle shared one large server module                            | Separate route, campaign core, job service and startup modules with queue/recovery tests   |
| General requests let a model edit runtime files directly                                | Bounded entity/prep proposals, GM review and application with no model file tools          |
| A crash between document writes could leave content, links or plans half applied        | Write-ahead journal completes interrupted changes at startup and before the next write     |
| Stored data had no version, so builds could not migrate old data or refuse newer data   | Schema marker, verified pre-migration backups, idempotent migrations, restore tool         |

The release gate also caught intermittent Windows file replacement failures. Source now retries temporary
sharing/access errors while holding the document lock; permanent errors still fail after a bounded wait.
Regression tests cover both outcomes, and test subprocesses are cleaned up even when an assertion fails.

## Structural assessment

The filesystem model is understandable and suitable for a local prototype. Separating workflows,
revisions, map I/O, rendering and configuration already gives useful boundaries. A no-build frontend makes
installation straightforward. The renderer is independent of AI: walls and image geometry share one plan.

The HTTP transport, campaign operations, job lifecycle and startup have separate modules. The browser now
separates document state, controls and page controllers, and route loads get abortable view contexts. The
large map editor and shared in-memory browser state still need care as features grow.

## Design and pattern review (2026-10-04)

An independent review of all application code up to `43efb18`: backend, forge, frontend, Foundry macros and
tests. It checked correctness, encapsulation, single responsibility, duplication and dependency direction.

**Patterns worth keeping.** Atomic replacement with cooperating file locks; optimistic `X-Rev` concurrency
with a three-way merge; stable, prefixed IDs that make applies idempotent; strict validation and size limits
on model output; an AI runner without tools; localhost Host checks plus a custom-header CSRF guard; bounded,
SSRF-checked package fetches; consistent HTML escaping in the browser and Foundry macros; an explicit source
manifest; and integration tests with fake providers. `JobService` shows good dependency injection: the
campaign supplies its callbacks. `gen_city` models its domain with `Grid` and `City` classes.

The codebase is mostly procedural modules over plain dictionaries. That is reasonable Python and is not a
defect by itself. The problems below are the ones object-oriented principles exist to prevent.

Review of PR #21 found that the World Library's LevelDB reader scanned every table and log in a database
folder. During compaction, a retired file can remain on disk after its deletion marker has been dropped;
scanning it can make a deleted document reappear. The reader now follows the live file set in `CURRENT` and
`MANIFEST`, and the snapshot fingerprint includes manifest changes. Synthetic fixtures cover retired tables
and logs; reading during a live Foundry compaction remains unverified.

Review of PR #24 found no confirmed bugs or structural defects. Review of PR #25 found that its one-action
import silently swallowed a first-run failure, left macro snapshots out of the codex, and put Foundry media
paths in codex image fields that the browser served as Studio files. Its UUID-only match could also update
an entry from another world with the same Foundry document ID. The import now reports its result, uses the
same codex conversion for folder and macro snapshots, scopes provenance to the world folder, and serves
supported local images through the read-only Foundry asset route. `campaign_core` owns the recoverable
snapshot/codex commit; `foundry_library` converts documents. Synthetic unit, route and browser checks cover
these behaviors. Live import and live Foundry image handling remain unverified.

Review of PR #29 found that its login failure table could still grow beyond its cap when all entries were
active. New addresses now wait for a slot to expire, while existing addresses retain their lockouts; the
gate checks capacity under its lock. This can temporarily delay a legitimate new address when 1,024 peers
have recently failed. An oversized login form could also reset the response connection on Windows while
the request body remained unread; modest oversized forms are now drained before the 400 response.

Review of PR #30 found no confirmed bugs or design faults. Review of PR #35 found that map painting still
printed bare percentages, which its new progress parser ignored, so the visible map job bar stopped
advancing. The renderer now emits explicit progress reports. The progress reader also retains the latest
report after more than 200 later log lines and ignores oversized numeric reports.

### Fixed in this review

| Finding                                                                                    | Resolution                                                                          |
| ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| Autosave merge replaced an unkeyed list wholesale: AI-added goals/checklist/loot lost      | Three-way list merge, in place; `tools/check_merge.cjs` and a browser check         |
| Autosave merge restored fields the server had removed (for example a cleared error)        | Untouched local keys missing on the server are removed                              |
| Edits typed while a save was in flight became the saved base and could be overwritten      | The base is the exact body sent; the document stays pending if it changed meanwhile |
| `foundry_upgrade` called 50 private `foundry_backup` helpers                               | Shared file primitives in `storage`; the backup helpers it needs are public         |
| Two Foundry `wotg-maps/index.json` writers; export's was non-atomic and failed on bad JSON | One locked, atomic `forge.write_foundry_index`; scene JSON written atomically       |
| Failed and interrupted jobs had near-duplicate handlers that overwrote finished records    | One `settle_failed_job` that only fails records still owned by that job             |
| Content apply and revision restore orchestration lived in HTTP routes                      | `campaign_core.apply_content` and `restore_revision`, beside `apply_layout`         |

### Design findings

1. **Global paths instead of a campaign object (dependency inversion, encapsulation). Resolved.** Twelve
   modules derived campaign paths from `__file__`, and the integration test patched 18 module globals across
   seven modules to relocate one campaign. `DM/campaign.py` now defines a `Campaign` (data, maps, uploads,
   backups, history, jobs, settings and stored relative paths) once. Modules ask `campaign.active()` at
   use, the job service and journal are given it, and child processes inherit it through `DM_HOME`. Tests
   activate one `Campaign`. One process still serves one campaign at a time (W15).
2. **Document shapes were implicit (DRY). Resolved.** A codex entry was written out field by field in four
   places, and the copies had drifted: layout areas lacked `rooms` and `threads`, new preps lacked
   `handouts`, map events lacked `title`. `DM/shapes.py` now defines each stored record once; apply paths,
   migrations and the browser (through `GET /api/shapes`) build records from it, and tests fail when a
   writer or a stored shape diverges.
3. **God functions (single responsibility). Partly resolved.** HTTP methods now dispatch from a route
   table to handlers under 60 lines. Foundry upgrade catalogue collection, compatibility solving and
   inventory/clone workflows now live in separate modules; its long clone workflows
   still need care when extended. `render2d.prop` is 424 lines. `map-pages.js` has `mapStudio` at over 1,000
   lines and `foundry-pages.js` has `foundryUpgradeCard` at over 500. Page controllers are separate from
   shared controls and autosave; those two complex page functions still need careful decomposition when extended.
4. **HTTP errors are untyped. Resolved at the transport boundary.** Route handlers raise `Invalid`,
   `NotFound` and `Conflict`; one dispatcher maps them to 400, 404 and 409. Domain `ValueError`s become
   `Invalid` at that boundary. A stale document save still carries its merge payload with the conflict.
5. **The generic document API bypassed domain rules. Resolved.** `PUT /api/doc/<name>` refuses
   application-owned documents (`settings`, `workflows/*`, `jobs/*`) with 403; `campaign_core.APP_OWNED_DOC`
   lists them, so a new runtime document is protected by adding it there.
6. **The SSRF guard was time-of-check. Resolved.** `foundry_catalog` resolves the host once inside the
   connection, rejects any non-public answer and connects to that address (the hostname still drives SNI and
   certificate checks). Redirects open new connections, so each hop is checked and pinned. The downloader
   bypasses environment proxies because a proxy could resolve the target again; networks requiring a proxy
   cannot fetch package metadata through this path.

## Product and scale audit (2026-10-07)

An audit of `fe14a0c` against the owner's goal of planning a whole session from a few prompts (see
`docs/ROADMAP.md`). Every required check passed. The evidence comes from reading the code, the synthetic
preview fixture, synthetic reproductions, and one read of a large real v12 dnd5e world through the
read-only World Library reader. Only aggregate counts from that world are recorded here.

The safety patterns hold up; the gaps are in the product layer above them. Prompts grow with the whole
campaign, nothing works at the level of a session, and the Foundry write side covers one map per run with
description-only sheets. Code size shows where effort went: backup, upgrade and cutover tooling is 3,721
lines; session prep, codex, threads and requests are 1,713; the Foundry import macro is 374.

| Finding                                                                                 | Evidence                                                                                                                                                                                                                           | Backlog  |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| Structured prompts previously serialised the whole codex and thread list                | A world of about 500 actors, 1,400 items and 90 scenes imported about 1,950 entries, giving an 8 MB codex. W23 now bounds prompts, preserves linked identities and excludes imported provenance; W24 still needs selective import. | W24      |
| Exporting a map duplicates a linked actor or item that was imported from Foundry        | `key_for_foundry` drops `foundry.uuid` and the macro matches only Studio-tagged documents. Reproduced in the macro harness: the original actor plus a description-only copy                                                        | W25      |
| Whole-map content drafts must describe every numbered area in one response              | Generated districts have 44 areas at 60 × 60 squares, 97 at 80 × 80, 146 at 120 × 80 and 311 at 160 × 120                                                                                                                          | W26      |
| Content drafts must match each requested count exactly                                  | A draft one NPC short is rejected after the model has run; the schema sent to the model carries no counts                                                                                                                          | W26      |
| Autosave sent whole codex and thread documents and kept 50 copies of each               | W28 moved entries and threads to individual files, bounded browser pages, and record-level revisions and history; one edit no longer sends the 8 MB codex                                                                          | Done W28 |
| Maps, session preps and world maps cannot be deleted; deleting an entry leaves its ID   | IDs stay in areas, scenes, briefs and `related` lists; the map inspector hides them but still counts them                                                                                                                          | W29      |
| The map inspector loads Foundry-imported portraits through `/files/`                    | 403 there and 200 through `/api/foundry/asset` for the same path; exports also drop these images                                                                                                                                   | W27, W25 |
| Handouts asks for `DM/uploads` by name                                                  | Empty when `DM_HOME` is a folder with another name; reproduced                                                                                                                                                                     | W27      |
| Four routes add the forge folder to `sys.path` on every call                            | `/api/state`, plan saves, map creation and generator commands                                                                                                                                                                      | W27      |
| Map checkpoints copy the full image of maps without roofs, and nothing prunes old files | About 17 MB per checkpoint at 80 × 80 squares; revisions, job logs and history grow without limit                                                                                                                                  | W54      |
| Single-campaign names remain in source                                                  | Folder names in `FILE_ROOTS` and temple names in `gen_city`                                                                                                                                                                        | W56      |
| Restart recovery and the map busy check read only the newest 200 job records            | An older queued job stays stuck after a restart                                                                                                                                                                                    | #43      |
| Open PR #43 initially sent the layout schema to OpenAI in strict mode                   | Strict mode requires every property in `required`, but layout operations required only `type`; #43 now uses complete operation variants. Not checked live                                                                          | #43      |
| The relay lost the reviews of #32, #33, #36 and #37                                     | Their `Reviewed-PR:` lines sat before a separate `Co-Authored-By` paragraph, where git reads no trailers; restated in this audit's PR                                                                                              | W45      |

## Prioritized development work

The work these findings call for, with the owner's feature requests, is tracked in `docs/BACKLOG.md`. The
owner's product goal and the design notes behind the rows are in `docs/ROADMAP.md`.

## Remaining limitations

- Multi-document changes are journaled and completed after interruption. If a document is edited outside
  the app before recovery, the change is set aside for GM review rather than merged. Map import/export is not
  journaled. Journaled changes are flushed to disk; ordinary single-document saves are not, so a power cut
  can still lose a just-saved edit.
  Backups remain necessary.
- On restart, jobs that never started are requeued; jobs that were running are failed (replay could repeat side effects).
- Workflow fingerprints cover layout/location changes, not all campaign text edited during generation.
- The World Library reads selected media and top-level world documents from the world's database files, a
  GM-exported snapshot, or a paired read-only GM browser tab. It does not include compendium contents,
  edit existing Foundry documents or provide two-way synchronization. The live tab has synthetic tests but
  no version-specific GM verification yet.
- NPC/item mechanics are notes, not complete mechanical D&D 5e sheets. Scene export targets v12; the GM
  macro adapts v11/v12/v13 fields with fixture coverage but no live GM compatibility certification. v14 is
  rejected before import.
- Foundry receives one map per macro run. Tokens are not placed, and session prep, scenes and handouts are
  not exported (W38–W41).
- AI prompts select bounded codex and thread context. Map, request and session-plan drafts read the
  newest played-session logs as canon. World import can select folders, though duplicate folder names
  still need ID-based selection (W24).
  A brief or map key larger than the configured budget is refused before a provider call.
- Session recording transcription (W68) has fixture coverage with fake engines only: no real Whisper model, GPU
  or long recording has been run, so speed and accuracy are unknown. A cancel or restart stops the engine's
  programs, but a server killed without a restart can leave an engine running until it finishes; its staged
  result is discarded on the next start. The transcript list reads every stored transcript in full.
- Sorting transcripts into play and banter (W69) is tested with a fake model on a synthetic session; no real
  Claude run has checked how well it tells a table joke from play, so the GM's review is the safeguard: only
  confirmed in-game passages leave the step. A window is a separate request, so a long session costs several
  against the subscription; the estimate is shown first, but the usage ledger (W44) has no per-session total
  or warning yet. A passage cannot span two windows, so a scene split by a window edge is two passages.
  Re-transcribing replaces a transcript, so its passages are discarded after an explicit confirmation.
- Thread ledger proposals (W70) are tested with exact synthetic quotes, stale-target checks, selective apply,
  idempotent retry and a phone/desktop browser flow. No real Claude draft has checked event quality, and no
  long recording has measured the number of windows or cost. Applied ledgers keep their evidence if a
  transcript is later re-transcribed or removed; a second ledger for that recording ID is not yet offered.
- Downloadable source needs Python and dependency installation; it is not a bundled executable.
- Browser smoke and accessibility checks now run in CI. The current axe gate covers serious and critical
  WCAG 2 A/AA findings; minor and moderate findings are not yet gated.
- Foundry backup, isolated restore-copy verification, v12 clone preparation, module review, migrated-clone
  audit and cutover readiness review are available for local User Data. Opening the clone in Foundry, its
  migration, the actual User Data switch and live rollback remain manual.
- The v12 package report reads declared directory and manifest metadata. Package URLs may be unavailable;
  unknown results block automatic retention. No live Foundry migration or package behavior was verified.

These limitations are reflected in the README. Keep this review current as the listed work is completed.
