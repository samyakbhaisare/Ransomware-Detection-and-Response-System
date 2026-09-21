from rdrs.scoring import ThreatLevel, ThreatScoringEngine


def test_normal_score():
    score = ThreatScoringEngine().calculate(
        high_entropy=True,
    )

    assert score.total == 25
    assert score.level == ThreatLevel.NORMAL


def test_warning_score():
    score = ThreatScoringEngine().calculate(
        rapid_encryption=True,
    )

    assert score.total == 40
    assert score.level == ThreatLevel.WARNING


def test_critical_score():
    score = ThreatScoringEngine().calculate(
        rapid_encryption=True,
        mass_rename=True,
    )

    assert score.total == 70
    assert score.level == ThreatLevel.CRITICAL


def test_score_is_capped_at_100():
    score = ThreatScoringEngine().calculate(
        rapid_encryption=True,
        mass_rename=True,
        high_entropy=True,
        cpu_spike=True,
        unknown_program=True,
    )

    assert score.total == 100