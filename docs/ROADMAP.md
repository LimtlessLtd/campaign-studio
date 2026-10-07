# Roadmap

The owner's goal (7 October 2026): plan a whole game session from a few prompts. One or two large maps,
NPCs with tokens and playable stats, images, journal entries and handouts, all linked to each other and to
the open story threads, published to Foundry in one step, with what happened at the table folded back into
the threads afterwards.

`docs/BACKLOG.md` holds the work and its order. This file holds what a row has no room for: why it matters,
its boundaries and the design to follow, under the row's ID. The evidence behind the rows is in
`docs/CODE_REVIEW.md` → Product and scale audit. Delete a row's note in the PR that completes the row, and
update a note when its design changes.

## Focus areas

Ranked by how much each holds back the goal.

1. **Campaign memory and AI context** (W23, W24, W30, W31). Prompts carry nothing about past sessions, and
   after a large world import they carry the whole codex, about two million tokens. Every AI feature
   depends on this.
2. **Session workflow** (W32–W37). Studio prepares maps and single records well. Nothing turns a pitch into
   a session, or a played session back into threads.
3. **Foundry write side** (W25, W38–W41, W46, W47). The import macro handles one map per run with
   description-only sheets. Tokens, playable stats, session journals and handouts are missing, and imported
   actors come back duplicated.
4. **Data at scale** (W28, W29, W54). Whole-document saves, 50-copy history, no deletion and dangling links
   break down at real campaign sizes.
5. **Map breadth** (W26, W48–W50). One procedural generator. Other environments rely on the model drawing a
   grid, and large maps cannot be stocked in one response.
6. **Verified integrations** (W43, W44, W51). Foundry imports and the live bridge have fixture coverage
   only, AI cost is invisible, and prompt changes have no evaluation.
7. **Browser code** (W55). About 6,200 lines of global-scope script with syntax checks only. The session and
   review screens will add thousands more.
8. **Process focus** (W45). Backup, upgrade and cutover tooling (3,721 lines) is larger than session prep,
   codex, threads and requests together (1,713). Take rows in order; the upgrade area is maintenance only.

## The session journey

The order a GM works in. Step 8 writes the log that step 1 reads, which is what ties each session back
into the threads.

| Step                  | Today                                            | Rows          |
| --------------------- | ------------------------------------------------ | ------------- |
| 1. Start next session | "+ Next session" copies last session's checklist | W30, W34      |
| 2. Pitch              | Nowhere to say what the session is about         | W32           |
| 3. Outline            | One request per NPC, item, encounter or handout  | W32, W33      |
| 4. Maps               | A wizard per map; scenes name maps in free text  | W26, W32, W48 |
| 5. Cast               | Free-text stats and one image per entry          | W25, W41, W46 |
| 6. Art                | One image per click, no shared style             | W42           |
| 7. Publish to Foundry | One map per macro run                            | W38–W40, W47  |
| 8. Play and wrap up   | A "played" status                                | W30, W35      |

## Milestone

A synthetic campaign goes through the whole journey in Studio. A pitch becomes an outline the GM edits; two
maps (one generated, one imported) are stocked; six NPCs get portraits, tokens and compendium bases; one
bundle imports into the macro fixture; a wrap-up moves the threads; the next session's recap uses it. Rows
W23–W26, W30, W32–W35 and W38–W42 cover it. Then the owner repeats it on their own campaign with W43.

## Design notes

New work reuses the patterns in `AGENTS.md`: propose, validate, review, apply; `commit_docs` for
multi-document changes; shapes with migrations; ownership tags in Foundry. W27 and W52–W56 need no note:
their row and finding say enough.

### W23 Context budget

- One module (for example `DM/context.py`) builds the reference data for every structured prompt and
  prompt pack: map workflows, requests, and later session and wrap-up drafts. Today `workflow.prompt` and
  `request_workflow.prompt_pack` each serialise the whole codex and thread list.
- Fill in this order until the budget is spent: campaign card (name, system, tone); party roster (W37); the
  last few session logs (W30); open, planned and foreshadowed threads as one line each; records linked to
  the task, in full; records whose names appear in the request text; an index of everything else
  (`id · type · name · first sentence`).
- Linked records: the map key's links, the focus entry and its `related` list, the prep's threads and scene
  NPCs, the brief's selected threads.
- Leave out provenance (`foundry`, `request`, `workflow`, `expanded_by`) and file paths. Shorten long notes
  instead of dropping a linked record.
- Count the budget in characters, estimating tokens as characters ÷ 4; the default lives in settings. Keep
  the order stable so repeated drafts reuse provider prompt caches.
- The browser shows what a draft will include (sections, counts, size) and lets the GM pin records.
- Proposal validation still checks IDs against the whole codex, so a draft may link a record it saw only in
  the index.

### W24 Selective import

