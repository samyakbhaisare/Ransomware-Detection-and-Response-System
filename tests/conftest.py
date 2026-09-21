"""Shared test configuration and safety guards.

The shipped ``config.yaml`` is the project's real configuration. The API's
``POST /settings`` endpoint persists changes so they survive a restart, which
means a test that runs against the default config path will **rewrite the
repository's configuration file**.

That happened: ``tests/unit/test_api.py`` used a module-level
``TestClient(app)`` with no runtime configuration, so ``POST /settings`` wrote
to the real ``config.yaml`` and silently stripped its comments and settings.

The guard below turns any such corruption into a loud failure and restores the
file, so it can never pass unnoticed again.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_CONFIG = PROJECT_ROOT / "config.yaml"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def protect_shipped_configuration():
    """Fail the run if any test modifies the repository's config.yaml."""
    if not SHIPPED_CONFIG.is_file():
        yield
        return

    original_bytes = SHIPPED_CONFIG.read_bytes()
    original_digest = _digest(SHIPPED_CONFIG)

    yield

    if _digest(SHIPPED_CONFIG) == original_digest:
        return

    # Restore first, so the damage does not persist while the error is read.
    SHIPPED_CONFIG.write_bytes(original_bytes)

    pytest.fail(
        "A test modified the shipped config.yaml. Tests must run against a "
        "temporary configuration (see the client fixture in "
        "tests/unit/test_api.py). The file has been restored.",
        pytrace=False,
    )
