from __future__ import annotations

from rdrs.config_engine import ConfigEngine


def test_config_engine_validates_monitoring_sections():
    engine = ConfigEngine(
        monitoring={"paths": ["/tmp"], "recursive": True},
        detection={"rules": ["mass_rename"]},
        response={"actions": ["log"]},
        ai={"enabled": False},
    )

    payload = engine.to_dict()
    assert payload["monitoring"]["paths"] == ["/tmp"]
    assert payload["detection"]["rules"] == ["mass_rename"]
    assert payload["ai"]["enabled"] is False


def test_config_engine_rejects_invalid_config():
    try:
        ConfigEngine(monitoring={"paths": []}, detection={"rules": []}, response={"actions": []}, ai={"enabled": True}).validate()
        assert False, "Expected validation error"
    except ValueError:
        pass
