from rdrs.detection import DetectionEngine
from rdrs.policy import Policy


def test_detection_engine_creates_real_detection():
    policy = Policy(
        name="suspicious_file",
        action="deny",
        resource="file",
        condition={
            "path": "C:\\suspicious",
            "severity": "high",
            "confidence": 0.95,
        },
    )

    engine = DetectionEngine([policy])

    detection = engine.detect(
        {
            "resource": "file",
            "context": {
                "path": "C:\\suspicious\\payload.exe",
                "severity": "high",
                "confidence": 0.95,
                "event_type": "created",
            },
        }
    )

    assert detection is not None
    assert detection.rule_name == "suspicious_file"
    assert detection.resource == "file"
    assert detection.severity == "high"
    assert detection.confidence == 0.95
    assert detection.action == "deny"
    assert detection.reason == "policy_match"
    assert detection.evidence["resource"] == "file"
    assert detection.evidence["context"]["event_type"] == "created"
    assert detection.detection_id
    assert detection.timestamp
    assert "severity:high" in detection.tags


def test_detection_engine_deduplicates_identical_events():
    policy = Policy(
        name="blocked_file",
        action="deny",
        resource="file",
        condition={"path": "C:\\blocked"},
    )

    engine = DetectionEngine([policy])

    event = {
        "resource": "file",
        "context": {
            "path": "C:\\blocked\\test.exe",
        },
    }

    first = engine.detect(event)
    second = engine.detect(event)

    assert first is not None
    assert second is None
    assert len(engine.history) == 1


def test_detection_engine_returns_none_without_matching_policy():
    policy = Policy(
        name="network_rule",
        action="deny",
        resource="network",
        condition={"dest": "10.0.0."},
    )

    engine = DetectionEngine([policy])

    detection = engine.detect(
        {
            "resource": "file",
            "context": {
                "path": "C:\\normal\\file.txt",
            },
        }
    )

    assert detection is None


def test_detection_engine_rejects_invalid_confidence():
    policy = Policy(
        name="invalid_confidence",
        action="deny",
        resource="file",
        condition={},
    )

    engine = DetectionEngine([policy])

    try:
        engine.detect(
            {
                "resource": "file",
                "context": {
                    "confidence": 1.5,
                },
            }
        )
    except ValueError as exc:
        assert "confidence" in str(exc)
    else:
        raise AssertionError(
            "Invalid confidence was accepted"
        )