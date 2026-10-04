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

The HTTP transport, campaign operations, job lifecycle and startup now have separate modules. Complex
route bodies and shared global frontend state are the main remaining structure pressure points. Further
route decomposition should follow tested business boundaries as features grow.

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

1. **Global paths instead of a campaign object (dependency inversion, encapsulation).** Twelve modules
   derive campaign paths from `__file__`. The integration test patches 18 module globals across seven
   modules to relocate one campaign. Introduce a `Campaign` object (root, data, maps, uploads, backups,
   history, jobs, settings) created once at startup and passed to services. This also enables more than one
   campaign and simpler tests.
2. **Document shapes were implicit (DRY). Resolved.** A codex entry was written out field by field in four
   places, and the copies had drifted: layout areas lacked `rooms` and `threads`, new preps lacked
   `handouts`, map events lacked `title`. `DM/shapes.py` now defines each stored record once; apply paths,
   migrations and the browser (through `GET /api/shapes`) build records from it, and tests fail when a
   writer or a stored shape diverges.
3. **God functions (single responsibility).** `http_routes.do_POST` is a 410-line `if` chain and
   `do_GET` is 176 lines. `render2d.prop` is 424 lines, and `foundry_upgrade` has 220- and 190-line
   workflows inside a 1,300-line module. `studio.js` has `mapStudio` at 1,064 lines and
   `foundryUpgradeCard` at 524. Use a route table (path pattern → handler), a prop-painter registry,
   separate `foundry_catalog`, `package_solver` and `upgrade_workflow` modules, and page controllers for
   the map studio.
4. **Errors are untyped.** Nearly every failure is a `ValueError`, mapped to 403 in `do_GET` and 400 in
   `do_POST`, so "not found", "conflict" and "invalid input" are indistinguishable. Add a small exception
   hierarchy (`NotFound`, `Conflict`, `Invalid`) mapped to status codes in one place.
5. **The generic document API bypasses domain rules.** `PUT /api/doc/<name>` can write `settings`,
   `workflows/*` or `jobs/*` without the validation of their dedicated routes. That is acceptable for a
   single-user local app, but restrict writable document prefixes.
6. **The SSRF guard is time-of-check.** `_validate_url` resolves DNS, then `urllib` resolves again when it
   connects, so DNS rebinding could reach a private address. Pin the validated address for the connection.
7. Minor: `_media_path` repeats the manifest path rules, and `forge.forge` rereads `key.json` three times.

## Prioritized development work

| Priority | Work                                                                | Acceptance criteria                                                                                                                       |
| -------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 1        | Introduce a `Campaign` context object and pass it to services       | No module computes campaign paths from `__file__`; the test fixture builds one `Campaign` instead of patching globals                     |
| 2        | Route table and typed HTTP errors                                   | No handler over ~60 lines; 400/404/409 come from exception types; the existing HTTP tests are unchanged                                   |
| 2        | Split `foundry_upgrade` into catalogue, solver and workflow modules | Same public functions and fixtures pass; no module imports another's private names                                                        |
| 2        | Split frontend state/autosave, shared controls and page controllers | Route changes cancel stale work; preserve autosave/conflict behavior and focus                                                            |
| 2        | Add browser smoke tests and accessibility checks                    | Wizard, pin editor, proposal review and mobile navigation verified in CI                                                                  |
| 2        | Build supported Foundry version/system adapters                     | Fixture contracts plus explicit live GM checks; preserve custom documents on reimport                                                     |
| 2        | Complete the local Foundry upgrade wizard                           | Backup, inventory, report and isolated v12 clone preparation are implemented; confirm clone module state, migrate it and validate cutover |
| 2        | Extend snapshot browsing into a live Foundry-side connection        | Stable provenance, read permissions and conflict policy; no raw DB editing                                                                |
| 3        | Provider adapters, job cancellation, progress and resumable queue   | Fake-provider failure/cancel/retry tests; settings avoid secret storage                                                                   |
| 3        | Installer and performance budgets for very large maps               | Clean-machine install test and measured time/memory at documented map sizes                                                               |

## Remaining limitations

- Multi-document changes are journaled and completed after interruption. If a document is edited outside
  the app before recovery, the change is set aside for GM review rather than merged. Map import/export is not
  journaled, and only the journal is flushed to disk, so a power cut can still lose a just-saved change.
  Backups remain necessary.
- The in-memory queue fails unfinished jobs on restart; automatic resume/cancel is not implemented.
- Workflow fingerprints cover layout/location changes, not all campaign text edited during generation.
- The World Library reads selected media and a GM-exported snapshot of top-level world documents. It does
  not include compendium contents, edit existing documents or provide live two-way synchronization.
- NPC/item mechanics are notes, not complete mechanical D&D 5e sheets. Scene export targets v12.
- Downloadable source needs Python and dependency installation; it is not a bundled executable.
- Browser syntax checks and manual UI checks are present; browser automation remains on the roadmap.
- Foundry backup, isolated restore-copy verification and v12 clone preparation are available for local User
  Data. Opening the restored world in v12, changing modules in the clone, migration, cutover and live rollback
  remain manual.
- The v12 package report reads declared directory and manifest metadata. Package URLs may be unavailable;
  unknown results block automatic retention. No live Foundry migration or package behavior was verified.

These limitations are reflected in the README. Keep this review current as the listed work is completed.
