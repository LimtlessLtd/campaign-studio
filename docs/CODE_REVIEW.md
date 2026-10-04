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

## Prioritized development work

| Priority | Work                                                                | Acceptance criteria                                                                                                                       |
| -------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
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
