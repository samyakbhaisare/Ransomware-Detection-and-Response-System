from rdrs.telemetry import ProcessMonitor


def test_process_monitor_captures_current_process():
    monitor = ProcessMonitor()

    process = monitor.capture_current()

    assert process.pid > 0
    assert process.name
    assert process.command_line is not None
    assert process.cwd

    snapshot = monitor.snapshot()

    assert len(snapshot) == 1
    assert snapshot[0]["pid"] == process.pid