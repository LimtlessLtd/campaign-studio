# Campaign Studio

[![CI](https://github.com/LimtlessLtd/campaign-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/LimtlessLtd/campaign-studio/actions/workflows/ci.yml)

**Public alpha · MIT licensed · runs on your computer.**

Download the source ZIP from the [releases page](https://github.com/LimtlessLtd/campaign-studio/releases),
or clone this repository. Release downloads include a SHA-256 checksum. Python 3.11+ and dependency
installation are required; there is no bundled executable yet. Node is only needed by developers.

A local campaign manager built around places: create or import a map, mark locations, and link characters,
items, events, journal entries, images and story threads. Python serves a plain HTML/CSS/JavaScript UI.
There is no frontend build step. The source is MIT licensed; your campaign material remains yours.

## Run locally

Use Python 3.11 or later. From the extracted source folder:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r DM/requirements.txt
python DM/server.py
```

On Windows, `start.bat` creates the virtual environment and installs dependencies on first launch.
On macOS/Linux, run `sh start.sh`. Subsequent starts use the existing environment. To upgrade dependencies,
activate the environment and rerun the pip install command. Keep only one server running per campaign.

Open http://127.0.0.1:8766. On first run, name the Studio project and select an existing Foundry world, or
create a world in Foundry Setup and then select it. The world picker detects common local User Data folders;
you can also enter a custom path. The server binds to localhost.
Set `DM_PORT` before launch to use a different port.

## Maps and iteration

- Create a procedural city district, or ask AI to design a custom environment: dungeon, cave, tavern,
  temple, ship or wilderness. Choose dimensions, visual theme, seed and content targets in the wizard.
- Layout AI returns bounded operations for rooms, paths, furniture and scatter. A planning preview and
  validation warnings appear before **Apply & render** generates the detailed map, walls and lights.
- **Refine map** starts a proposal from the current layout and your change request. Proposals become stale
  if the layout or its location pins change. Save named checkpoints; compare and restore earlier layouts.
- Imported PNG/JPEG/WebP maps retain their artwork and grid scale. Add location pins and generate linked
  content. Imported artwork has no editable grid plan; its key can still be checkpointed and restored.
- A new map can automatically queue a content draft after rendering. Content drafts respect your selected
  counts and thread connections. Apply them after review. Per-location Generate buttons use the same workflow.

## AI and images

Install and authenticate Claude Code separately to use the built-in AI runner. The CLI must support
`--json-schema`, `--restricted`, `--tools` and `--strict-mcp-config`. Optionally set its model in Settings.
Structured workflows run with file and shell tools disabled. **Export prompt pack** and **Import proposal**
let you use another assistant without the CLI. See `DM/AI_WORKFLOW.md`.
General Requests use the same draft, review and apply pattern for codex entries, threads and session prep.
Link a session before requesting encounters or handouts. Applied requests keep their stable IDs; use a new
follow-up request for more additions. Existing map requests open the map studio.

Image briefs are queued with their content. Upload artwork, or configure a local HTTP / remote HTTPS
image endpoint. The request contract is `{model, prompt, size, n: 1}`; the response must include
`data[0].b64_json`. Use an environment variable for the API key; settings store its name only.
Images are generated individually when you press **Generate image**. Different providers may need an adapter.

## Foundry

**World Library** browses media in the connected Foundry User Data folder. To browse existing scenes,
journals, actors and items, download its export Script macro, run it as GM in Foundry, and import the JSON
snapshot. It is read-only and shows when the snapshot was made; later Foundry changes require another export.
See [first run and World Library](docs/FOUNDRY_LIBRARY.md).

**Prepare for Foundry → Update Foundry export** copies assets and scene JSON to the selected world's
`Data/wotg-maps` folder. Download the Script macro and run it as GM in Foundry. The server does not write the
world database. Scenes contain grid, generated walls/doors/lights and roof tiles where available. Journals
and location pins are GM-only. Reimports update generated parts and preserve custom tokens and notes.

The macro also creates/updates D&D 5e NPC and loot item sheets by stable studio IDs. Stat blocks and item
mechanics are notes: they need GM review and are not automatically converted into attacks or activities.
Other game systems receive linked character/item information in the journal; sheet adapters are future work.
The scene format targets Foundry v12. Live compatibility with each Foundry/system version needs verification.

**Foundry backup and restore test** in Settings can create a verified offline copy of a local Foundry User Data folder
and an isolated restore test copy. Close Foundry first and test opening the restored world in its original
Foundry version. This backup stage does not migrate a world.
See [Foundry backup and restore test](docs/FOUNDRY_BACKUP.md).

**Foundry upgrade compatibility report** in Settings accepts an inventory exported by a GM Script macro in
the original v12 world and a verified backup of that world. It checks Foundry's stable release and package
directory pages, release manifests, the game system, enabled modules and required dependencies. It shows the
newest full match, any unverified packages, exclusions including Plutonium, and blockers. The inventory and
report are saved beside the backup. After a verified restore test copy is opened and inspected in v12, Studio
can make a separate v12 clone with a saved plan of modules to disable and package releases to install. The GM
must disable excluded modules such as Plutonium in the clone before its first launch in newer Foundry. Studio
can then compare a fresh v12 clone inventory with that plan and save a review beside the backup. Studio does
not yet install packages, run migration or cut over a live world.

## Your files

Runtime content lives in `DM/data`, maps in `DM/maps`, artwork in `DM/uploads`. Back up these directories.
Document saves retain previous versions in `DM/data/.history`; layout checkpoints live beside each map.
Story thread statuses are open, planned, foreshadowed and resolved. Session prep and the codex remain editable.
Text handouts drafted for a session appear in that session's prep, with player text and GM secrets separated.
Local reference notes can be added as `DM/data/notes.txt`. An existing neighbouring `Website/content`
folder can supply read-only legacy session/hero references if `legacy_references: true` is added to local
settings; this compatibility import is disabled by default. Handouts shows uploaded studio images.

**Build clean source package** uses an explicit code allowlist. It excludes campaign JSON, maps, uploads,
local settings, job logs, credentials and campaign-specific agent instructions. Source packages are written
to `DM/exports`. This does not publish a repository.

## Status and development

This initial alpha supports the map-driven preparation workflow and a read-only snapshot of selected world
documents. It does not yet live-sync or edit existing Foundry documents from Studio.
The [code review and roadmap](docs/CODE_REVIEW.md) describe the remaining work toward managing an entire
campaign. Live AI availability depends on your provider; live Foundry compatibility needs version-specific
verification. Existing inbox requests remain readable and can be drafted through the structured workflow.

See [CONTRIBUTING](CONTRIBUTING.md), [development workflow](docs/DEVELOPMENT.md),
[architecture](docs/ARCHITECTURE.md), and [coding-agent instructions](AGENTS.md).
Report bugs through GitHub issues using synthetic examples. See [SECURITY](SECURITY.md) for private reports.

To update a private installation, stop the server, back up its runtime directories and copy only the new
source files. Follow migration notes in [CHANGELOG](CHANGELOG.md). Do not overwrite your runtime data or
attach a private campaign backup to a public issue or release.

Foundry Virtual Tabletop and Dungeons & Dragons are separate products. This project is an independent tool
and does not include Foundry, game rulebooks, campaign artwork or AI service subscriptions.
