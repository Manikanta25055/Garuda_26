"""Things Narada has proposed and a person has not yet agreed to.

A capability whose tier is "confirm" (deleting a device, changing who can sign
in, stopping the system) is never carried out by the model. The agent puts it
here and the chat shows it as a card; it happens only when the person who asked
taps Confirm, and then it runs as them, through the site's own endpoint. A card
nobody answers lapses.
"""
import secrets
import threading
import time

TTL_S = 600
MAX_WAITING = 20


def _words(name):
    return name.replace("_", " ")


class Confirmations:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._waiting = {}

    def add(self, capability, args, user, key=""):
        """Hold one action for `user`. Returns the card the chat shows."""
        now = self._clock()
        entry = {"id": secrets.token_hex(6), "capability": capability.name, "args": dict(args),
                 "user": user, "key": key, "expires": now + TTL_S}
        with self._lock:
            self._prune(now)
            while len(self._waiting) >= MAX_WAITING:
                self._waiting.pop(next(iter(self._waiting)))
            self._waiting[entry["id"]] = entry
        return self.card(entry, capability)

    @staticmethod
    def card(entry, capability):
        return {"id": entry["id"], "capability": capability.name,
                "title": _words(capability.name).capitalize(),
                "about": capability.summary,
                "lines": [{"name": _words(k), "value": v} for k, v in entry["args"].items()],
                # What the person types on the card itself: it never passes through
                # the model or the conversation.
                "typed": [{"name": name, "label": _words(name).capitalize(),
                           "secret": "password" in name, "required": name in capability.required}
                          for name in capability.typed],
                "expires": entry["expires"]}

    def take(self, action_id, user):
        """The waiting action, removed, if it is this person's and still fresh."""
        with self._lock:
            self._prune(self._clock())
            entry = self._waiting.get(action_id)
            if entry is None or entry["user"] != user:
                return None
            return self._waiting.pop(action_id)

    def restore(self, entry):
        """Put back an action whose confirmation was refused for a fixable reason
        (a weak password), so the card can be tried again."""
        with self._lock:
            self._waiting[entry["id"]] = entry

    def waiting(self, user):
        with self._lock:
            self._prune(self._clock())
            return [dict(e) for e in self._waiting.values() if e["user"] == user]

    def _prune(self, now):
        for action_id in [i for i, e in self._waiting.items() if e["expires"] < now]:
            del self._waiting[action_id]
