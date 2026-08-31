from __future__ import annotations

from rdrs.policy import Policy, PolicyEngine


def test_policy_engine_enforces_rule():
    policy = Policy(name="default", action="allow", resource="file", condition={"path": "/tmp"})
    engine = PolicyEngine([policy])

    decision = engine.evaluate("file", {"path": "/tmp/example.txt"})
    assert decision["allowed"] is True
    assert decision["policy"] == "default"


def test_policy_engine_rejects_unknown_resource():
    engine = PolicyEngine([])
    decision = engine.evaluate("network", {"dest": "1.2.3.4"})
    assert decision["allowed"] is False
    assert decision["reason"] == "no_policy"
