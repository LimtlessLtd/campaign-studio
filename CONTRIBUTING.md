# Contributing

Start with [development setup](docs/DEVELOPMENT.md) and [architecture](docs/ARCHITECTURE.md). Coding agents
also follow [AGENTS.md](AGENTS.md). Small fixes are welcome; discuss broad storage, provider or Foundry
changes in an issue so their compatibility and migration requirements are clear.

Use synthetic campaign examples. Do not upload screenshots, logs, prompt packs or Foundry files containing
your campaign or credentials. Describe bugs with steps, versions, expected behavior and a minimal public
fixture. For UI changes, include a synthetic screenshot and the viewport you checked.

Keep PRs focused, describe the user outcome and tests, and identify migrations or unverified integrations.
CI must pass. A maintainer reviews outside contributions, especially persistence and Foundry ownership
changes, before merging. The owner's coding agents merge their own PRs under the review relay in
[development](docs/DEVELOPMENT.md). Update the source manifest for new files and add a changelog entry for
user-visible behavior.

Contributions are provided under the repository's MIT license. Foundry VTT and optional AI services are
separate products; this project does not bundle them or their credentials.
