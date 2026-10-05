"""Public Foundry upgrade API, preserving existing callers and test fixtures."""

import config
import foundry_backup
from foundry_catalog import (
    MAX_MANIFEST,
    MAX_PAGE,
    PACKAGE_URL,
    RELEASES_URL,
    collect_catalog as _collect_catalog,
    fetch as _fetch,
    package_releases,
    stable_builds,
)
from foundry_compat import (
    CORE_RE,
    ID_RE,
    INVENTORY_FORMAT,
    compatible as _compatible,
)
from foundry_solver import analyze
from foundry_upgrade_workflow import (
    audit_migration,
    complete_inventory as _complete_inventory,
    prepare_clone,
    report as _report,
    review_clone,
)


def collect_catalog(inventory):
    return _collect_catalog(inventory, fetcher=_fetch)


def report(inventory, backup_path, disabled_modules=(), approved_dependencies=(), catalog=None):
    return _report(
        inventory,
        backup_path,
        disabled_modules,
        approved_dependencies,
        catalog,
        catalog_loader=collect_catalog,
    )
