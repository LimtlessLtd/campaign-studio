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
text can include GM secrets. Each entry records its source world, Foundry UUID and the values last imported.
A later import refreshes an entry only while it still holds those values, so anything edited in Studio is
kept and counted as kept. Foundry IDs can be reused in another world; importing that world creates separate
entries. Entries imported by the earlier unscoped importer are preserved and may appear a second time on the
first reimport because their source world cannot be established safely. Journals stay browsable in the World
Library. Media is not copied: the result reports how many files can be browsed. Supported local image paths
on codex entries display through the read-only Foundry asset route while their world is selected; remote,
missing and unsupported images are left blank. Foundry's files are only read. If the folder cannot be read,
first-run setup shows the error and the page offers the macro. The result also reports documents omitted by
the 5,000-per-kind limit and when the media listing reached its limit.

## Fallback: the export macro

If the folder cannot be read, use the macro instead:

1. Download the **World Library export macro** in Campaign Studio (under "Use the export macro instead").
2. In the connected world, create a Foundry Script macro with that code and run it as GM.
3. Import the downloaded JSON snapshot on the World Library page. Its actors, items and scenes enter the
   codex by the same rules as a folder import.

The macro reads world documents through Foundry's client API and downloads a summary snapshot. It does not
change Foundry. A snapshot you import is never replaced automatically; **Read again now** replaces it with
a folder read.

## Live GM connection

In World Library, download the **live bridge macro**, create a Foundry Script macro with that code, and run
it as GM in the selected world. It opens a Studio World Library tab. Approve **Connect and import** in that
tab after checking the offered world and Foundry origin. The macro reads through Foundry's client document
API, includes only documents the GM can observe, and sends the same bounded summary format as the export
macro. It does not enable CORS, copy a Studio access code, or edit Foundry. The two tabs must stay open.
If Studio asks for its access code, sign in and return to World Library; the macro continues announcing the
connection. A blocked popup needs to be allowed before rerunning the macro.

Foundry create, update and delete hooks mark the view as changed. Press **Refresh from Foundry** to receive
a fresh snapshot. Studio records `live` as its source and scopes imported entries to the selected world and
their Foundry UUIDs. If a value was edited in Studio, refresh keeps that entry rather than overwriting it;
deleted Foundry documents remain in Studio for the GM to review. Switching worlds cannot display the old
snapshot. This is one-way reading, not a write-back path. The macro and tab pairing have synthetic tests;
live Foundry v11/v12/v13 behavior still needs GM verification.

## What is stored

All three routes save snapshots and imported codex entries under `DM/data` in the local Studio installation.
Journal text, including GM-visible secrets, can appear in this local snapshot; protect Studio's runtime
folder as you would the Foundry world. The page shows when the documents were read or exported and which
route produced them.
The import checks world ID, title and game system against the selected local world. Switching the linked world
hides the previous world's snapshot.

The snapshot includes top-level world scenes, journals and their pages, actors and items. It does not include
compendium documents, embedded documents other than journal pages (for example an actor's own items), or the
complete mechanical data of game-system sheets. Descriptions are displayed as plain text. Editing existing
Foundry documents, connecting them to Studio story threads, and one-click publishing need a GM-reviewed
write-back integration and expected-revision conflict handling. The existing map import macro remains the
current path for applying Studio-generated maps and content.
