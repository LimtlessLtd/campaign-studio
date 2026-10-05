# Backlog

Work items for coding agents, highest priority first. `docs/CODE_REVIEW.md` explains the structural
findings behind the early items. Claim an item before starting it (`docs/DEVELOPMENT.md` → Coordinating
agents) and delete its row in the PR that completes it.

Priority 1 is next, 2 is planned and 3 is later. A bug the owner reports enters at priority 1; other owner
requests take the priority the owner gives, or your judgement against the table. Describe the outcome,
never private campaign material. IDs are never reused. Rows added by concurrent PRs conflict on the next ID
line below: the agent merging second renumbers its rows and edits the Slack replies that announced them.

Next ID: W16

| ID  | Priority | Work                                                                | Acceptance criteria                                                                                                                                            |
| --- | -------- | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| W3  | 2        | Split `foundry_upgrade` into catalogue, solver and workflow modules | Same public functions and fixtures pass; no module imports another's private names                                                                             |
| W4  | 2        | Split frontend state/autosave, shared controls and page controllers | Route changes cancel stale work; preserve autosave/conflict behavior and focus                                                                                 |
| W5  | 2        | Add browser smoke tests and accessibility checks                    | Wizard, pin editor, proposal review and mobile navigation verified in CI                                                                                       |
| W6  | 2        | Build supported Foundry version/system adapters                     | Fixture contracts plus explicit live GM checks; preserve custom documents on reimport                                                                          |
| W7  | 2        | Complete the local Foundry upgrade wizard                           | Backup, inventory, report and isolated v12 clone preparation are implemented; confirm clone module state, migrate it and validate cutover                      |
| W8  | 2        | Extend snapshot browsing into a live Foundry-side connection        | Stable provenance, read permissions and conflict policy; no raw DB editing                                                                                     |
| W11 | 2        | Restrict the generic document API to editable documents             | `PUT /api/doc` refuses `settings`, `workflows/*` and `jobs/*`; their dedicated routes and existing tests still pass                                            |
| W12 | 2        | Pin the validated address for package downloads                     | The connection uses the address the SSRF check approved; a DNS rebinding test reaches no private address                                                       |
| W9  | 3        | Provider adapters, job cancellation, progress and resumable queue   | Fake-provider failure/cancel/retry tests; settings avoid secret storage                                                                                        |
| W10 | 3        | Installer and performance budgets for very large maps               | Clean-machine install test and measured time/memory at documented map sizes                                                                                    |
| W13 | 3        | Share manifest path rules with `_media_path`; read `key.json` once  | One path rule definition; `forge.forge` reads the map key once per run                                                                                         |
| W14 | 3        | Mark a retried migration's backup as partly migrated                | After an interrupted migration, the retry's backup says it holds partly migrated files and names the first attempt's complete backup                           |
| W15 | 3        | Serve several campaigns from one process                            | Services receive their `Campaign` explicitly (no process-wide active campaign); app links to `forge/` scripts work when the campaign is outside the app folder |
