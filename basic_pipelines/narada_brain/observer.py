"""Learning from what the household does, not only from what it says.

The house already notices routines: garuda_auto/habits.py counts how devices
are switched by hand and, when the lamp goes on around 19:05 on most weekdays,
offers "do it automatically?" on the Automations page. This connects that to
Narada in three ways, none of which involve a model:

  sync()     each routine the house has noticed becomes a fact in memory
             ("The Lamp is usually turned on by hand around 19:05 on
             weekdays."), kept in step as routines appear and fade. So Narada
             can answer "when do I usually turn the lamp on?" and use it.
  offer()    one routine at a time is offered in the conversation, as a chip
             with Yes and No. Rarely: an assistant that keeps interrupting
             gets ignored, and an ignored offer teaches people to dismiss
             them unread.
  decided()  the answer is remembered as the household's choice, whichever
             page it was given on, so the same question is not asked again
             and Narada knows which way they like it.

Detection and wording both come from the actuation log, so there is nothing
to hallucinate: a routine is a count, and its sentence is a template.
"""
import logging
import time

from ..garuda_auto import jsonfile

log = logging.getLogger(__name__)

OFFERS_FILE = "narada_offers.json"
# How often the house's routines are recomputed for Narada (it reads the log).
REFRESH_S = 600
# At most one offer in this long, whatever there is to offer ...
OFFER_GAP_S = 6 * 3600
# ... and the same routine is not offered again for this long if left unanswered.
REOFFER_S = 3 * 86400


def _when(days):
    return "on weekdays" if list(days) == [0, 1, 2, 3, 4] else "every day"


def habit_key(suggestion):
    return f"habit:{suggestion['device']}:{suggestion['action']}"


def choice_key(suggestion):
    return f"choice:{suggestion['device']}:{suggestion['action']}"


def habit_text(suggestion, name):
    return (f"The {name} is usually turned {suggestion['action']} by hand around "
            f"{suggestion['time']} {_when(suggestion['days'])}.")


def choice_text(suggestion, name, accepted):
    if accepted:
        return (f"The household chose to have the {name} turned {suggestion['action']} "
                f"automatically at {suggestion['time']} {_when(suggestion['days'])}.")
    return (f"The household prefers to turn the {name} {suggestion['action']} by hand around "
            f"{suggestion['time']}, not automatically.")


class Observer:
    def __init__(self, memory, path=None, *, clock=time.time):
        self.memory = memory
        self.path = path
        self._clock = clock
        state = jsonfile.load(path, {}) if path else {}
        self._offered = state.get("offered", {}) if isinstance(state.get("offered"), dict) else {}
        self._last_offer = float(state.get("last_offer") or 0)

    def sync(self, suggestions, names):
        """Bring the remembered routines in step with what the house sees now.

        Returns the memory outcomes that changed something (for the chips).
        A routine that has faded, or that a schedule now covers, is archived:
        it is no longer something the household does by hand.
        """
        changed, live = [], set()
        for s in suggestions:
            name = names.get(s["device"])
            if not name:
                continue
            key = habit_key(s)
            live.add(key)
            # Once they have answered, their choice is the fact; the bare routine is not repeated.
            if self.memory.by_key(choice_key(s)):
                continue
            outcome = self.memory.remember(habit_text(s, name), category="routine",
                                           origin="observed", key=key)
            if outcome["status"] in ("saved", "updated"):
                changed.append(outcome)
        for fact in self.memory.facts():
            key = fact.get("key", "")
            if key.startswith("habit:") and key not in live:
                self.memory.archive_key(key, "no longer observed")
        return changed

    def offer(self, suggestions):
        """One routine to offer now, or None. Marks it as offered."""
        now = self._clock()
        if now - self._last_offer < OFFER_GAP_S:
            return None
        for s in suggestions:                      # strongest first (habits.suggest sorts them)
            if now - float(self._offered.get(s["id"], 0)) < REOFFER_S:
                continue
            self._offered[s["id"]] = now
            self._last_offer = now
            self._save()
            return {"id": s["id"], "text": s["text"], "device": s["device"],
                    "action": s["action"], "time": s["time"], "days": list(s["days"])}
        return None

    def decided(self, suggestion, name, accepted, by=""):
        """Remember the answer to an offered routine. Returns the memory outcome."""
        self.memory.archive_key(habit_key(suggestion), "answered")
        outcome = self.memory.remember(choice_text(suggestion, name, accepted), category="routine",
                                       origin="choice", by=by, key=choice_key(suggestion))
        self._offered[suggestion["id"]] = self._clock()
        self._save()
        return outcome

    def _save(self):
        if not self.path:
            return
        # Ids of routines that no longer exist are of no further use.
        recent = {k: v for k, v in self._offered.items() if self._clock() - v < 60 * 86400}
        self._offered = recent
        try:
            jsonfile.save(self.path, {"offered": recent, "last_offer": self._last_offer})
        except OSError:
            log.exception("saving offers")
