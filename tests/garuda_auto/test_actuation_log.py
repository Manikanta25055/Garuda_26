import pytest
from basic_pipelines.garuda_auto import actuation_log as alog

pytestmark = pytest.mark.unit

MATCHED = [{"field": "occupancy", "op": "==", "value": "empty"}]


def test_record_then_read_back(tmp_path):
    path = str(tmp_path / "actuations.jsonl")
    alog.record(path, device="fan", action="off", rule_id="r_001",
                matched=MATCHED, ok=True)
    entries = alog.recent(path)
    assert len(entries) == 1
    assert entries[0]["device"] == "fan"
    assert entries[0]["matched"] == MATCHED
    assert entries[0]["ok"] is True


def test_failed_actuation_keeps_its_reason(tmp_path):
    path = str(tmp_path / "actuations.jsonl")
    alog.record(path, device="heater", action="on", rule_id="r_002",
                matched=MATCHED, ok=False, reason="device 'heater' is unreachable")
    assert alog.recent(path)[0]["reason"] == "device 'heater' is unreachable"


def test_recent_returns_newest_first(tmp_path):
    path = str(tmp_path / "actuations.jsonl")
    ticks = iter([1.0, 2.0, 3.0])
    for device in ("a", "b", "c"):
        alog.record(path, device=device, action="on", rule_id="r",
                    matched=[], ok=True, clock=lambda: next(ticks))
    assert [e["device"] for e in alog.recent(path)] == ["c", "b", "a"]


def test_recent_respects_the_limit(tmp_path):
    path = str(tmp_path / "actuations.jsonl")
    for i in range(10):
        alog.record(path, device=f"d{i}", action="on", rule_id="r", matched=[], ok=True)
    assert len(alog.recent(path, limit=3)) == 3


def test_last_for_finds_the_most_recent_entry_for_one_device(tmp_path):
    path = str(tmp_path / "actuations.jsonl")
    alog.record(path, device="fan", action="on", rule_id="r_001", matched=[], ok=True)
    alog.record(path, device="lamp", action="on", rule_id="r_002", matched=[], ok=True)
    alog.record(path, device="fan", action="off", rule_id="r_003", matched=[], ok=True)
    assert alog.last_for(path, "fan")["rule_id"] == "r_003"


def test_missing_file_reads_as_empty(tmp_path):
    assert alog.recent(str(tmp_path / "nothing.jsonl")) == []
    assert alog.last_for(str(tmp_path / "nothing.jsonl"), "fan") is None


def test_a_corrupt_line_does_not_break_the_read(tmp_path):
    path = tmp_path / "actuations.jsonl"
    alog.record(str(path), device="fan", action="on", rule_id="r", matched=[], ok=True)
    with open(path, "a") as fh:
        fh.write("{ not json\n")
    assert len(alog.recent(str(path))) == 1
