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
# From this many lines on, a result also says what kinds of line they were.
KINDS_FROM = 8
# Lines that say nothing happened: the presence check wrote one every 30 s
# until 2026-10 and the logs on disk are full of them. Left out of the Activity
# card and of what Narada reads, unless asked for (routine=1) or searched for.
ROUTINE = re.compile(r"^\[PRESENCE\] (?:Match|No match) [—–-] \d+ active ARP entries$")
# Written at every start under the [TAMPER] tag though nothing was tampered
# with: a search for tampering found 320 of these around 190 covered lenses.
STARTUP_NOTICE = "[TAMPER] Dead-man switch disabled"


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

    def _its_own_device(text, q):
        """A device line reads "Fan on — rule: ... turn on lamp and fan": a search
        for the lamp must not find the fan's line by the rule it quotes."""
        ctx = getattr(core, "DRISHTI_CTX", None)
        named = {"on", "off"}
        for d in getattr(getattr(ctx, "registry", None), "devices", []):
            named.update(str(d.get("name", "")).lower().split())
            named.add(str(d.get("id", "")).lower())
        head = text.split(" — ", 1)[0].lower().split()
        asked = [w for w in re.split(r"[^a-z0-9_]+", q.lower()) if w in named]
        return all(w in head for w in asked) or " or " in q.lower()

    def _once(entries):
        """The camera writes a covered lens and a person to the system log and
        to the detection log in the same second. Read together, that is one
        event: counted twice it was "188 in system and 187 in detection"."""
        blind = set()
        for e in entries:
            if e["source"] == "system" and e["text"].startswith("[TAMPER] Camera blindness"):
                at = datetime.datetime.strptime(e["ts"], log_history.STAMP_FMT)
                blind.update(log_history.stamp(at + datetime.timedelta(seconds=d))
                             for d in (-2, -1, 0, 1, 2))
        seen = {e["ts"] for e in entries
                if e["source"] == "detection" and e["text"].lower().startswith("[watch] person")}
        return [e for e in entries if not (
            (e["source"] == "detection" and e["ts"] in blind
             and e["text"].startswith("[TAMPER] camera_blind"))
            or (e["source"] == "system" and e["ts"] in seen
                and e["text"].startswith("[WATCH] Person (")))]

    def _collect(session, sources, start, end, q, newest=None, routine=True, any_word=False,
                 groups=()):
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
                if groups and log_history.group(text) not in groups:
                    return False
                if q and source == "devices" and not any_word and not _its_own_device(text, q):
                    return False
                if q and text.startswith(STARTUP_NOTICE) and "dead" not in q.lower():
                    return False
                return not q or log_history.matches(f"{source} {text}", q, any_word)
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
                 routine=False, digest=False, kind=""):
        if q and not at and re.fullmatch(r"[\d:.\s]+(?:[ap]\.?m\.?)?", q.strip(), re.I) \
                and getattr(log_history.parse_moment(q), "has_time", False):
            # "4:30" given as the words to look for is a time.
            at, q, date = f"{date} {q}".strip(), "", ""
        core._do_flush_logs()               # lines still waiting in the write buffer
        allowed, refused = _allowed(session)
        asked = [s for s in re.split(r"[^a-z]+", (sources or "").lower()) if s]
        unknown = [s for s in asked if s not in SOURCES]
        if unknown:
            _bad(f"unknown source {unknown[0]!r}; the sources are {', '.join(SOURCES)}")
        chosen = [s for s in (asked or allowed) if s in allowed]
        not_searched = {s: refused[s] for s in asked if s in refused}
        groups = tuple(k for k in re.split(r"[^a-z]+", (kind or "").lower()) if k)
        if any(k not in log_history.GROUPS for k in groups):
            _bad(f"unknown kind; the kinds are {', '.join(log_history.GROUPS)}")
        limit = max(1, min(int(limit or 40), MAX_LIMIT))
        now = datetime.datetime.now()
        lo, hi, centre, read_as = "", log_history.stamp(now + datetime.timedelta(minutes=1)), "", ""
        one_day = False
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
        elif date and log_history.parse_span(date, now):
            first, last, read_as = log_history.parse_span(date, now)
            lo, hi = log_history.stamp(first), log_history.stamp(last)
        elif date:
            moment = log_history.parse_moment(date, now)
            if moment is None:
                _bad(f"could not read a date from {date!r}; give it as YYYY-MM-DD")
            day = datetime.datetime.combine(moment.dt.date(), datetime.time())
            lo = log_history.stamp(day)
            hi = log_history.stamp(day + datetime.timedelta(days=1))
            read_as = f"the whole of {day:%A %d %B %Y}"
            one_day = True
        if start or end:
            # With a date as well, a start or an end inside that day narrows it
            # (10:00 to 18:00) and one outside it widens it: Narada gave
            # date=1 October with end=31 October for "in October", was handed
            # the 1st alone, and went on day by day until its time ran out.
            wider = False
            if start:
                m = log_history.parse_moment(start, now)
                if m is None:
                    _bad(f"could not read a time from {start!r}")
                first = log_history.stamp(m.dt)
                wider = one_day and first < lo
                lo = first if wider else max(lo, first)
            if end:
                m = log_history.parse_moment(end, now)
                if m is None:
                    _bad(f"could not read a time from {end!r}")
                # An end given as a day or a minute includes all of it.
                last = log_history.stamp(m.dt + datetime.timedelta(seconds=m.precision))
                if one_day and last > hi:
                    wider = True
                    hi = min(last, log_history.stamp(now + datetime.timedelta(minutes=1)))
                else:
                    hi = min(hi, last)
            if wider or not read_as:
                read_as = f"from {lo or 'the beginning'} to {hi}"
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
        # A day, a range or a search is read whole, so that the count is the
        # count: Narada was handed the newest 40 of 206 knife detections with
        # "total: 41" and said there had been 41. Only the open-ended page of
        # everything (the Logs page scrolling back) stops at what it shows.
        whole = bool(centre or lo or q) and not before
        notes = {}
        entries = _collect(session, chosen, lo, hi, q, None if whole else limit, routine,
                           groups=groups)
        if q and not entries:
            wider = [s for s in allowed if s not in chosen]
            found = _collect(session, wider, lo, hi, q, None, routine, groups=groups) if wider else []
            if found:
                notes["searched_instead"] = (f"nothing in {', '.join(chosen)}; these are from "
                                             f"{', '.join(wider)}")
                entries, chosen = found, wider
            elif len(q.split()) > 1:
                entries = _collect(session, chosen, lo, hi, q, None, routine, any_word=True,
                                   groups=groups)
                if entries:
                    notes["loose_match"] = ("no line has all of these words; these have at "
                                            "least one, so check each is what was asked")
        if whole and "system" in chosen and "detection" in chosen:
            entries = _once(entries)
        kept, omitted_before, omitted_after = log_history.pick(entries, limit, centre)
        out = {"from": lo or None, "to": hi, "at": centre or None, "read_as": read_as or None,
               "query": q or None, "searched": chosen, "total": len(entries),
               "omitted_before": omitted_before, "omitted_after": omitted_after, **notes}
        if whole and (len(entries) > len(kept) or len(entries) > KINDS_FROM):
            # What the range held, line for line: the lines not shown, and a
            # search for "email" that also found "Mode email_off set".
            out["kinds"] = log_history.summary(entries)
            # "When was the last one" asked of 126 lines sorted by how common
            # they are got the wrong line; the two ends are said outright.
            out["earliest"], out["latest"] = entries[0], entries[-1]
            days = {}
            for e in entries:
                days[e["ts"][:10]] = days.get(e["ts"][:10], 0) + 1
            if 1 < len(days) <= 62:
                out["per_day"] = days
            if digest and not centre and len(entries) > len(kept):
                kept = log_history.digest(entries, limit)
                del out["omitted_before"], out["omitted_after"]
                out["not_shown"] = len(entries) - len(kept)
                out["entries_are"] = ("a selection: every line of the kinds that are few, the "
                                      "first and last of the kinds that repeat. kinds counts "
                                      "every line of the range")
        elif not whole and len(entries) > len(kept):
            out["total_is"] = "at least this many: give a date, a range or words for an exact count"
        out["entries"] = kept
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
            # A thing searched for by name is looked for as far back as the
            # logs go: "when did a mode last change" is eleven days ago.
            earlier = _collect(session, chosen, "" if q else near_lo, lo, q, 1, routine)
            later = _collect(session, chosen, hi, near_hi, q, None, routine)
            out["nearest_before"] = earlier[-1] if earlier else None
            out["nearest_after"] = later[0] if later else None
        return out

    @router.get("/api/history")
    async def history(at: str = "", window_minutes: int = 0, start: str = "", end: str = "",
                      date: str = "", q: str = "", sources: str = "", limit: int = 40,
                      before: str = "", routine: bool = False, digest: bool = False,
                      kind: str = "",
                      session=Depends(core.require_session)):
        """What the logs hold for a moment, a day or a range, from every file on disk.

        at       a time as written or copied ("2026-10-02 14:33:05", "14:33", "yesterday 9pm");
                 the window_minutes either side of it (5 by default) are returned, nearest first
        date     a whole day, or "this week", "last month", "last 48 hours"; start/end a
                 range; before pages back (up to and including that second)
        q        words that must all appear; sources a comma list of system, detection,
                 devices, presence, voice (the last two need the master key)
        routine  also the presence checks that found nothing new (always, when q is given)
        kind     only these kinds of line, a comma list of alert, warning, watch, presence,
                 mode, system: the Activity card's filter
        digest   when there are more lines than the limit, a selection that says what
                 happened (the rare kinds whole) instead of the newest: what Narada reads
        """
        return await asyncio.to_thread(_history, session, at[:80], window_minutes, start[:80],
                                       end[:80], date[:40], q[:200], sources[:80], limit,
                                       before[:40], routine, digest, kind[:80])

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
