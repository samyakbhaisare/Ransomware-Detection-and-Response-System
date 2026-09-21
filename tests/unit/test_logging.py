from pathlib import Path

from rdrs.logging import LOG_FILES, initialize_rotating_logs


def test_rotating_log_files_are_created(tmp_path: Path):
    loggers = initialize_rotating_logs(tmp_path)

    assert set(loggers) == set(LOG_FILES)

    for log_name, filename in LOG_FILES.items():
        loggers[log_name].info(f"{log_name} initialization test")

        log_file = tmp_path / filename
        assert log_file.exists()


def test_log_files_receive_json_logs(tmp_path: Path):
    loggers = initialize_rotating_logs(tmp_path)

    loggers["system"].info("system test")
    loggers["events"].info("event test")
    loggers["alerts"].warning("alert test")
    loggers["errors"].error("error test")
    loggers["audit"].info("audit test")

    for log_name, filename in LOG_FILES.items():
        for handler in loggers[log_name].handlers:
            if hasattr(handler, "flush"):
                handler.flush()

        log_file = tmp_path / filename
        assert log_file.exists()

        content = log_file.read_text(encoding="utf-8")
        assert content
        assert '"message":' in content