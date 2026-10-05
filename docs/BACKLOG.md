# Backlog

Work items for coding agents, highest priority first. `docs/CODE_REVIEW.md` explains the structural
findings behind the early items. Claim an item before starting it (`docs/DEVELOPMENT.md` → Coordinating
agents) and delete its row in the PR that completes it.

Priority 1 is next, 2 is planned and 3 is later. A bug the owner reports enters at priority 1; other owner
requests take the priority the owner gives, or your judgement against the table. Describe the outcome,
never private campaign material. IDs are never reused. Rows added by concurrent PRs conflict on the next ID
line below: the agent merging second renumbers its rows and edits the Slack replies that announced them.

Next ID: W17

| ID  | Priority | Work                                                              | Acceptance criteria                                                                                                                                            |
| --- | -------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| W7  | 2        | Complete the local Foundry upgrade wizard                         | Backup, inventory, report and isolated v12 clone preparation are implemented; confirm clone module state, migrate it and validate cutover                      |
| W8  | 2        | Extend snapshot browsing into a live Foundry-side connection      | Stable provenance, read permissions and conflict policy; no raw DB editing                                                                                     |
| W9  | 3        | Provider adapters, job cancellation, progress and resumable queue | Fake-provider failure/cancel/retry tests; settings avoid secret storage                                                                                        |
| W10 | 3        | Installer and performance budgets for very large maps             | Clean-machine install test and measured time/memory at documented map sizes                                                                                    |
| W15 | 3        | Serve several campaigns from one process                          | Services receive their `Campaign` explicitly (no process-wide active campaign); app links to `forge/` scripts work when the campaign is outside the app folder |