- First-run setup imports every actor, item and scene. A long-running world turns into about two thousand
  codex entries, most of them gear and spells that never need campaign notes.
- Ask which actor, item and scene folders become codex entries. Default out documents copied from a
  compendium (`_stats.compendiumSource` in v12+, `flags.core.sourceId` before).
- Everything else stays in the World Library snapshot as a **reference library**: searchable, linkable from
  a codex entry by Foundry UUID, and reaching prompts only as W23 search hits.
- Store a hash of the imported values instead of the `foundry.imported` copy, which doubles the codex today.
  The migration computes the hash from the stored copy.

### W25 Linked Foundry documents

- Imported codex entries keep `foundry.uuid` and `foundry.world_key`, but `forge.key_for_foundry` drops
  them, so the import macro creates a second, description-only actor or item.
- Export the UUID with each linked entry. When the export's target world is the entry's source world, the
  macro resolves the UUID, links GM pages to it with `@UUID[...]` and leaves the document unchanged. Create
  documents only for entries with no Foundry origin.
- Images of imported entries already sit in Foundry's Data folder: pass their Foundry-relative paths
  through instead of resolving them against the campaign folder, where they are dropped today.

### W28 Per-record storage

- Autosave PUTs the whole codex for any edit, and the server keeps 50 copies. At about 8 MB after a large
  import, that dominates every save, poll and history write.
- Store each codex entry and thread as its own document (`data/codex/<id>.json`,
  `data/threads/<id>.json`); `doc_path` already allows one folder level. Each record keeps its own revision
  and history.
- A paged list route with type, tag, text and source filters replaces loading the whole codex; the codex
  grid pages instead of rendering every card.
- Multi-record changes keep using `commit_docs`, naming each record as a target. A schema migration splits
  the single documents, with the usual verified backup and an older-fixture test.

### W29 Deletion and where used

- IDs are referenced from map areas (`npcs`, `items`, `threads`), scenes (`npcs`), prep `threads`, map
  briefs, `related` lists and art items. A server-side backlink index answers "where used" for any record;
  W34's "appears in" uses the same index.
- Deleting unlinks every reference in one `commit_docs` change. Maps, preps and world maps go to a trash
  folder and stay recoverable until it is emptied.

### W30, W31 Campaign memory

- A **session log** records what happened: date, players' summary, GM notes, outcomes, threads touched,
  entities that appeared, loot awarded. Keep it with the session's prep so one session stays one document.
  It is a shape change with a migration.
- W31 imports what a GM already has: a legacy DM-screen or Studio campaign folder (the schema-0 migration
  already reads their JSON), session summaries as JSON or one Markdown file per session, and chosen Foundry
  journal folders marked as lore. Each import arrives as a reviewed proposal, and its text is reference data.

### W32, W33 Session Forge and review

- **Session Forge** is a `session` workflow kind built like requests: a bounded schema, validation, review,
  then one `commit_docs` change with the workflow record last and IDs prefixed with the workflow ID, so
  applying twice adds nothing.
- Input: a pitch paragraph, the prep it belongs to, and a few settings (length, combat and social mix,
  threads to push). Context: W23, led by the last session's log and the threads ranked by staleness.
- The draft holds a recap; goals; 3–6 scenes; up to two new map briefs; new NPCs and items as proposed codex
  entries (optionally with a W41 compendium base); handouts with player text, secrets and an image prompt;
  loot; thread changes and new threads; a checklist.
- Each scene has a purpose, a location (an existing map location, a map from this draft, or free text), NPC
  IDs, an encounter (creatures with counts, target difficulty, terrain, tactics, ways it can end), clues
  tied to thread IDs, and read-aloud text.
- Applying writes the prep, codex entries and thread changes, queues each new map as a map workflow linked
  to its scene, and queues art briefs. A scene's `map` then holds a map slug and location number.
- W33 renders every proposal kind as cards with accept, edit, reject and "redraft this one". Applying
  writes only accepted items; an edited item is validated again. Changes to existing records show before
  and after.

### W34, W35 Threads and wrap-up

- Threads gain links (codex entries, maps and locations, sessions), clues (text, where it is found, and
  planned, planted or found), and the last session that touched them. The threads page sorts by staleness
  and by hero.
- W35 adds a `wrapup` workflow kind. The GM's notes or a recording summary go in. A proposed session log,
  thread changes, codex changes (status, allegiance, notes), new threads and hooks for the next pitch come
  out, reviewed with W33.

### W36, W37 Readiness and party

- **Readiness** checks stored state for one prep: maps rendered, stocked and exported; scene NPCs with stats
  or a compendium base, a portrait and a token; art ready; handouts written; threads touched; encounters
  within the party's budget; a Foundry export newer than the prep's last change. Each failed check links to
  the action that fixes it.
- W37 needs character levels and classes. In dnd5e they come from class items embedded in each actor (keys
  `!actors.items!<actor>.<item>` in the world database), which the World Library reader skips today.

