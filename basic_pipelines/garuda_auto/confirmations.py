"""Things Narada has proposed and a person has not yet agreed to.

A capability whose tier is "confirm" (deleting a device, changing who can sign
in, stopping the system) is never carried out by the model. The agent puts it
here and the chat shows it as a card; it happens only when the person who asked
taps Confirm, and then it runs as them, through the site's own endpoint. A card
nobody answers lapses.

Given a path, the waiting cards are kept in a file, so a restart of the service
inside a card's ten minutes does not take its Confirm button away. Nothing
typed on a card (a password) is ever in it: that goes straight to the endpoint.
"""
import os
import secrets
import threading
import time

from . import jsonfile

TTL_S = 600
MAX_WAITING = 20


def _words(name):
    return name.replace("_", " ")


class Confirmations:
    def __init__(self, clock=time.time, path=None):
        self._clock = clock
        self._lock = threading.Lock()
        self._path = path
        self._waiting = {}
        if path:
            for entry in jsonfile.load(path, []):
                if isinstance(entry, dict) and isinstance(entry.get("id"), str) \
                        and isinstance(entry.get("expires"), (int, float)):
                    self._waiting[entry["id"]] = entry
            with self._lock:
                self._prune(self._clock())

    def _save(self):
        if not self._path:
            return
        try:
            jsonfile.save(self._path, list(self._waiting.values()))
            os.chmod(self._path, 0o600)
        except OSError:
            pass        # the card still works until the next restart

    def add(self, capability, args, user, key="", shortcut=None):
        """Hold one action for `user`. Returns the card the chat shows.

        `shortcut` is the shortcut in words, for a card that saves one: shown in
        place of the model's JSON."""
        now = self._clock()
        entry = {"id": secrets.token_hex(6), "capability": capability.name, "args": dict(args),
                 "user": user, "key": key, "expires": now + TTL_S}
        # Kept with the action: the page asks for it again when its copy is gone
        # (a reload, a phone that dropped the tab), so the Confirm button returns.
        entry["card"] = self.card(entry, capability)
        if shortcut:
            entry["card"]["shortcut"] = shortcut
            entry["card"]["lines"] = [l for l in entry["card"]["lines"] if l["name"] != "program"]
        with self._lock:
            self._prune(now)
            while len(self._waiting) >= MAX_WAITING:
                self._waiting.pop(next(iter(self._waiting)))
            self._waiting[entry["id"]] = entry
            self._save()
        return entry["card"]

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
            entry = self._waiting.pop(action_id)
            self._save()
            return entry

    def restore(self, entry):
        """Put back an action whose confirmation was refused for a fixable reason
        (a weak password), so the card can be tried again."""
        with self._lock:
            self._waiting[entry["id"]] = entry
            self._save()

    def waiting(self, user):
        with self._lock:
            self._prune(self._clock())
            return [dict(e) for e in self._waiting.values() if e["user"] == user]

    def cards(self, user):
        """The cards still waiting for `user`, oldest first."""
        return [e["card"] for e in self.waiting(user) if e.get("card")]

    def _prune(self, now):
        gone = [i for i, e in self._waiting.items() if e["expires"] < now]
        for action_id in gone:
            del self._waiting[action_id]
        if gone:
            self._save()
