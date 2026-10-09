# Backlog

Work items for coding agents, highest priority first. `docs/CODE_REVIEW.md` holds the findings behind the
rows. `docs/ROADMAP.md` holds the owner's product goal and a design note under each row's ID: read a row's
note before claiming it. Claim an item before starting it (`docs/DEVELOPMENT.md` → Coordinating agents) and
delete its row, and its roadmap note, in the PR that completes it.

Priority 1 is next, 2 is planned and 3 is later. A bug the owner reports enters at priority 1; other owner
requests take the priority the owner gives, or your judgement against the table. Describe the outcome,
never private campaign material. IDs are never reused. Rows added by concurrent PRs conflict on the next ID
line below: the agent merging second renumbers its rows and edits the Slack replies that announced them.

Next ID: W77

| ID  | Priority | Work                                                                                                                           | Acceptance criteria                                                                                 |
| --- | -------- | ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| W74 | 2        | Owner-assisted live check of recordings to draft: a real recording, Whisper model and Claude run through the automatic run     | Requests, cost, window count, limit message and passage quality recorded; failures become rows      |
| W76 | 2        | Owner-assisted live check of the Codex provider: one small draft through `codex exec`, its events, usage and limit message     | Event shapes, flags, tool lock-down and limit wording recorded; the stream guard's lists corrected  |
| W33 | 2        | Proposal review, remainder: before/after diffs, redraft one item, item review for map and session workflows                    | Map and session drafts support item choices; changed records show diffs                             |
| W34 | 2        | Threads, remainder: links to maps and locations, clues, sort by hero (entry and session links, staleness sort, backlinks done) | A thread lists its clues and map pins; threads sort by hero                                         |
| W35 | 2        | Wrap-up after play: notes in; session log, thread, codex and next-hook changes out                                             | Applying updates threads and NPCs; the next recap draft uses the log                                |
| W36 | 2        | Readiness board on each prep, with a one-click fix for each gap                                                                | Each check reads stored state; a fixture exercises every gap                                        |
| W37 | 2        | Party roster, remainder: encounter budget checks against the party (characters read and AI context done)                       | A prep's encounters are rated against the party's size and level                                    |
| W38 | 2        | Session bundle: everything a prep links reaches Foundry in one export and one import                                           | Two maps, six NPCs and two handouts arrive in one run; reruns update them                           |
| W39 | 2        | Foundry session journal with scene pages and @UUID links, plus player handout pages                                            | A player fixture sees the handout and none of the secrets                                           |
| W40 | 2        | Tokens: token art, prototype settings and hidden tokens placed at location positions                                           | Linked creatures appear as hidden tokens; GM tokens survive reimport                                |
| W41 | 2        | Playable NPCs based on a compendium monster: duplicated, renamed, re-imaged and annotated                                      | The NPC imports with its attacks; reimport changes name, art and notes only                         |
| W42 | 2        | Art pipeline: style guide, templates per kind, batch per prep, token cut-outs, adapters                                        | One action fills every missing image for a prep; fake-provider tests                                |
| W43 | 2        | Live Foundry check with the owner on their v12 dnd5e versions (owner-assisted)                                                 | Exact versions and results recorded; failures become backlog rows                                   |
| W44 | 2        | AI usage ledger, remainder: totals per session; ask before prompts over a set size (per-job and per-run done)                  | A large prompt asks first; totals per session                                                       |
| W57 | 2        | Phone-first editing of codex, threads, preps and proposal review                                                               | Touch-emulated flow edits an entry, thread and scene, then applies a proposal                       |
| W58 | 2        | One-click journal entry: text, links to entries and an image prompt                                                            | One click yields reviewed linked text and a ready image prompt                                      |
| W61 | 2        | Folder hierarchy, remainder: show paths on codex entries; reimport updates a moved folder (paths are in the library)           | Imported records show parent folder paths; deprecated scenes stay grouped                           |
| W63 | 2        | Preview imported scene images, journal videos, and both NPC token and portrait art                                             | Each asset opens or plays from Studio with safe path and media limits                               |
| W64 | 2        | Choose an imported Foundry scene as an overworld map and place other scenes on it                                              | A selected scene becomes the base map; pins link to imported scenes                                 |
| W65 | 2        | Edit Foundry scene settings in Studio: playlist and music, lighting, weather, grid and other fields                            | A scene's music and settings edit in Studio and reach Foundry on export                             |
| W66 | 2        | AI proposes pins on the overworld map for scenes whose names match its labels                                                  | A scene named like a map label gets a reviewed pin proposal; none apply unreviewed                  |
| W67 | 2        | Studio never deletes from Foundry: every link and Studio-made detail can be unlinked or removed                                | Each linked record offers unlink or remove; Foundry data is never touched                           |
| W24 | 3        | Folder picker identifies Foundry folders by ID and shows parent paths (name-only today)                                        | Two folders with one name import separately; older snapshots still open                             |
| W10 | 3        | Installer and performance budgets for very large maps                                                                          | Clean-machine install test and measured time/memory at documented map sizes                         |
| W15 | 3        | Serve several campaigns from one process (forge links, map import/export, forge exports, revisions shipped)                    | Services receive their `Campaign` explicitly (no process-wide active campaign)                      |
| W46 | 3        | Structured stat blocks mapped to D&D 5e actor data for NPCs without a compendium base                                          | A fixture actor matches its stat block; malformed blocks are rejected                               |
| W47 | 3        | Companion Foundry module: install once, preview changes before applying, DialogV2 for v13                                      | No macro paste needed; v12 and v13 fixtures pass                                                    |
| W48 | 3        | Generators for dungeons, caverns, wilderness, interiors and ships, each with keyed areas                                       | Each lints clean across 20 seeds at three sizes                                                     |
| W49 | 3        | Visual plan editor: paint, fill and rectangle tools with live warnings and undo                                                | A door moves and saves without opening the text plan                                                |
| W50 | 3        | Multi-level maps: floors linked by stairs, as Regions or linked scenes                                                         | A token moves between linked floors in a fixture                                                    |
| W51 | 3        | Opt-in draft evaluation on synthetic campaigns, run by the owner and never in CI                                               | The script reports pass rates for map, request and session drafts                                   |
| W52 | 3        | Campaign backup and restore from Settings: data, maps and uploads with checksums                                               | Round trip restores to a new folder; a newer schema is refused                                      |
| W53 | 3        | Global search and quick capture across codex, threads, maps, locations and sessions                                            | Each record type is found; a note can be added from any page                                        |
| W54 | 3        | Retention for job logs, map revision images and document history                                                               | Limits are configurable and pruning is tested                                                       |
| W55 | 3        | Browser code as ES modules with `// @ts-check`; split `mapStudio`; Ruff F and B rules                                          | Type check and wider lint run in CI with no behaviour change                                        |
| W56 | 3        | Move single-campaign names out of source: readable folders to settings, names from the codex                                   | No single-campaign names in source; generator test uses codex names                                 |
| W59 | 3        | Show the schema change and backup folder before a migration runs                                                               | The notice appears for an older campaign; the backup it names restores                              |
| W75 | 3        | Split `campaign_core.py` (about 1,700 lines) by workflow: document storage, job results, recordings to draft, maps             | Each module has one job; behaviour and tests unchanged; the job tables stay the one extension point |

## Recurring when numbered work is complete

**RA1 — Full project audit.** Whenever no numbered backlog rows remain, audit the entire application against
`AGENTS.md`, the review checklist and the owner's session journey. Update `docs/CODE_REVIEW.md`,
`docs/ARCHITECTURE.md`, `docs/ROADMAP.md` and this backlog with confirmed findings and new numbered work.
Repeat RA1 whenever the numbered backlog becomes empty again; do not remove this standing item.
