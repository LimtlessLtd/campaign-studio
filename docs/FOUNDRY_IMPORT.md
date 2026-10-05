# Foundry import compatibility

Campaign Studio exports a v12-shaped scene JSON under `Data/wotg-maps`. The standalone Script macro
selects a Foundry core adapter at runtime and applies the export through Foundry's client document API.
The application never edits a Foundry world database. Reimport updates tagged Studio documents by stable
IDs and keeps GM-created tokens, notes, tiles, walls, lights, journal pages, actors and items. An older scene
without `managed` ownership tags still replaces all walls and lights after the GM confirms that warning.

| Foundry core           | Scene and roof adapter                                          | D&D 5e system                   | Other systems   | Evidence                          |
| ---------------------- | --------------------------------------------------------------- | ------------------------------- | --------------- | --------------------------------- |
| v11                    | Legacy darkness/global light/fog fields and overhead roof tiles | Descriptive NPC and loot sheets | GM journal only | Synthetic import/reimport fixture |
| v12                    | Export scene fields and elevated roof tiles                     | Descriptive NPC and loot sheets | GM journal only | Synthetic import/reimport fixture |
| v13                    | Export scene fields and elevated roof tiles                     | Descriptive NPC and loot sheets | GM journal only | Synthetic import/reimport fixture |
| v14 and other versions | No import; macro warns before changing the world                | No import                       | No import       | Unsupported-version fixture       |

The fixture contracts run with `npm run check:js`. They exercise the complete macro with simulated Foundry
documents, including GM-owned documents and a same-named journal page on reimport. They cannot verify
Foundry's document validation, rendering, permissions or an installed game system. No live Foundry version
or D&D 5e system version has been checked for this change.

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

Do not call a version/system combination live-verified until this checklist passes for those exact versions.
