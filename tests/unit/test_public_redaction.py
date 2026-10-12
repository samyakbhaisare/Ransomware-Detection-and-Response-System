from rdrs.public_redaction import redact


def test_public_projection_preserves_relationships_and_removes_nested_secrets():
    secret = "C:/Users/PrivateUser/Documents/private.docx"
    data = {"id": 7, "created_at": "2026-09-24T12:00:00+00:00", "path": secret,
            "metadata": {"entropy": 7.9, "username": "PrivateUser", "private": secret},
            "evidence": {"signals": {"rapid_encryption": True, "secret": secret},
                         "affected_files": [secret], "suspect_process": {"pid": 44, "name": "private.exe", "command_line": secret}},
            "message": secret, "unknown_private_evidence": secret}
    public = redact(data)
    assert public["id"] == 7
    assert public["created_at"] == data["created_at"]
    assert public["path"] == public["evidence"]["affected_files"][0]
    assert public["metadata"] == {"entropy": 7.9}
    assert public["evidence"]["signals"] == {"rapid_encryption": True}
    assert public["evidence"]["suspect_process"]["pid"] == 44
    assert "PrivateUser" not in str(public) and "private.exe" not in str(public)
    assert data["path"] == secret


def test_redaction_handles_numeric_chart_arrays_and_configuration():
    result = redact({"telemetry": {"cpu": [0.1, 80], "entropy": [7.9]},
                     "config": {"name": "private-host", "environment": "PrivateUser", "shutdown_timeout": 15,
                                "persistence_enabled": True, "db_path": "secret", "detection_thresholds": {"cpu_percent": 80}},
                     "subsystems": {"database": {"healthy": True, "details": {"error": "secret"}}}})
    assert result["telemetry"] == {"cpu": [0.1, 80], "entropy": [7.9]}
    assert result["config"]["persistence_enabled"] is True
    assert result["config"]["detection_thresholds"] == {"cpu_percent": 80}
    assert result["subsystems"] == {"database": {"healthy": True}}
