from pathlib import Path

from rdrs.storage import DatabaseManager
from rdrs.telemetry import (
    NetworkConnectionTelemetry,
    ProcessTelemetry,
    UserSessionTelemetry,
)


def test_database_persists_monitoring_evidence(tmp_path: Path):
    db = DatabaseManager(tmp_path / "rdrs.db")
    db.initialize()

    file_id = db.record_file_event(
        {
            "path": str(tmp_path / "sample.txt"),
            "action": "created",
            "previous_path": None,
            "new_path": None,
            "metadata": {
                "size": 10,
                "sha256": "abc123",
            },
        }
    )

    process_id = db.record_process(
        ProcessTelemetry(
            pid=1234,
            name="test-process",
            parent_pid=1000,
            command_line="test-process --scan",
            cwd=str(tmp_path),
        )
    )

    user_id = db.record_user_session(
        UserSessionTelemetry(
            user="test-user",
            session_id="test-session",
            privilege="user",
            home=str(tmp_path),
        )
    )

    network_id = db.record_network_connection(
        NetworkConnectionTelemetry(
            family="AF_INET",
            local_address="127.0.0.1:5000",
            remote_address="127.0.0.1:443",
            protocol="tcp",
            port=443,
        )
    )

    assert file_id > 0
    assert process_id > 0
    assert user_id > 0
    assert network_id > 0

    assert db.count_records("file_events") == 1
    assert db.count_records("process_telemetry") == 1
    assert db.count_records("user_sessions") == 1
    assert db.count_records("network_connections") == 1

    db.close()