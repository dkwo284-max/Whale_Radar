from usage import record, used_today


def test_record_accumulates_per_day(tmp_path):
    cfg = {"llm": {"guaranteed": 3}}
    record(tmp_path, "2026-10-29", 1, cfg)
    record(tmp_path, "2026-10-29", 2, cfg)
    assert used_today(tmp_path, "2026-10-29") == 3 and used_today(tmp_path, "2026-10-30") == 0
