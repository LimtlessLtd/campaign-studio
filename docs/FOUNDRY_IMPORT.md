# Foundry import compatibility

Campaign Studio exports a v12-shaped scene JSON under `Data/wotg-maps`. The standalone Script macro
selects a Foundry core adapter at runtime and applies the export through Foundry's client document API.
The application never edits a Foundry world database. Reimport updates tagged Studio documents by stable
IDs and keeps GM-created tokens, notes, tiles, walls, lights, journal pages, actors and items. Pins link to
generated pages by Studio ownership tag, even when a GM page has the same title. An older scene
without `managed` ownership tags still replaces all walls and lights after the GM confirms that warning.

| Foundry core           | Scene and roof adapter                                                 | D&D 5e system                          | Other systems   | Evidence                          |
| ---------------------- | ---------------------------------------------------------------------- | -------------------------------------- | --------------- | --------------------------------- |
| v11                    | Legacy darkness/global light/fog fields and overhead roof tiles        | Descriptive NPC and loot sheets        | GM journal only | Synthetic import/reimport fixture |
| v12                    | Export scene fields and elevated roof tiles                            | Descriptive NPC and loot sheets        | GM journal only | Synthetic import/reimport fixture |
| v13                    | Export scene fields and elevated roof tiles                            | Descriptive NPC and loot sheets        | GM journal only | Synthetic import/reimport fixture |
| v14                    | One Studio Scene Level with the background; roofs at its top; fog mode | Descriptive NPC and loot sheets (5.x+) | GM journal only | Synthetic import/reimport fixture |
| v15 and other versions | No import; macro warns before changing the world                       | No import                              | No import       | Unsupported-version fixture       |

### Foundry v14 Scene Levels

Foundry v14 moved a scene's background image, foreground image and foreground (overhead) elevation onto
its Levels, embedded documents of the scene; it still reads the old scene fields through deprecated shims
until v16. The v14 adapter writes the v14 fields instead:

- A new scene gets one Level, named after the map, holding the background and tagged as Studio's. Its
  elevation range is Foundry's default (top at four times the grid distance). Fog exploration
  `fog.exploration: true` becomes `fog.mode` `INDIVIDUAL` (`false` becomes `DISABLED`).
- Roof tiles sit at the top of that Level, where v12/v13 put them at the scene's foreground elevation, with
  `occlusion.modes: [FADE]` (v14 allows several occlusion modes) and weather blocked under them.
- Generated walls, lights, roofs and area pins list that Level in their `levels`, so a Level the GM adds
  (a cellar, a rooftop) is not crossed by the map's walls or covered by its roofs. GM-made documents keep
  their own Levels.
- Reimport updates the tagged Level's background in place and leaves its name, elevation and any other
  Level alone. A scene imported before the world moved to v14 has Foundry's migrated, untagged Level: the
  macro uses the scene's initial Level, tags it and moves the regenerated walls, lights, roofs and pins
  onto it. Old generated documents are replaced as on any reimport; GM walls, tiles, tokens and notes
  stay as they were.
- Journals, handouts, folders and the D&D 5e NPC (`system.details.biography`) and loot
  (`system.description`) fields are unchanged in v14 and in dnd5e 5.x and 6.x, so they use the same
  writes as v12/v13.
- The macro still uses Foundry's V1 `Dialog`, deprecated since v13 and removed in v16. v14 logs a
  deprecation warning when the macro opens its picker; W47 moves the import to DialogV2.

The fixture contracts run with `npm run check:js`. They exercise the complete macro with simulated Foundry
documents, including GM-owned documents and a same-named journal page on reimport, a GM-added v14 Level
and a scene imported before the world moved to v14. They cannot verify Foundry's document validation,
rendering, permissions or an installed game system. No live Foundry version
or D&D 5e system version has been checked for this change. The v14 field names come from Foundry's v14 API
documentation (14.368) and dnd5e 5.3.3 and 6.0.6 data models.

Session prep export adds a schema-1 manifest at `Data/wotg-maps/sessions/<id>.json`, with a separate picker
index scoped by Foundry world. The GM chooses a session in the same macro to import every linked map,
descriptive actor/item sheet, GM cast/item overview and GM-only handout journal in one run. Studio-tagged
sidebar folders group the new documents. The fixture
checks two scenes, six NPCs, two handouts, stable IDs and preserved GM tokens/pages on reimport in v11,
v12, v13 and v14. Existing Foundry-origin actors/items are linked without moving or changing them.
Handouts are GM-only until W39 adds reviewed player pages; token placement and mechanical sheets remain
W40/W41.

## Live GM checks before campaign use

Run these in a disposable world for each Foundry core and game-system version you intend to use. Record the
exact Foundry and system versions, installed modules, pass/fail for each step and any console errors. Do
not use a production world for the first check.

1. Export a synthetic Studio map with a roof, walls, a door, a light, two numbered areas, a linked NPC and
   item, and a journal entry. Download the current import macro from Studio. Run it as GM in the matching
   disposable world. Confirm the scene image, grid scale, wall/door movement and sight, light, roof fade,
   area pins and GM-only journal pages in the canvas and document sheets.
2. In D&D 5e, confirm one NPC and one loot item were created with their public and GM notes. Check that a
   player cannot see GM secrets. In a different system, confirm the linked details appear in the GM
   journal and that the macro creates no D&D 5e actor/item sheets.
3. Add a GM token, tile, wall, light, note and journal page to the imported scene. Add an unrelated actor
   and item. Give the journal page the same title as a new Studio area. Change the Studio map and key,
   export again, and reimport. Confirm the generated parts changed, each GM addition is intact, there are
   no duplicate Studio scenes/sheets, and generated area pins still open the right pages.
4. Run the macro as a non-GM and in a world with a different ID; confirm both refuse the import. On an
   unsupported Foundry version, confirm the warning appears before a dialog or document write.
   On v14, open the scene's Levels tab: one Level carries the map's background, and its walls, lights,
   roofs and pins are listed on that Level. Add a second Level above it, reimport, and confirm the second
   Level has no generated walls or roofs and a token below the roof still fades it.
5. Export a synthetic prep linking two maps, six NPCs and two handouts. Choose the session once in the GM
   macro; check both maps, session folders, GM-only handouts and descriptive sheets. Add a GM token and
   journal page, edit the prep and export/import again; check that Studio content updates under the same IDs
   and both GM additions remain. Check that player access to the handouts is still denied.

Do not call a version/system combination live-verified until this checklist passes for those exact versions.
