# First run and World Library

Campaign Studio keeps one local Studio project per installation. On a fresh installation, name the project,
choose an existing Foundry world, or create a new world in Foundry Setup and then select it. The world picker
looks in common local Foundry User Data locations; a custom User Data path can be entered. Existing Studio
installations keep their current settings and open the normal dashboard.

The World Library shows media files in the selected world's folder and in shared locations under its
Foundry `Data` folder. It excludes other worlds, game systems and modules. Images can be previewed; all media
is read-only. Foundry can reference assets anywhere inside the `Data` folder, so choosing only a world folder
would miss shared media. See [Foundry User Data](https://foundryvtt.com/article/user-data/).

## Reading documents from the world folder

Foundry scenes, journals, actors and items live in the world's own database files. Opening the World
Library reads them straight from `Data/worlds/<world>/data`, with no macro. Foundry 11 and later keep each
collection in a LevelDB folder, which Studio reads with its own standard-library reader
(`DM/foundry_leveldb.py`). It follows LevelDB's `CURRENT` and `MANIFEST` files to read only active tables
and logs; files retired by compaction are ignored even if they are still on disk. Foundry 10 and earlier
keep one JSON document per line in `<collection>.db`,
which is also supported. The reader only opens files for reading: it does not open the database, take
Foundry's lock or write anything, so it cannot corrupt the world.

The page reads when no documents have been read yet, and again when the files have changed since the last
read (Studio compares a fingerprint of the database and manifest file names, sizes and modification times). **Read again now**
forces a re-read. A read takes a few seconds for a large world; one with hundreds of actors and over a
thousand items took about two seconds on a development machine. Foundry writes changes to disk as you play, so a change made a
moment ago may not appear until you reopen the page. This has been tested against a real v12 world while
Foundry was closed, and against synthetic LevelDB and v10-style fixtures; reading while Foundry is running
has not been verified, so if a read fails then, close Foundry or use the macro below.

A world with more than 5,000 documents of one kind shows the first 5,000 by name and says how many were
left out. If a database is damaged or unreadable, the page says so and offers the macro.

## Importing the world into Studio

**Import world into Studio** (World Library; first-run setup runs it after connecting a world) reads the
world folder, refreshes the snapshot and adds each actor (player characters as `pc`, others as `npc`), item
and scene (as `place`) to the codex. Descriptions go in the entry's `notes`, never `public`, because Foundry
text can include GM secrets. Each entry records its Foundry UUID and the values last imported. A later
import refreshes an entry only while it still holds those values, so anything edited in Studio is kept and
counted as kept. Journals stay browsable in the World Library. Media is not copied: the result reports how many
files can be browsed. Foundry's files are only read. If the folder cannot be read, the page offers the macro.

## Fallback: the export macro

If the folder cannot be read, use the macro instead:

1. Download the **World Library export macro** in Campaign Studio (under "Use the export macro instead").
2. In the connected world, create a Foundry Script macro with that code and run it as GM.
3. Import the downloaded JSON snapshot on the World Library page.

The macro reads world documents through Foundry's client API and downloads a summary snapshot. It does not
change Foundry. A snapshot you import is never replaced automatically; **Read again now** replaces it with
a folder read.

## What is stored

Either route saves a snapshot only under `DM/data` in the local Studio installation. Journal text, including
GM-visible secrets, can appear in this local snapshot; protect Studio's runtime folder as you would the
Foundry world. The page shows when the documents were read or exported and which route produced them.
The import checks world ID, title and game system against the selected local world. Switching the linked world
hides the previous world's snapshot.

The snapshot includes top-level world scenes, journals and their pages, actors and items. It does not include
compendium documents, embedded documents other than journal pages (for example an actor's own items), or the
complete mechanical data of game-system sheets. Descriptions are displayed as plain text. Editing existing
Foundry documents, connecting them to Studio story threads, and one-click publishing need a Foundry-side
integration and conflict handling. The existing map import macro remains the current path for applying
Studio-generated maps and content.
