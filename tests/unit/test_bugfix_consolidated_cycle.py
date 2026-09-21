"""Regression tests for the consolidated bug-fix cycle (B-01 … B-11).

Each test is written against the defect as it was observed in the read-only
audit (``reports/READONLY_BUG_AUDIT_20260919.md``), so a reintroduction fails
here.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from rdrs.api import app, prune_reports
from rdrs.config import ConfigConflictError, RuntimeConfig
from rdrs.runtime import RuntimeManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_config(tmp_path: Path, **overrides) -> tuple[Path, RuntimeConfig]:
    """A temp config carrying watch_paths, ready to load and save."""
    path = tmp_path / "rdrs.yaml"
    path.write_text(
        (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    for key, value in overrides.items():
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(key, value), encoding="utf-8")

    return path, RuntimeConfig.load(path)


# ==========================================================================
# B-01 — an empty settings payload must not rewrite the configuration
# ==========================================================================


def test_b01_empty_payload_does_not_rewrite_the_config(tmp_path):
    """A save with nothing changed must not touch the file.

    The first save normalises the file into the canonical commented form; the
    second must be a true no-op.
    """
    path, _ = make_config(tmp_path)

    RuntimeConfig.load(path).save(path)

    before = path.read_bytes()

    RuntimeConfig.load(path).save(path)

    assert path.read_bytes() == before


def test_b01_empty_settings_body_leaves_the_file_untouched(tmp_path):
    """POST /settings with {} must not touch config.yaml."""
    for name in ("data", "logs"):
        (tmp_path / name).mkdir()

    path, _ = make_config(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)

    path.write_text(
        (PROJECT_ROOT / "config.yaml")
        .read_text(encoding="utf-8")
        .replace("data_dir: data", f"data_dir: {(tmp_path / 'data').as_posix()}")
        .replace("logs_dir: logs", f"logs_dir: {(tmp_path / 'logs').as_posix()}")
        .replace(
            "db_path: data\\rdrs.db",
            f"db_path: {(tmp_path / 'data' / 'rdrs.db').as_posix()}",
        )
        .replace(
            "  watch_paths:\n    - data\n",
            f"  watch_paths:\n    - {(tmp_path / 'data').as_posix()}\n",
        ),
        encoding="utf-8",
    )

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path)

    with TestClient(app) as client:
        before = path.read_bytes()

        response = client.post("/settings", json={})

        assert response.status_code == 200
        assert path.read_bytes() == before, (
            "an empty settings payload rewrote the configuration"
        )

        # watch_paths and comments survive.
        text = path.read_text(encoding="utf-8")

        assert "watch_paths" in text
        assert "# Folders the file monitor watches" in text

    RuntimeManager.reset_singleton()


# ==========================================================================
# B-02 — save() must preserve watch_paths and comments
# ==========================================================================


def test_b02_to_config_dict_includes_watch_paths(tmp_path):
    _, config = make_config(tmp_path)

    rendered = config.to_config_dict()

    assert "watch_paths" in rendered["runtime"]
    assert rendered["runtime"]["watch_paths"] == [
        str(item) for item in config.watch_paths
    ]


def test_b02_to_dict_and_to_config_dict_agree_on_watch_paths(tmp_path):
    _, config = make_config(tmp_path)

    assert "watch_paths" in config.to_dict()
    assert "watch_paths" in config.to_config_dict()["runtime"]


def test_b02_save_preserves_watch_paths_and_comments(tmp_path):
    path, config = make_config(tmp_path)

    config.log_level = "DEBUG"
    config.save(path)

    text = path.read_text(encoding="utf-8")
    on_disk = yaml.safe_load(text)

    assert on_disk["runtime"]["watch_paths"], "watch_paths was dropped"
    assert "# Folders the file monitor watches" in text, "comments were dropped"
    assert on_disk["logging"]["level"] == "DEBUG"


def test_b02_relative_watch_paths_stay_relative(tmp_path):
    path, config = make_config(tmp_path)

    assert config.watch_paths_raw == ["data"]

    config.environment = "staging"
    config.save(path)

    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert on_disk["runtime"]["watch_paths"] == ["data"]


def test_b02_monitored_folders_survive_a_save_and_reload(tmp_path):
    path, config = make_config(tmp_path)

    before = [p.name for p in config.monitored_paths]

    config.log_level = "ERROR"
    config.save(path)

    reloaded = RuntimeConfig.load(path)

    assert [p.name for p in reloaded.monitored_paths] == before


def test_b02_empty_subsystems_round_trips(tmp_path):
    """A bare ``subsystems:`` parses as None and the loader rejects it."""
    path, config = make_config(tmp_path)

    config.subsystems = []
    config.save(path)

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["subsystems"] == []

    reloaded = RuntimeConfig.load(path)
    assert reloaded.subsystems == []


def test_b02_render_is_always_loadable(tmp_path):
    path, config = make_config(tmp_path)

    for mutations in (
        {"subsystems": []},
        {"watch_paths": [], "watch_paths_raw": []},
        {"log_level": "CRITICAL"},
    ):
        for key, value in mutations.items():
            setattr(config, key, value)

        rendered = config.render_yaml()

        assert isinstance(yaml.safe_load(rendered), dict)


# ==========================================================================
# B-03 — empty / null / invalid persistence values
# ==========================================================================


def test_b03_missing_key_is_unchanged(tmp_path):
    """A payload that omits the field must leave the stored value alone.

    The value is pinned here rather than inherited from the live config.yaml,
    so the test does not break when the operator changes their own setting.
    """
    path, config = make_config(tmp_path)

    config.persistence_enabled = True
    config.save(path)

    reloaded = RuntimeConfig.load(path)
    assert reloaded.persistence_enabled is True

    # Saving again without touching the field keeps it True.
    reloaded.log_level = "WARNING"
    reloaded.save(path)

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["persistence"][
        "enabled"
    ] is True


def test_b03_api_rejects_a_non_boolean_persistence_value(tmp_path):
    for name in ("data", "logs"):
        (tmp_path / name).mkdir()

    path, _ = make_config(tmp_path)

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path)

    with TestClient(app) as client:
        # Settings are editable only while monitoring is stopped (FSD).
        client.post("/monitor/stop")

        # Pydantic coerces the documented boolean literals ...
        assert (
            client.post(
                "/settings", json={"persistence_enabled": "false"}
            ).status_code
            == 200
        )

        # ... and refuses anything that is not a boolean at all.
        assert (
            client.post(
                "/settings", json={"persistence_enabled": "maybe"}
            ).status_code
            == 422
        )

    RuntimeManager.reset_singleton()


def test_b03_false_and_true_are_both_accepted(tmp_path):
    for name in ("data", "logs"):
        (tmp_path / name).mkdir()

    path, _ = make_config(tmp_path)

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path)

    with TestClient(app) as client:
        # Settings are only editable while monitoring is stopped (FSD).
        assert client.post("/monitor/stop").status_code == 200

        assert (
            client.post("/settings", json={"persistence_enabled": False}).status_code
            == 200
        )
        assert (
            client.post("/settings", json={"persistence_enabled": True}).status_code
            == 200
        )

    RuntimeManager.reset_singleton()


# ==========================================================================
# B-04 — concurrent / stale writers
# ==========================================================================


def test_b04_stale_writer_is_refused(tmp_path):
    path, stale = make_config(tmp_path)

    # Another writer changes the file.
    other = RuntimeConfig.load(path)
    other.name = "newer-writer"
    other.save(path)

    # The stale instance must not clobber it.
    stale.log_level = "DEBUG"

    with pytest.raises(ConfigConflictError):
        stale.save(path)

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["runtime"][
        "name"
    ] == "newer-writer"


def test_b04_force_overrides_the_conflict_check(tmp_path):
    path, stale = make_config(tmp_path)

    other = RuntimeConfig.load(path)
    other.name = "newer-writer"
    other.save(path)

    stale.name = "forced"
    stale.save(path, force=True)

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["runtime"][
        "name"
    ] == "forced"


def test_b04_conflict_is_reported_as_409(tmp_path):
    for name in ("data", "logs"):
        (tmp_path / name).mkdir()

    path, _ = make_config(tmp_path)

    RuntimeManager.reset_singleton()
    runtime = RuntimeManager(config_path=path)

    with TestClient(app) as client:
        # Stop first: otherwise the settings endpoint answers 409 for the
        # RUNNING-state lock and this test would pass without ever reaching the
        # conflict check it is meant to exercise.
        assert client.post("/monitor/stop").status_code == 200

        # Change the file behind the runtime's back.
        current = RuntimeConfig.load(path)
        current.name = "changed-elsewhere"
        current.save(path)

        response = client.post("/settings", json={"log_level": "DEBUG"})

        assert response.status_code == 409

    RuntimeManager.reset_singleton()


def test_b04_a_backup_is_written_before_replacing_the_file(tmp_path):
    path, config = make_config(tmp_path)

    config.name = "changed"
    config.save(path)

    backups = list(tmp_path.glob(f"{path.name}.bak-*"))

    assert backups, "no pre-write backup was created"


# ==========================================================================
# B-05 — process snapshot semantics
# ==========================================================================


def test_b05_dashboard_labels_process_count_as_snapshots():
    html = (
        PROJECT_ROOT / "src" / "rdrs" / "dashboard" / "index.html"
    ).read_text(encoding="utf-8")

    assert "Process Snapshots" in html
    assert "not the number of processes running now" in html


# ==========================================================================
# B-06 — responsive layout
# ==========================================================================


def test_b06_topbar_stretches_so_controls_can_wrap():
    """`align-items: flex-start` made .controls max-content and overflow."""
    html = (
        PROJECT_ROOT / "src" / "rdrs" / "dashboard" / "index.html"
    ).read_text(encoding="utf-8")

    block = html[html.index("@media (max-width: 650px)") :][:1200]

    assert "align-items: stretch" in block


def test_b06_status_line_wraps_on_narrow_screens():
    html = (
        PROJECT_ROOT / "src" / "rdrs" / "dashboard" / "index.html"
    ).read_text(encoding="utf-8")

    block = html[html.index("@media (max-width: 650px)") :][:1600]

    assert "flex-wrap: wrap" in block
    assert "margin-left: 0" in block


# ==========================================================================
# B-07 — report retention
# ==========================================================================


def test_b07_prune_keeps_the_newest_and_removes_the_rest(tmp_path):
    for index in range(30):
        for suffix in (".json", ".csv"):
            path = tmp_path / f"incident_7_20260101T0000{index:02d}Z{suffix}"
            path.write_text("{}", encoding="utf-8")
            time.sleep(0.001)

    result = prune_reports(tmp_path, 7, keep=10)

    remaining = sorted(tmp_path.glob("incident_7_*"))

    assert len(remaining) == 10
    assert len(result["removed"]) == 50
    assert result["blocked"] == []
    assert result["considered"] == 60
    # The newest JSON and CSV survive even under a tight cap.
    assert any(name.endswith(".json") for name in result["kept"])
    assert any(name.endswith(".csv") for name in result["kept"])


def test_b07_prune_never_touches_other_incidents(tmp_path):
    for index in range(5):
        (tmp_path / f"incident_1_2026010{index}T000000Z.json").write_text(
            "{}", encoding="utf-8"
        )

    other = tmp_path / "incident_2_20260101T000000Z.json"
    other.write_text("{}", encoding="utf-8")

    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")

    prune_reports(tmp_path, 1, keep=2)

    assert other.is_file()
    assert unrelated.is_file()


def test_b07_prune_keeps_at_least_one_of_each_format(tmp_path):
    for index in range(6):
        (tmp_path / f"incident_3_2026010{index}T000000Z.json").write_text(
            "{}", encoding="utf-8"
        )

    csv_file = tmp_path / "incident_3_20260101T000000Z.csv"
    csv_file.write_text("a,b", encoding="utf-8")

    prune_reports(tmp_path, 3, keep=2)

    assert csv_file.is_file(), "the only CSV was deleted"
    assert list(tmp_path.glob("incident_3_*.json"))


def test_b07_prune_is_a_no_op_below_the_threshold(tmp_path):
    for index in range(3):
        (tmp_path / f"incident_4_2026010{index}T000000Z.json").write_text(
            "{}", encoding="utf-8"
        )

    result = prune_reports(tmp_path, 4, keep=10)

    assert result["removed"] == []
    assert result["blocked"] == []
    assert result["considered"] == 3
    assert len(list(tmp_path.glob("incident_4_*"))) == 3


# ==========================================================================
# B-08 / B-09 — refresh message and duplicate request
# ==========================================================================


def test_b08_refresh_message_names_the_current_page():
    html = (
        PROJECT_ROOT / "src" / "rdrs" / "dashboard" / "index.html"
    ).read_text(encoding="utf-8")

    # The literal must not appear inside a showMessage(...) call.
    import re

    calls = re.findall(r"showMessage\(([^;]{0,200})", html)

    assert not any("Dashboard refreshed." in call for call in calls)
    assert "pageTitles[page]" in html


def test_b09_settings_refresh_reuses_the_status_payload():
    """The poll must not repopulate the Settings form.

    The poll used to call ``loadSettings(status, false)`` on every 5-second
    cycle, which overwrote whatever the operator was editing. It now calls
    ``markSettingsPolled()``, which updates only the status line. The ability to
    inject an already-fetched ``/status`` payload is still supported, so a
    caller can avoid a duplicate request -- which was the original point of this
    test.
    """
    html = (
        PROJECT_ROOT / "src" / "rdrs" / "dashboard" / "index.html"
    ).read_text(encoding="utf-8")

    # The repopulating poll call is gone for good.
    assert "loadSettings(status, false)" not in html

    # The poll updates the status line only.
    assert "markSettingsPolled()" in html
    assert "function markSettingsPolled()" in html

    # Payload reuse is still available to any caller that wants it. The
    # signature is multi-line, so the parameters are asserted individually
    # rather than as one literal string.
    assert "async function loadSettings(" in html
    assert "status = null," in html
    assert "announce = true," in html


# ==========================================================================
# B-10 — cross-origin protection
# ==========================================================================


def test_b10_foreign_origin_is_refused_on_settings(tmp_path):
    for name in ("data", "logs"):
        (tmp_path / name).mkdir()

    path, _ = make_config(tmp_path)

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path)

    with TestClient(app) as client:
        response = client.post(
            "/settings",
            json={"log_level": "DEBUG"},
            headers={"Origin": "https://evil.example"},
        )

        assert response.status_code == 403

    RuntimeManager.reset_singleton()


def test_b10_local_origin_is_accepted(tmp_path):
    for name in ("data", "logs"):
        (tmp_path / name).mkdir()

    path, _ = make_config(tmp_path)

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path)

    with TestClient(app) as client:
        # Settings are editable only while monitoring is stopped (FSD).
        client.post("/monitor/stop")

        for origin in (
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ):
            response = client.post(
                "/settings",
                json={"log_level": "INFO"},
                headers={"Origin": origin},
            )

            assert response.status_code == 200, origin

    RuntimeManager.reset_singleton()


def test_b10_a_request_without_origin_is_allowed(tmp_path):
    for name in ("data", "logs"):
        (tmp_path / name).mkdir()

    path, _ = make_config(tmp_path)

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path)

    with TestClient(app) as client:
        # The test client sends no Origin, like curl or the CLI.
        assert client.post("/settings", json={}).status_code == 200

    RuntimeManager.reset_singleton()


def test_b10_reads_are_unaffected_by_the_origin_check(tmp_path):
    for name in ("data", "logs"):
        (tmp_path / name).mkdir()

    path, _ = make_config(tmp_path)

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path)

    with TestClient(app) as client:
        for route in ("/status", "/events", "/alerts", "/reports"):
            response = client.get(
                route,
                headers={"Origin": "https://evil.example"},
            )

            assert response.status_code == 200, route

    RuntimeManager.reset_singleton()


# ==========================================================================
# B-11 — ISO started_at
# ==========================================================================


def test_b11_status_exposes_an_iso_started_at(tmp_path):
    for name in ("data", "logs"):
        (tmp_path / name).mkdir()

    path, _ = make_config(tmp_path)

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path)

    with TestClient(app) as client:
        payload = client.get("/status").json()

        assert "started_at" in payload
        assert "started_at_iso" in payload

        if payload["started_at_iso"] is not None:
            from datetime import datetime

            parsed = datetime.fromisoformat(payload["started_at_iso"])

            assert parsed.tzinfo is not None

    RuntimeManager.reset_singleton()
