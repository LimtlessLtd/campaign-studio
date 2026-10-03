# First run and World Library

Campaign Studio keeps one local Studio project per installation. On a fresh installation, name the project,
choose an existing Foundry world, or create a new world in Foundry Setup and then select it. The world picker
looks in common local Foundry User Data locations; a custom User Data path can be entered. Existing Studio
installations keep their current settings and open the normal dashboard.

The World Library shows media files in the selected world's folder and in shared locations under its
Foundry `Data` folder. It excludes other worlds, game systems and modules. Images can be previewed; all media
is read-only. Foundry can reference assets anywhere inside the `Data` folder, so choosing only a world folder
would miss shared media. See [Foundry User Data](https://foundryvtt.com/article/user-data/).

Foundry scenes, journals, actors and items are stored as documents. To browse them:

1. Download the **World Library export macro** in Campaign Studio.
2. In the connected world, create a Foundry Script macro with that code and run it as GM.
3. Import the downloaded JSON snapshot on the World Library page.

The macro reads world documents through Foundry's client API and downloads a summary snapshot. It does not
change Foundry. The snapshot is saved only under `DM/data` in the local Studio installation. Journal text,
including GM-visible secrets, can appear in this local snapshot; protect Studio's runtime folder as you would
the Foundry world. The page shows the export time. Repeat the export to see changes made later in Foundry.
The import checks world ID, title and game system against the selected local world. Switching the linked world
hides the previous world's snapshot.

The snapshot includes top-level world scenes, journals and their pages, actors and items. It does not include
compendium documents, live changes after export, or the complete mechanical data of game-system sheets.
Descriptions are displayed as plain text. Editing existing Foundry documents, connecting them to Studio
story threads, and one-click publishing need a Foundry-side integration and conflict handling. The existing
map import macro remains the current path for applying Studio-generated maps and content.
