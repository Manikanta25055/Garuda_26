"""Logs: the in-memory logs and the full download. Both need an admin session with the master key entered.

Moved out of Garuda_web.py (2026-10). Handler bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`.

/api/history reads everything that is on disk, for any day or moment: the
dashboard's Activity card, the Logs page's date and search, and Narada's
search_history all ask it, so a time copied from one is found by the others.
"""
import asyncio
import datetime
import re

from fastapi import APIRouter, Depends, HTTPException, Response

try:
    from ..garuda_core import log_history
    from ..garuda_auto import actuation_log
except ImportError:
    from basic_pipelines.garuda_core import log_history
    from basic_pipelines.garuda_auto import actuation_log

SOURCES = ("system", "detection", "devices", "presence", "voice")
# The Logs page's own, behind the master key: phone addresses, and what every
# person said to Narada.
PRIVATE = ("presence", "voice")
MAX_LIMIT = 500
DEFAULT_WINDOW_MIN = 5
# Where to look for the nearest thing either side when a window is empty.
NEAREST_DAYS = 7
# Lines that say nothing happened: the presence check wrote one every 30 s
# until 2026-10 and the logs on disk are full of them. Left out of the Activity
# card and of what Narada reads, unless asked for (routine=1) or searched for.
ROUTINE = re.compile(r"^\[PRESENCE\] (?:Match|No match) [—–-] \d+ active ARP entries$")


