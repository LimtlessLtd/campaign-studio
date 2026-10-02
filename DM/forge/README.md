# Map Forge

AI image models are bad at battle maps because the walls never line up with anything. Map Forge flips it
round: the **layout** is written as text (by you, Claude or ChatGPT), and everything else is computed from it
exactly: the picture, and the Foundry walls, doors, windows and lights that match it.

```
plan.txt  ──►  forge.py  ──►  <slug>.webp            the battle map (no grid; Foundry draws its own)
                          ├─►  <slug>.foundry.json    Foundry v12 scene: walls, doors, windows, lights, grid
                          ├─►  <slug>.da.json         the same in Dungeon Alchemist's format (Import Data route)
                          ├─►  <slug>.check.jpg       walls drawn over the picture, to eyeball alignment
                          └─►  FoundryVTT/Data/wotg-maps/   copies for the one-click import macro
```

## The quick way: the DM site

Battle maps → **Generate a new map**: pick a type and size, set how built-up it is, tick canal / city wall /
market, press Generate and watch the progress. Or on a session's prep page, **Make for this session → Battle map**:
either generate one from settings or describe it and let Claude design it.

Every generated map gets:
- **roofs**: one image per building, imported as overhead tiles that fade when a token walks in;
- a **DM key** (`key.json`): every building and square numbered, with its rooms and starter loot. Press
  **Stock with Claude** on the map's page and Claude writes read-aloud text, proper loot, events and who is
  there, tied to your open threads and the session it is for. Select a pin to edit an area, link NPCs and items
  from the codex, add separate journal entries, or upload images. Maps without a generated key can start one
  and place pins by hand.

In Foundry, the **Map Forge import** macro (paste `foundry-import-macro.js` into a Script macro once) creates the
scene with walls, doors, windows, lights, the roof tiles, and a GM-only journal with one page per area and pins
on the map. Linked codex details and area images are included in the GM journal; separate journal entries get
their own pages. Run it again after changes to replace the forged parts; your tokens stay.

## Making a map by hand

1. Make a folder `DM/maps/<slug>/` (lowercase, dashes) with a `plan.txt`. Or ask Claude in the DM site's
   Requests page / in chat: *"forge a battle map of …"*.
2. Run `python DM/forge/forge.py DM/maps/<slug>/plan.txt` (several plans at once is fine).
   Warnings point at the exact line and column of anything that won't work in Foundry.
3. Check `<slug>.check.jpg`, or the Battle maps page of the DM site.
4. In Foundry, run the **Map Forge import** macro (paste `foundry-import-macro.js` into a Script macro once).
   Pick the map; the scene appears with everything placed. Re-forging and re-importing updates the same scene
   and leaves your tokens alone.
   *Alternative:* create a scene with `wotg-maps/<slug>.webp` as background, then right-click it →
   Import Data → `<slug>.da.json`, exactly like a Dungeon Alchemist export.

## The plan format

Settings, a line with just `---`, then the map, one character per 5 ft square:

```
name: Flooded Ashen Shrine
theme: dungeon          # dungeon | cellar | temple | tavern | ship | cave | outdoor | city
roofs: yes              # outdoor and city themes: roof tiles over anything with an indoor floor
cell: 150               # pixels per square (Dungeon Alchemist uses 150)
darkness: 0.7           # Foundry darkness, 0 bright to 1 pitch black
seed: 3                 # change for a different look of the same layout
summary: one line for the DM site
legend: X = statue      # optional: remap or add a character
---
###############
#.....#...P...#
#..T..+...*...#
#.....#...P...#
####W##########
```

| Char | Feature | Foundry result |
|---|---|---|
| `#` | wall | blocks movement, sight, light, sound |
| `+` | door | door (closed) |
| `S` | secret door | secret door; painted as plain wall |
| `W` | window | blocks movement, see-through |
| `=` | railing / low wall | blocks movement, see-through, sound passes |
| `"` | hedge / thick foliage | blocks movement, limited sight |
| `.` | the theme's floor (indoors, for the outdoor theme) | |
| (space) | the surroundings: open grass outdoors, sky round a ship, rock underground | |
| `-` `^` | wooden floor, stone floor (mix indoor floors in any theme) | |
| `,` `:` `;` `~` `_` | grass, dirt, sand, water, rug | |
| `T` `c` `B` `s` `A` `C` | table, chair, bed, shelf, altar, chest | |
| `b` `x` `o` `K` `H` `>` | barrel, crate, rock, cannon, hatch, stairs | |
| `$` | stone wall (city walls, temples) | same as `#`, painted as stone |
| `` ` `` | cobblestones | |
| `P` `m` `M` | pillar, mast, statue | small wall ring: blocks sight |
| `O` `Q` `Z` `u` | well, fountain, market stall, counter | a well is a see-through ring you can't walk through |
| `n` `k` `q` `j` `y` `g` | anvil, cart, sacks, bench, flowers, grave | |
| `l` | lamp post | light source |
| `&` `%` | tree, bush | ring of limited-sight wall, like Dungeon Alchemist foliage |
| `*` `t` `f` `i` `Y` | brazier, lantern, fireplace, candles, crystal | light source (Foundry light + painted glow) |

Rules that keep Foundry happy (the forge warns about each):
- Walls are **one character thick** and meet at right angles. Thick rock (caves) is fine: only the face
  towards the room becomes a wall.
- A door or window sits **in** a wall, with wall on both sides (left+right or above+below).
- Keep every row the same width. A row one character too long shifts a wall and the forge will point at it.
- Tables, beds, shelves, altars and stairs made of several touching characters become one big piece of furniture.

## Big maps

Size is limited by patience, not memory: the painter works in 1536 px tiles across most CPU cores, and every
texture is a function of map position, so tiles join seamlessly. On this PC an 80×80-square map at 150 px a
square (12000×12000 px, like the bigger Dungeon Alchemist exports) takes about 4 minutes and makes a ~17 MB
WebP. Past 16383 px a side (WebP's limit, 109+ squares at 150 px) it writes JPEG instead. `cell: 100` makes
the same map 2.25× smaller and faster if Foundry or the players' machines struggle.

Nobody should type an 80×80 grid by hand. Claude composes big plans with a short script (roads, rivers,
buildings stamped from room templates, woods scattered with gaps kept clear) and then hand-edits the
interesting bits. The result is still a plain `plan.txt` you can tweak.

## Working with Claude and ChatGPT

- **Claude** writes and edits plans directly (it can read your session prep and codex), runs the forge, and
  fixes whatever the warnings or the check image show.
- **ChatGPT** can write plans too: give it this README and a description. Paste its plan into `plan.txt`.
- For **art**, a forged map is a precise sketch. ChatGPT can repaint it in a richer style as reference art, but
  the forged image stays the one Foundry uses, because only it matches the walls exactly.

## Next steps

- A Blender renderer for the same plans (real 3D lighting and shadows, same walls).
- Diagonal and curved walls, and more props and themes as sessions need them.
