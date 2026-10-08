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

1. **Campaign memory and AI context** (W24, W30). W23 now bounds drafts and prompt packs, and the world import
   takes chosen folders. Existing campaign records, session summaries and selected Foundry journals enter through
   a reviewed memory import; recent session logs reach AI drafts. Every AI feature depends on richer thread
   history and context.
2. **Session workflow** (W33–W37). Session Forge now turns a pitch into a linked prep and queued map briefs.
   Item-level review, readiness and folding played outcomes back into threads remain.
3. **Foundry write side** (W25, W38–W41, W46, W47). The import macro handles one map per run with
   description-only sheets. Tokens, playable stats, session journals and handouts are missing, and imported
   actors come back duplicated.
4. **Data at scale** (W29, W54). Codex entries and threads now save separately; deletion, retained history
   and other whole documents still need work at real campaign sizes.
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
| 2. Pitch              | Session Forge drafts a linked prep               | W33           |
| 3. Outline            | Whole-session review; no item-level choices yet  | W33           |
| 4. Maps               | Session map briefs queue layout workflows        | W26, W48      |
| 5. Cast               | Free-text stats and one image per entry          | W25, W41, W46 |
| 6. Art                | One image per click, no shared style             | W42           |
| 7. Publish to Foundry | One map per macro run                            | W38–W40, W47  |
| 8. Play and wrap up   | A "played" status                                | W30, W35      |

## Milestone

A synthetic campaign goes through the whole journey in Studio. A pitch becomes an outline the GM edits; two
maps (one generated, one imported) are stocked; six NPCs get portraits, tokens and compendium bases; one
bundle imports into the macro fixture; a wrap-up moves the threads; the next session's recap uses it. Rows
W24–W26, W30, W33–W35 and W38–W42 cover the remaining work. Then the owner repeats it on their own campaign with W43.

## Design notes

New work reuses the patterns in `AGENTS.md`: propose, validate, review, apply; `commit_docs` for
multi-document changes; shapes with migrations; ownership tags in Foundry. W27 and W52–W56 need no note:
their row and finding say enough.

### W24 Selective import

- First-run setup imports every actor, item and scene. A long-running world turns into about two thousand
  codex entries, most of them gear and spells that never need campaign notes.
- Ask which actor, item and scene folders become codex entries. Default out documents copied from a
  compendium (`_stats.compendiumSource` in v12+, `flags.core.sourceId` before).
- Everything else stays in the World Library snapshot as a **reference library**: searchable, linkable from
  a codex entry by Foundry UUID, and reaching prompts only as W23 search hits.
- Done: records carry a `compendium` flag, `import_into_codex` takes a folder list (default skips compendium
  copies) and `/api/foundry/world/import` accepts `folders`; the Foundry-macro snapshot sends the flag too.
  The library page has a "Choose folders to import" picker (`library` returns `folders`). Entries keep `foundry.hash` and `foundry.image`, not a copy of the imported
  values (schema 4 migrates older entries).
- The remaining picker work should identify folders by their Foundry IDs and show parent paths. The current
  name-only selection cannot distinguish two folders with the same name. Keep older name-only snapshots
  readable when adding those IDs.

### W25 Linked Foundry documents

- Imported codex entries keep `foundry.uuid` and `foundry.world_key`, but `forge.key_for_foundry` drops
  them, so the import macro creates a second, description-only actor or item.
- Export the UUID with each linked entry. When the export's target world is the entry's source world, the
  macro resolves the UUID, links GM pages to it with `@UUID[...]` and leaves the document unchanged. Create
  documents only for entries with no Foundry origin.
- Images of imported entries already sit in Foundry's Data folder: pass their Foundry-relative paths
  through instead of resolving them against the campaign folder, where they are dropped today.

### W30 Campaign memory

- A **session log** records what happened: date, players' summary, GM notes, outcomes, threads touched,
  entities that appeared, loot awarded. Keep it with the session's prep so one session stays one document.
  It is a shape change with a migration.

### W68 Session recordings

- Owner feedback (8 Oct): point AI at local video files, transcribe the audio, and work out which threads are
  still unresolved. The table's jokes and invented gags (for example a player's made-up animal form) must not
  become canon, while real play (for example destroying a named temple) must.
- Files stay on disk: the GM picks a path and Studio never uploads or copies a recording. Transcription is a
  job behind a provider adapter (local Whisper or a hosted API), with the usual usage ledger and an ask before a
  large job. The transcript is reference data, never instructions.
- A second pass classifies each passage as **in-game**, **table banter** or **unclear**, quoting the passage
  for the GM to confirm. Only confirmed in-game passages feed a W35 wrap-up proposal: session log, thread changes
  and new threads. Nothing applies unreviewed. The GM can mark a passage as banter so later runs remember it.

