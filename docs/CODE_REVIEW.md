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

## Structural assessment

The filesystem model is understandable and suitable for a local prototype. Separating workflows,
revisions, map I/O, rendering and configuration already gives useful boundaries. A no-build frontend makes
installation straightforward. The renderer is independent of AI: walls and image geometry share one plan.

The main pressure points are the large HTTP/orchestration module and shared global frontend state.
Splitting these should follow tested boundaries, rather than changing the stack during the initial release.
Formatting and removing dead renderers make the current structure easier to review; the module split is
still needed as features grow.

## Prioritized development work

| Priority | Work                                                                           | Acceptance criteria                                                                        |
| -------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| 1        | Extract a job service and route handlers from server.py                        | Existing APIs/state transitions unchanged; recovery and queue tests pass                   |
| 1        | Replace broad legacy file-editing requests with structured entity/prep schemas | Review/apply flow for general requests; no model filesystem tools needed                   |
| 1        | Strengthen multi-document commit recovery                                      | Crash at each write boundary recovers without dangling links or duplicates                 |
| 1        | Add a versioned campaign schema and migration tool                             | Realistic old fixtures migrate with backups; unknown future versions are rejected          |
| 2        | Split frontend state/autosave, shared controls and page controllers            | Route changes cancel stale work; preserve autosave/conflict behavior and focus             |
| 2        | Add browser smoke tests and accessibility checks                               | Wizard, pin editor, proposal review and mobile navigation verified in CI                   |
| 2        | Build supported Foundry version/system adapters                                | Fixture contracts plus explicit live GM checks; preserve custom documents on reimport      |
| 2        | Add read-only world document browsing before two-way sync                      | Supported API/module integration, stable provenance and conflict policy; no raw DB editing |
| 3        | Provider adapters, job cancellation, progress and resumable queue              | Fake-provider failure/cancel/retry tests; settings avoid secret storage                    |
| 3        | Installer and performance budgets for very large maps                          | Clean-machine install test and measured time/memory at documented map sizes                |

## Remaining limitations

- Multi-file content application is retryable, not an atomic transaction. Backups remain necessary.
- The in-memory queue fails unfinished jobs on restart; automatic resume/cancel is not implemented.
- Workflow fingerprints cover layout/location changes, not all campaign text edited during generation.
- General Claude requests have file tools; their scope is an instruction, not enforced filesystem isolation.
- The app reads the chosen world manifest and exports assets. It does not yet manage every existing Foundry
  document or provide live two-way synchronization.
- NPC/item mechanics are notes, not complete mechanical D&D 5e sheets. Scene export targets v12.
- Downloadable source needs Python and dependency installation; it is not a bundled executable.
- Browser syntax checks and manual UI checks are present; browser automation remains on the roadmap.

These limitations are reflected in the README. Keep this review current as the listed work is completed.
