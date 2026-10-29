from usage import over_cap, record


def test_whale_usage_cap(tmp_path):
    cfg = {"llm": {"daily_cap": 4, "expected_per_run": 1}}
    record(tmp_path, "2026-10-29", 2, cfg)
    assert over_cap(tmp_path, "2026-10-29", cfg) == (False, 2, 4)
    record(tmp_path, "2026-10-29", 2, cfg)
    assert over_cap(tmp_path, "2026-10-29", cfg) == (True, 4, 4)