### W33 Item-level proposal review

- Session Forge (W32) now accepts a pitch and settings, validates a bounded proposal, and applies its linked
  prep, records, art briefs and map layout workflows in one recoverable change. Its review shows the whole
  proposal, with an editable JSON import. A scene's map points to a stored slug, while the location number
  stays zero until a new map has a keyed layout. Recent logs lead its context; W34 will provide reliable
  thread staleness fields for better ranking. W41 adds compendium bases for proposed NPCs.
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

### W48–W50 Maps

- W48: each generator follows the city generator's contract (`generate(W, H, seed, **params)` returns the
  plan grid and keyed areas with kinds and rooms) and registers in `DM/forge/generate.py`. AI layout
  requests then choose a generator and its parameters and add set pieces with revision operations, rather
  than drawing a whole environment in up to 200 grid operations.
- W49: the plan editor paints legend characters with brush, fill and rectangle tools, shows `forge.lint`
  warnings as you paint and checkpoints before saving.
- W50: the floors of one place share a key. Stairs link them as Foundry Regions or as separate linked scenes.

### W57 Phone-first editing

- The owner wants to manage and edit the campaign from a phone. Today only world-map pin placement and
  larger touch targets are phone-aware; the codex, thread, prep and proposal-review pages are laid out for
  a desktop. Work through each page at phone width, with a touch-emulated Chromium flow per page.
- Keep the one-column layout reachable from the existing access-code and Caddy HTTPS setup in the README.
  Autosave must survive a phone losing connection briefly; W28's per-record saves reduce each retry's size.

### W58 One-click journal entry

- A "Draft with AI" button on a journal entry asks for no prompt. It sends the entry, its location or codex
  neighbours and their linked records (through the W23 context budget) and returns a proposal: body text
  that may mention other entries, the entry ids it links to, and a ready image prompt.
- It uses the existing propose, validate, review, apply path (W33 gives item-by-item review). The image
  prompt can feed W42's batch art step; this row does not generate the image itself.
- Today the location "Generate" button asks for a free-text instruction first and returns a separate
  workflow to open.

### W59 Migration notice

- Start-up migrations already take a verified backup first (`DM/schema.py`), but the owner only learns
  of it from the changelog. Before a migration runs, show the version change, the document count affected
  and the backup folder, and keep the existing refusal of newer schemas. W52 adds restore from Settings.

### W61 Foundry folder hierarchy

- Keep each imported document's Foundry folder ID and parent chain, separate from editable Studio grouping.
  Show that path in the World Library and imported record lists so a deprecated scene folder remains distinct.
- Older snapshots without folder IDs still import. Reimport updates a changed Foundry folder without
  overwriting a Studio-edited record; coordinate with W24's folder-ID picker.

### W62 Visible selection

- The currently opened item needs a persistent selected state in its list, starting with Foundry scenes.
  Use the same route-derived state for click and keyboard navigation, and expose it to assistive technology.

### W63 Imported media previews

- Use the read-only Foundry asset route for scene backgrounds, journal video and both NPC portrait and
  prototype-token art. Keep world provenance, path containment, content-type and size limits.
- Show an explicit unavailable state for missing media or a disconnected world instead of an empty image.

### W64 Imported overworld scene

- A world map may take its image from a selected imported Foundry scene instead of an upload. Keep the
  selected scene's world-scoped identity so the link survives a refresh or a scene rename.
- Let the GM place pins for other imported scenes on that map. Keep the existing uploaded world maps and
  battle-map links working; do not write to Foundry's database.

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

### W65 Scene settings

- Imported scenes already keep their Foundry document. Add an editable `settings` subset (playlist or
  sound, darkness, weather, global light, grid size and units) with a validated shape, written back by the
  scene export. Start with music, since that is what the owner asked for; leave unknown fields untouched.

### W66 AI pins for named scenes

- After a scene is chosen as the overworld map (W64), let the AI read its image text and pin labels and
  match them to imported scenes by exact or near name (a label "Campess Port" and a scene "Campess Port").
  Return a proposal, one pin per match with its position; the GM accepts, edits or rejects each (W33). Never
  place a pin without review, and send the AI only what the job needs.

### W67 Unlink and remove, never delete from Foundry

- Rule: Studio never deletes anything from Foundry. It may remove links, nominations and detail it generated.
  Give every linked record (world map nomination, pin, scene or entry link) an unlink or remove action that
  states what stays in Foundry. Shipped so far: remove a world map (nomination and pins); remove an imported world (its codex entries, their links and the library snapshot).

## Deprioritised

- Foundry backup, upgrade and cutover tooling is complete; keep it to maintenance.
- W15 (several campaigns in one process) stays at priority 3. W10 (large-map budgets) matters again once
  W48 adds generators.
