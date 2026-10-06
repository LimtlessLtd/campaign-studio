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

## Prioritized development work

The work these findings call for, with the owner's feature requests, is tracked in `docs/BACKLOG.md`.

## Remaining limitations

- Multi-document changes are journaled and completed after interruption. If a document is edited outside
  the app before recovery, the change is set aside for GM review rather than merged. Map import/export is not
  journaled. Journaled changes are flushed to disk; ordinary single-document saves are not, so a power cut
  can still lose a just-saved edit.
  Backups remain necessary.
- The in-memory queue fails unfinished jobs on restart; automatic resume/cancel is not implemented.
- Workflow fingerprints cover layout/location changes, not all campaign text edited during generation.
- The World Library reads selected media and top-level world documents, from the world's database files or a
  GM-exported snapshot. It does not include compendium contents, edit existing documents or provide live
  two-way synchronization.
- NPC/item mechanics are notes, not complete mechanical D&D 5e sheets. Scene export targets v12; the GM
  macro adapts v11/v12/v13 fields with fixture coverage but no live GM compatibility certification. v14 is
  rejected before import.
- Downloadable source needs Python and dependency installation; it is not a bundled executable.
- Browser smoke and accessibility checks now run in CI. The current axe gate covers serious and critical
  WCAG 2 A/AA findings; minor and moderate findings are not yet gated.
- Foundry backup, isolated restore-copy verification, v12 clone preparation, module review, migrated-clone
  audit and cutover readiness review are available for local User Data. Opening the clone in Foundry, its
  migration, the actual User Data switch and live rollback remain manual.
- The v12 package report reads declared directory and manifest metadata. Package URLs may be unavailable;
  unknown results block automatic retention. No live Foundry migration or package behavior was verified.

These limitations are reflected in the README. Keep this review current as the listed work is completed.