### W38–W41, W46, W47 Publishing to Foundry

- Decide the transport before W38, and record the decision in `docs/ARCHITECTURE.md`:
  - (a) extend the import macro to read a session manifest in `Data/wotg-maps`;
  - (b) a companion module (W47) that reads the same manifest;
  - (c) an Adventure compendium written with the official Foundry CLI. The owner suggested the CLI, and
    Foundry's own Adventure import shows what will change. It writes database files, so it needs the
    owner's decision and a change to the Foundry rule in `AGENTS.md`.

  Start with (a) and keep the manifest format stable so (b) can reuse it.

- W38: a **bundle** holds every map, actor, item, journal and handout a prep links, placed in sidebar
  folders named for the session. Reruns update managed documents in place and keep GM-made ones, as map
  imports do today.
- W39: a GM journal with an overview page, one page per scene (read-aloud, secrets, encounter, `@UUID` links
  to scenes, actors and items) and loot. Handouts go in a separate journal whose ownership the GM sets per
  handout.
- W40: token art separate from the portrait (W42's round cut-out by default); prototype token disposition,
  size and vision; hidden tokens placed around each location's position for its linked creatures, tagged
  as managed so reimport moves or updates only those.
- W41: an entry may name a compendium monster, by UUID or by name in the game system's SRD monster
  compendium. The macro duplicates it into the world with the entry's name, art and notes, so the NPC has
  working attacks. Reimport updates the name, art and notes only.
- W46: structured stat blocks mapped to dnd5e actor data, for NPCs without a base. It comes after W41
  because dnd5e activities make hand-built attacks fragile.
- W47: the module replaces pasted macros, shows what an import will create, update and keep, and moves
  dialogs to DialogV2 ahead of the V1 dialog removal.

### W42 Art

- The image studio promises "a consistent campaign brief", but there is no style setting. Add a style
  guide in settings, applied to every prompt, and templates per kind: portrait, token, item, handout, scene.
- Batch generation fills a prep's missing images, showing the count before it starts. Token cut-outs are
  round with a transparent background (Pillow).
- Provider adapters sit behind the existing `{model, prompt, size, n}` contract, including providers that
  return a URL to download and local services.

### W43, W44, W51 Verification and cost

- W43: every Foundry write path has fixture coverage only. Run the `docs/FOUNDRY_IMPORT.md` checklist with
  the owner on a disposable copy of a v12 dnd5e world, and on v13 if the owner upgrades. Agents prepare the
  synthetic map and checklist; the owner runs Foundry. Record exact versions, and turn failures into rows.
- W44: record tokens, cost and time per AI job from the provider's output (the Claude CLI's JSON result
  reports usage and cost). Show totals per session and month, and ask before running a prompt above a
  configurable size.
- W51: an opt-in script runs drafts against synthetic campaigns and checks that links resolve, canon is
  kept, counts are met and layouts lint clean, so prompt changes can be compared. It makes paid calls, so
  the owner runs it outside CI.

### W45 Relay check

- Review commits for #32, #33, #36 and #37 put `Reviewed-PR:` lines in a paragraph before
  `Co-Authored-By`, and git ignored them until the audit restated them. A CI step reads the PR's commit
  messages and fails when a `Reviewed-PR:` line sits outside the final trailer paragraph.

### W48–W50 Maps

- W48: each generator follows the city generator's contract (`generate(W, H, seed, **params)` returns the
  plan grid and keyed areas with kinds and rooms) and registers in `DM/forge/generate.py`. AI layout
  requests then choose a generator and its parameters and add set pieces with revision operations, rather
  than drawing a whole environment in up to 200 grid operations.
- W49: the plan editor paints legend characters with brush, fill and rectangle tools, shows `forge.lint`
  warnings as you paint and checkpoints before saving.
- W50: the floors of one place share a key. Stairs link them as Foundry Regions or as separate linked scenes.

## Glossary

- **Session**: one game night. Its prep is the plan; its log (W30) is what happened.
- **Scene**: a beat in a session's prep. A Foundry Scene is a map here, so call that a map.
- **Map**: a battle map with a plan and a key, exported as a Foundry Scene.
- **Location**: a numbered area on a map with its own NPCs, items, journal entries and events.
- **Codex entry**: a person, place, item, faction, god or monster.
- **Thread**: a story line with a status. Clues lead the players to it (W34).
- **Proposal**: a validated AI draft awaiting review. Nothing changes until it is applied.
- **Bundle**: everything a session's prep links, exported to Foundry together (W38).
- **Reference library**: Foundry documents kept searchable without becoming codex entries (W24).

## Deprioritised

- Foundry backup, upgrade and cutover tooling is complete; keep it to maintenance.
- W15 (several campaigns in one process) stays at priority 3. W10 (large-map budgets) matters again once
  W48 adds generators.
