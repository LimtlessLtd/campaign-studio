# Changelog

## Unreleased

- Split the local server into HTTP routes, campaign operations, background job service and a small startup
  entry point. Existing HTTP APIs and stored job formats are unchanged.
- Added subprocess completion and restart recovery regression tests alongside the existing worker survival
  and integration checks.

## 0.1.1 — 2026-10-02

- Retry temporary Windows file replacement failures while holding the document lock; permanent failures
  still propagate after a bounded wait. Added sharing/permanent-failure regression tests.
- Ensure concurrent test subprocesses finish before temporary campaign cleanup, even on failure.

The v0.1.0 tag's release checks caught an intermittent Windows replacement failure and blocked publication.
0.1.1 is the first downloadable alpha release.

## 0.1.0 — 2026-10-02

Initial public alpha release.

- Map workspace with procedural layouts, imported images, location pins, linked content and checkpoints.
- Structured AI draft/review/refine/apply workflow and portable prompt/proposal exchange.
- Codex, story threads, session preparation, image queue, uploads and configurable image endpoint.
- Foundry v12 scene/journal export and D&D 5e NPC/item descriptions through a GM import macro.
- Self-contained source download with checksum, setup instructions, MIT license and privacy allowlist.
- Shared map catalogue process locking and recovery from job result-processing errors.
- Development agent instructions, architecture review, contribution templates and Windows/Linux CI.
