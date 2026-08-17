from core.weekly_radar_audit import RadarIdea, evaluate_radar


def test_conditional_paths_are_scored_separately_and_enter_next_bar():
    bars = {"XYZ": [
        {"time": "2026-08-10T13:30:00Z", "open": 99, "high": 100, "low": 98, "close": 99},
        {"time": "2026-08-10T13:35:00Z", "open": 99, "high": 102, "low": 99, "close": 101},
        {"time": "2026-08-10T13:40:00Z", "open": 101.5, "high": 104, "low": 101, "close": 103},
    ]}
    result = evaluate_radar([RadarIdea("XYZ", 100, (103,), (97,))], bars, start="2026-08-10", end="2026-08-14")
    idea = result["ideas"][0]
    assert idea["status"] == "BOTH_DIRECTIONS_TRIGGERED"
    assert idea["bullish"]["next_bar_entry"] == 101.5
    assert idea["bullish"]["targets_reached"] == [103]
    assert result["summary"]["triggered_conditional_paths"] == 2


def test_bullish_only_non_trigger_is_explicit():
    bars = {"XYZ": [{"time": "2026-08-10T13:30:00Z", "open": 99, "high": 99, "low": 98, "close": 99}]}
    result = evaluate_radar([RadarIdea("XYZ", 100, bullish_only=True)], bars, start="2026-08-10", end="2026-08-14")
    assert result["ideas"][0]["status"] == "NOT_TRIGGERED"
