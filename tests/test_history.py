"""The permanent logs read back by day, by moment and by words: /api/history.

The dashboard's Activity card showed only what was in the last 50 lines of the
state push, and Narada could not reach a line older than the in-memory 500.
These hold the files on disk, all their rotated generations, as the source.
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw
from garuda_core import log_history
from garuda_core.log_history import parse_moment

NOW = datetime.datetime(2026, 10, 3, 9, 0, 0)


# ── reading a time ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected,precision", [
    ("[2026-10-02 14:33:05]", "2026-10-02 14:33:05", 1),
    ("2026-10-02T14:33:05.123456", "2026-10-02 14:33:05", 1),
    ("what happened at 2026-10-02 14:33", "2026-10-02 14:33:00", 60),
    ("2026-10-02", "2026-10-02 00:00:00", 86400),
    ("14:33:05", "2026-10-02 14:33:05", 1),            # later than now today: yesterday's
    ("08:15", "2026-10-03 08:15:00", 60),
    ("2:33 pm", "2026-10-02 14:33:00", 60),
    ("2 Oct 2:33 pm", "2026-10-02 14:33:00", 60),
    ("Oct 2nd, 2026 at 14:33", "2026-10-02 14:33:00", 60),
    ("2nd of October at 11:05:59 PM", "2026-10-02 23:05:59", 1),
    ("yesterday 9pm", "2026-10-02 21:00:00", 3600),
    ("yesterday", "2026-10-02 00:00:00", 86400),
    ("last thursday at noon", "2026-10-01 12:00:00", 60),
    ("2/10 14:33:05", "2026-10-02 14:33:05", 1),         # day first
    ("02.10.2026 08:00", "2026-10-02 08:00:00", 60),
    ("knife at 14:33:05 conf=0.87", "2026-10-02 14:33:05", 1),
    ("Dec 25", "2025-12-25 00:00:00", 86400),           # not yet this year: last year's
])
def test_parse_moment(text, expected, precision):
    m = parse_moment(text, NOW)
    assert m is not None, text
    assert log_history.stamp(m.dt) == expected
    assert m.precision == precision


@pytest.mark.parametrize("text", ["", "nothing", "I decided 3 things", "the dog sat at 5",
                                  "31/02/2026", "99:99"])
def test_parse_moment_finds_no_time(text):
    assert parse_moment(text, NOW) is None


# ── reading the files ─────────────────────────────────────────────────────────

def _write(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in lines))


def test_every_generation_is_read_in_order(tmp_path):
    log = str(tmp_path / "perm_system_log.txt")
    _write(log + ".2", ["[2026-09-20 10:00:00] oldest"])
    _write(log + ".1", ["[2026-09-28 10:00:00] older", "[2026-09-28 10:00:01] two lines",
                        "of one message"])
    _write(log, ["[2026-10-02 14:33:05] newest"])
    lines = log_history.read_lines(log, 10)
    assert [t for _, t in lines] == ["oldest", "older", "two lines\nof one message", "newest"]
    assert log_history.day_counts(log, 10) == {"2026-09-20": 1, "2026-09-28": 2,
                                               "2026-10-02": 1}
    # A day only opens the files that cover it.
    assert log_history.read_lines(log, 10, "2026-09-28", "2026-09-29") == [
        ("2026-09-28 10:00:00", "older"), ("2026-09-28 10:00:01", "two lines\nof one message")]


def test_index_follows_appends_and_rotation(tmp_path):
    log = str(tmp_path / "perm.txt")
    _write(log, ["[2026-10-01 10:00:00] a"])
    assert log_history.day_counts(log, 10) == {"2026-10-01": 1}
    with open(log, "a") as f:
        f.write("[2026-10-02 10:00:00] b\n")
    assert log_history.day_counts(log, 10) == {"2026-10-01": 1, "2026-10-02": 1}
    os.rename(log, log + ".1")
    _write(log, ["[2026-10-03 10:00:00] c"])
    assert log_history.day_counts(log, 10) == {"2026-10-01": 1, "2026-10-02": 1,
                                               "2026-10-03": 1}


def test_the_newest_lines_do_not_open_old_files(tmp_path, monkeypatch):
    log = str(tmp_path / "perm.txt")
    _write(log + ".2", ["[2026-09-01 10:00:00] old"])
    _write(log + ".1", ["[2026-09-15 10:00:00] older"])
    _write(log, [f"[2026-10-02 10:00:{s:02d}] new {s}" for s in range(10)])
    opened = []
    real = log_history._read_file
    monkeypatch.setattr(log_history, "_read_file",
                        lambda p, a, b: opened.append(os.path.basename(p)) or real(p, a, b))
    got = log_history.read_lines(log, 10, newest=5)
    assert [t for _, t in got] == [f"new {s}" for s in range(4, 10)]   # five, and one to say more
    assert opened == ["perm.txt"]
    # A search keeps going back until it has enough.
    opened.clear()
    got = log_history.read_lines(log, 10, accept=lambda ts, t: "old" in t, newest=5)
    assert [t for _, t in got] == ["old", "older"] and len(opened) == 3


def test_rotation_keeps_old_generations(tmp_path, monkeypatch):
    log = str(tmp_path / "perm.txt")
    monkeypatch.setattr(gw, "_LOG_KEEP_ROTATED", 3)
    for i in range(5):
        _write(log, [f"[2026-10-0{i + 1} 10:00:00] gen {i}"])
        gw._rotate_log(log)
    # Three old files kept, newest first: .1 is the last one rotated.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["perm.txt.1", "perm.txt.2",
                                                          "perm.txt.3"]
    assert "gen 4" in open(log + ".1").read()
    assert "gen 2" in open(log + ".3").read()


def test_pick_keeps_what_is_nearest_the_moment():
    entries = [{"ts": f"2026-10-02 14:{m:02d}:00"} for m in range(60)]
    kept, before, after = log_history.pick(entries, 10, "2026-10-02 14:30:00")
    assert kept[0]["ts"] == "2026-10-02 14:25:00" and kept[-1]["ts"] == "2026-10-02 14:34:00"
    assert (before, after) == (25, 25)
    kept, before, after = log_history.pick(entries, 10)
    assert kept[-1]["ts"] == "2026-10-02 14:59:00" and (before, after) == (50, 0)


# ── the endpoint ──────────────────────────────────────────────────────────────

@pytest.fixture
def logs_on_disk(app_client):
    _write(gw.PERM_SYSTEM_LOG + ".1", [
        "[2026-09-30 08:00:00] Mode night set to True by admin",
        "[2026-09-30 08:05:00] Login: admin",
    ])
    _write(gw.PERM_SYSTEM_LOG, [
        "[2026-10-02 14:30:00] Pipeline started.",
        "[2026-10-02 14:33:05] Alert triggered.",
        "[2026-10-02 14:33:06] Email alert sent.",
        "[2026-10-02 15:00:00] Admin login: admin",
        "[2026-10-02 18:00:00] Mode night set to False by user",
    ])
    _write(gw.PERM_DETECTION_LOG, [
        "[2026-10-02 14:33:04] [DANGER] knife conf=0.87 — alert triggered",
    ])
    _write(gw.PERM_VOICE_LOG, ["[2026-10-02 14:34:00] You said: is anyone home"])
    yield


def _get(client, headers, **params):
    return client.get("/api/history", params=params, headers=headers)


def test_moment_returns_what_happened_around_it(app_client, user_headers, logs_on_disk):
    r = _get(app_client, user_headers, at="[2026-10-02 14:33:05]")
    assert r.status_code == 200
    d = r.json()
    assert d["at"] == "2026-10-02 14:33:05"
    assert d["from"] == "2026-10-02 14:28:05" and d["to"] == "2026-10-02 14:38:06"
    texts = [(e["source"], e["text"]) for e in d["entries"]]
    assert texts == [("system", "Pipeline started."),
                     ("detection", "[DANGER] knife conf=0.87 — alert triggered"),
                     ("system", "Alert triggered."), ("system", "Email alert sent.")]
    # The Narada log is the Logs page's, not everyone's.
    assert "voice" not in d["searched"]


def test_a_day_from_an_older_file(app_client, user_headers, logs_on_disk):
    d = _get(app_client, user_headers, date="2026-09-30", sources="system").json()
    # A user does not see sign-ins, as on the dashboard.
    assert [e["text"] for e in d["entries"]] == ["Mode night set to True by admin"]


def test_admin_sees_sign_ins(app_client, admin_headers, logs_on_disk):
    d = _get(app_client, admin_headers, date="2026-09-30", sources="system").json()
    assert len(d["entries"]) == 2


def test_words_are_searched_across_all_days(app_client, user_headers, logs_on_disk):
    d = _get(app_client, user_headers, q="mode night").json()
    assert [e["ts"] for e in d["entries"]] == ["2026-09-30 08:00:00", "2026-10-02 18:00:00"]


def test_paging_back_with_before(app_client, user_headers, logs_on_disk):
    d = _get(app_client, user_headers, sources="system", limit=2).json()
    assert [e["ts"] for e in d["entries"]] == ["2026-10-02 14:33:06", "2026-10-02 18:00:00"]
    assert d["omitted_before"] >= 1          # "at least": the older file was not read
    # Up to and including the oldest one shown: the page drops what it already has.
    d = _get(app_client, user_headers, sources="system", limit=3,
             before=d["entries"][0]["ts"]).json()
    assert [e["ts"] for e in d["entries"]] == ["2026-10-02 14:30:00", "2026-10-02 14:33:05",
                                               "2026-10-02 14:33:06"]
    assert d["omitted_before"] == 1         # 30 September, in the older file


def test_an_empty_window_says_what_is_either_side(app_client, user_headers, logs_on_disk):
    d = _get(app_client, user_headers, at="2026-10-02 16:30:00").json()
    assert d["total"] == 0
    assert d["nearest_before"]["ts"] == "2026-10-02 14:33:06"
    assert d["nearest_after"]["ts"] == "2026-10-02 18:00:00"


def test_private_sources_need_the_master_key(app_client, admin_headers, logs_on_disk):
    d = _get(app_client, admin_headers, at="2026-10-02 14:34", sources="voice,system").json()
    assert d["searched"] == ["system"] and "voice" in d["not_searched"]
    app_client.post('/api/master_key/verify', json={'key': 'test-master-key-12345'},
                    headers=admin_headers)
    d = _get(app_client, admin_headers, at="2026-10-02 14:34", sources="voice").json()
    assert [e["text"] for e in d["entries"]] == ["You said: is anyone home"]


def test_unreadable_time_is_a_clear_400(app_client, user_headers, logs_on_disk):
    r = _get(app_client, user_headers, at="whenever")
    assert r.status_code == 400 and "could not read a time" in r.json()["detail"]
    assert _get(app_client, user_headers, sources="nope").status_code == 400


def test_days_index(app_client, user_headers, logs_on_disk):
    r = app_client.get("/api/history/days", params={"source": "system"}, headers=user_headers)
    assert r.json()["days"] == [{"date": "2026-10-02", "count": 5},
                                {"date": "2026-09-30", "count": 2}]


def test_history_needs_a_sign_in(app_client):
    assert app_client.get("/api/history").status_code == 401


def test_routine_presence_checks_are_left_out_unless_asked(app_client, user_headers):
    _write(gw.PERM_SYSTEM_LOG, [
        "[2026-10-02 20:09:15] [PRESENCE] Match — 9 active ARP entries",
        "[2026-10-02 20:09:36] Master key login.",
        "[2026-10-02 20:09:47] [PRESENCE] Match – 9 active ARP entries",
        "[2026-10-02 20:10:32] Pipeline started.",
        "[2026-10-02 20:10:34] [OWNER] Phone arrived — device detected on network.",
    ])
    d = _get(app_client, user_headers, at="2026-10-02 20:10:34", sources="system").json()
    assert [e["text"] for e in d["entries"]] == [
        "Pipeline started.", "[OWNER] Phone arrived — device detected on network."]
    assert "routine_left_out" in d
    # The Logs page asks for them; a search for them finds them.
    d = _get(app_client, user_headers, at="2026-10-02 20:10:34", sources="system",
             routine="true").json()
    assert len(d["entries"]) == 4
    assert _get(app_client, user_headers, q="active ARP").json()["total"] == 2


def test_static_files_are_asked_after_on_every_load(app_client):
    r = app_client.get("/static/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