def build_logs_router(core):
    router = APIRouter()

    @router.get("/api/logs")
    async def get_logs(session=Depends(core.require_logs)):
        return {
            "system_log": core.system_updates_log,
            "voice_log": core.voice_assistant_log,
            "voice_responses": core.voice_responses,
            "presence_log": core.STATE.presence.log[-200:],
            "detection_log": core._detection_log[-200:],
        }

    @router.get("/api/logs/download")
    async def download_logs(session=Depends(core.require_logs)):
        """Return all permanent logs as a single combined text file for download."""
        # Up to 30 MB of files are read here: on a worker thread, not the loop.
        content = await asyncio.to_thread(core._combined_log_text)
        fname = f"garuda-full-log-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # ── history: every day on disk ───────────────────────────────────────────

    def _files():
        return {"system": core.PERM_SYSTEM_LOG, "detection": core.PERM_DETECTION_LOG,
                "voice": core.PERM_VOICE_LOG}

    def _allowed(session):
        """The sources this person may read, and why the others are not."""
        if session.get("role") == "admin" and session.get("logs_unlocked"):
            return list(SOURCES), {}
        return ([s for s in SOURCES if s not in PRIVATE],
                {s: "only on the Logs page, after the master key" for s in PRIVATE})

    def _hidden_line(session, text):
        return (session.get("role") != "admin"
                and any(tag in text for tag in core._ADMIN_ONLY_LOG_TAGS))

    def _devices(start, end):
        ctx = getattr(core, "DRISHTI_CTX", None)
        path = getattr(ctx, "log_path", None)
        if not path:
            return []
        names = {d["id"]: d["name"] for d in getattr(ctx.registry, "devices", [])}
        rules = {r.get("id"): r.get("source_utterance") for r in getattr(ctx.store, "rules", [])}
        out = []
        for e in actuation_log._read(path):
            try:
                ts = log_history.stamp(datetime.datetime.fromtimestamp(float(e["ts"])))
            except (KeyError, TypeError, ValueError, OSError):
                continue
            if not start <= ts < end:
                continue
            cause = (f"rule: {rules.get(e['rule_id']) or e['rule_id']}" if e.get("rule_id")
                     else e.get("source") or "manual")
            text = f"{names.get(e.get('device'), e.get('device'))} {e.get('action')}"
            if not e.get("ok", True):
                text += f" FAILED ({e.get('reason') or 'no reason given'})"
            text += f" — {cause}" + (f", by {e['actor']}" if e.get("actor") else "")
            out.append((ts, text))
        return out

    def _presence(start, end):
        return [(e.get("ts", ""), f"{e.get('device') or 'Unknown'} {e.get('event', '')} "
                                  f"({e.get('mac') or 'no mac'})")
                for e in list(core.STATE.presence.log) if start <= e.get("ts", "") < end]

    def _collect(session, sources, start, end, q, newest=None, routine=True):
        """Chronological entries of the given sources with start <= ts < end.

        newest=N: only the newest N are wanted (and one more, to tell that
        there are more); the log files are then read newest first and no further.
        """
        out = []
        for source in sources:
            def accept(ts, text, source=source):
                if source == "system" and _hidden_line(session, text):
                    return False
                if not routine and source == "system" and ROUTINE.match(text):
                    return False
                return not q or log_history.matches(f"{source} {text}", q)
            if source in _files():
                lines = log_history.read_lines(_files()[source], core._LOG_KEEP_ROTATED,
                                               start, end, accept, newest)
            else:
                lines = [(ts, text) for ts, text in
                         (_devices(start, end) if source == "devices" else _presence(start, end))
                         if accept(ts, text)]
                if newest is not None:
                    lines = lines[-(newest + 1):]
            out.extend({"ts": ts, "source": source, "text": text} for ts, text in lines)
        out.sort(key=lambda e: e["ts"])
        return out[-(newest + 1):] if newest is not None else out

    def _bad(message):
        raise HTTPException(400, message)

    def _history(session, at, window_minutes, start, end, date, q, sources, limit, before,
                 routine=False):
        core._do_flush_logs()               # lines still waiting in the write buffer
        allowed, refused = _allowed(session)
        asked = [s for s in re.split(r"[^a-z]+", (sources or "").lower()) if s]
        unknown = [s for s in asked if s not in SOURCES]
        if unknown:
            _bad(f"unknown source {unknown[0]!r}; the sources are {', '.join(SOURCES)}")
        chosen = [s for s in (asked or allowed) if s in allowed]
        not_searched = {s: refused[s] for s in asked if s in refused}
        limit = max(1, min(int(limit or 40), MAX_LIMIT))
        now = datetime.datetime.now()
        lo, hi, centre, read_as = "", log_history.stamp(now + datetime.timedelta(minutes=1)), "", ""
        if at:
            moment = log_history.parse_moment(at, now)
            if moment is None:
                _bad(f"could not read a time from {at!r}; give it as YYYY-MM-DD HH:MM:SS")
            if moment.precision >= 86400:
                lo = log_history.stamp(moment.dt)
                hi = log_history.stamp(moment.dt + datetime.timedelta(days=1))
                read_as = f"the whole of {moment.dt:%A %d %B %Y}"
            else:
                # 14:33 means the minute and 2 pm the hour: the middle of it is the centre.
                mid = moment.dt + datetime.timedelta(seconds=(moment.precision - 1) / 2)
                half = max(int(window_minutes or DEFAULT_WINDOW_MIN) * 60, moment.precision / 2)
                lo = log_history.stamp(mid - datetime.timedelta(seconds=half))
                hi = log_history.stamp(mid + datetime.timedelta(seconds=half + 1))
                centre = log_history.stamp(mid)
                read_as = f"{moment.dt:%A %d %B %Y %H:%M:%S}" + (
                    "" if moment.has_date else " (no date was given: the most recent one)")
        elif date:
            moment = log_history.parse_moment(date, now)
            if moment is None:
                _bad(f"could not read a date from {date!r}; give it as YYYY-MM-DD")
            day = datetime.datetime.combine(moment.dt.date(), datetime.time())
            lo = log_history.stamp(day)
            hi = log_history.stamp(day + datetime.timedelta(days=1))
            read_as = f"the whole of {day:%A %d %B %Y}"
        if start or end:
            if start:
                m = log_history.parse_moment(start, now)
                if m is None:
                    _bad(f"could not read a time from {start!r}")
                lo = max(lo, log_history.stamp(m.dt))
            if end:
                m = log_history.parse_moment(end, now)
                if m is None:
                    _bad(f"could not read a time from {end!r}")
                # An end given as a day or a minute includes all of it.
                last = m.dt + datetime.timedelta(seconds=m.precision)
                hi = min(hi, log_history.stamp(last))
            read_as = read_as or f"from {lo or 'the beginning'} to {hi}"
        if before:
            m = log_history.parse_moment(before, now)
            if m is None:
                _bad(f"could not read a time from {before!r}")
            # Up to and including that second: lines that share it with the last
            # one shown are not skipped (the page drops the ones it has).
            hi = min(hi, log_history.stamp(m.dt + datetime.timedelta(seconds=1)))
        # Around a moment the window is small and all of it is read, to centre
        # on the moment. Otherwise the newest `limit` are wanted, and the old
        # files are read only as far back as that takes; the counts are then
        # "at least" (omitted_before > 0 still means there is more).
        routine = routine or bool(q)
        entries = _collect(session, chosen, lo, hi, q, None if centre else limit, routine)
        kept, omitted_before, omitted_after = log_history.pick(entries, limit, centre)
        out = {"from": lo or None, "to": hi, "at": centre or None, "read_as": read_as or None,
               "query": q or None, "searched": chosen, "total": len(entries),
               "omitted_before": omitted_before, "omitted_after": omitted_after,
               "entries": kept}
        if not routine:
            out["routine_left_out"] = ("presence checks that found nothing new; "
                                       "ask with routine=true to include them")
        if not_searched:
            out["not_searched"] = not_searched
        if not entries and lo:
            # Nothing in the window: what came just before and just after it.
            near_lo = log_history.stamp(datetime.datetime.strptime(lo, log_history.STAMP_FMT)
                                        - datetime.timedelta(days=NEAREST_DAYS))
            near_hi = log_history.stamp(datetime.datetime.strptime(hi, log_history.STAMP_FMT)
                                        + datetime.timedelta(days=NEAREST_DAYS))
            earlier = _collect(session, chosen, near_lo, lo, q, 1, routine)
            later = _collect(session, chosen, hi, near_hi, q, None, routine)
            out["nearest_before"] = earlier[-1] if earlier else None
            out["nearest_after"] = later[0] if later else None
        return out

    @router.get("/api/history")
    async def history(at: str = "", window_minutes: int = 0, start: str = "", end: str = "",
                      date: str = "", q: str = "", sources: str = "", limit: int = 40,
                      before: str = "", routine: bool = False,
                      session=Depends(core.require_session)):
        """What the logs hold for a moment, a day or a range, from every file on disk.

        at       a time as written or copied ("2026-10-02 14:33:05", "14:33", "yesterday 9pm");
                 the window_minutes either side of it (5 by default) are returned, nearest first
        date     a whole day; start/end a range; before pages back (up to and including
                 that second)
        q        words that must all appear; sources a comma list of system, detection,
                 devices, presence, voice (the last two need the master key)
        routine  also the presence checks that found nothing new (always, when q is given)
        """
        return await asyncio.to_thread(_history, session, at[:80], window_minutes, start[:80],
                                       end[:80], date[:40], q[:200], sources[:80], limit,
                                       before[:40], routine)

    @router.get("/api/history/days")
    async def history_days(source: str = "system", session=Depends(core.require_session)):
        """The days a log has lines for, newest first, with how many."""
        allowed, _ = _allowed(session)
        if source not in allowed or source not in _files():
            raise HTTPException(400, f"no day index for {source!r}")
        counts = await asyncio.to_thread(log_history.day_counts, _files()[source],
                                         core._LOG_KEEP_ROTATED)
        return {"source": source,
                "days": [{"date": d, "count": counts[d]} for d in sorted(counts, reverse=True)]}

    return router
