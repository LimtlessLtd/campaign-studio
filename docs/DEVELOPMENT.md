# Development and review workflow

## Setup

```sh
python -m venv .venv
# Activate it: Windows .venv\Scripts\activate; macOS/Linux source .venv/bin/activate
python -m pip install -r requirements-dev.txt
npm ci
python DM/server.py
```

Python 3.11+ runs the app. Node 24+ is used for formatting and JavaScript checks; users do not need Node.
Run from the repository root. Tests construct temporary campaigns and a fake Foundry world and use a fake
image endpoint. They do not invoke paid AI services or import documents into a live world.

## Implement a change

1. Describe the user outcome and acceptance criteria. Read the affected module and its callers.
2. Identify stored fields, stable IDs, concurrency and export implications before choosing an approach.
3. Implement within module boundaries. Preserve old saved documents or provide an explicit migration.
4. Add a focused regression test for changed business behavior. For UI work, exercise the actual flow.
5. Format, run the checks below, inspect the diff and update docs/changelog/manifest.
6. Open a PR with the change, evidence and remaining limitations. Review and resolve findings before merge.

## Required checks

```sh
python -m ruff format DM tools tests
python -m ruff check DM tools tests
npm run format
python tools/check_source.py
python -m compileall -q DM tools tests
npm run check:js
python DM/tools/test_workflows.py
python -m unittest discover -s tests -v
python DM/packaging_source.py
git diff --check
```

CI checks formatting without modifying files and runs integration/release regression tests on Windows and
Ubuntu with Python 3.11 and 3.14. It also tests syntax, the source manifest and the extracted download's
startup/repackaging. The renderer integration fixture is deliberately small; it validates geometry and
output without a large performance run.

For a new source file, manually add its relative path to `source_manifest.json`. Do not generate that list
from every file in a working directory. `check_source.py` rejects files outside the manifest (using tracked
files plus non-ignored untracked files in a Git checkout); extracted archives are checked against the same
manifest. Private runtime directories may exist locally but are excluded from source.

## UI verification

Start with an empty campaign or run `python DM/tools/preview_fixture.py` for a disposable synthetic review
fixture on the port printed by the script. Press Enter to close and remove the fixture.

- Desktop and approximately 430px wide: navigation, forms, map canvas and inspector should remain usable.
- Create/import a map, select a pin, change fields, navigate away/back and confirm persistence.
- Import a JSON proposal, review, refine, apply; inspect linked codex entries, journals/events and image queue.
- Check save conflicts, stale proposals, failed jobs and empty states for the changed feature.
- For image/Foundry changes use fake providers/worlds first. Record live checks separately, including versions.

Never put a screenshot or log containing a real campaign into a public PR. Inspect generated walls using
the check image and the scene JSON. Syntax validation does not confirm live Foundry compatibility.

## Review checklist

- Does the change satisfy the acceptance criteria and handle failure/empty states?
- Can concurrent saves lose edits? Can retry create duplicate objects? Is a multi-file failure recoverable?
- Are paths constrained, model text escaped, sizes bounded and tool/provider access intentional?
- Are stable IDs, checkpoints and old campaign documents preserved?
- Does Foundry reimport preserve user-owned documents and keep secrets GM-only?
- Is the downloaded archive runnable, self-contained and free of campaign files/credentials?
- Are tests meaningful and docs accurate about integrations not checked live?

## Release

1. Update `CHANGELOG.md` and the review/compatibility notes, then pass CI on main.
2. Check the manifest and source diff. Build and smoke-test the source archive.
3. With release authorization, create and push a `vX.Y.Z` tag at the tested commit.
4. The release workflow reruns checks, builds the archive/checksum, and publishes a GitHub release.
   Tags beginning `v0.` are marked prerelease while the app is alpha.
5. Verify the release assets and workflow result. Never attach a runtime backup as a release asset.

The release workflow has write permission only in its final publication job. PR tests have read-only
permissions and do not use secrets or `pull_request_target`. Actions are pinned to reviewed commit SHAs.
Dependency updates open reviewable PRs through Dependabot.

To update an existing private installation, stop its server, back up its runtime directories, and copy new
source files without overwriting `DM/data`, `DM/maps` or `DM/uploads`. Read migration notes before restarting.
Keep any private campaign instructions local. Do not move the private installation into the public repo.
