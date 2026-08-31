from __future__ import annotations

from rdrs.events import EventBus, SecurityEvent


def test_event_bus_publish_and_route():
    bus = EventBus()
    received: list[SecurityEvent] = []

    bus.subscribe("sensor.file", lambda event: received.append(event))
    event = bus.publish(
        "sensor.file",
        event_type="FILE_CREATE",
        payload={"path": "/tmp/example.txt", "size": 7},
        source="sensor.file",
    )

    assert event.event_id
    assert event.type == "FILE_CREATE"
    assert event.payload["path"] == "/tmp/example.txt"
    assert len(received) == 1
    assert received[0].event_id == event.event_id


def test_event_bus_rejects_duplicate_event_ids():
    bus = EventBus()
    event = SecurityEvent(
        event_id="dup-1",
        source="sensor.test",
        route="sensor.test",
        type="FILE_MODIFY",
        payload={"path": "a"},
    )
    bus.publish_event(event)
    assert bus.publish_event(event) is None


def test_event_bus_drain_returns_all_events():
    bus = EventBus()
    bus.publish("sensor.file", event_type="FILE_DELETE", payload={"path": "/tmp/x"}, source="sensor.file")
    bus.publish("sensor.file", event_type="FILE_DELETE", payload={"path": "/tmp/y"}, source="sensor.file")

    drained = bus.drain()
    assert len(drained) == 2
    assert {item.type for item in drained} == {"FILE_DELETE"}
