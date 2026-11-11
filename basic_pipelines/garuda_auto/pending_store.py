"""Rules awaiting the user's confirmation.

A synthesised rule is not yet knowledge. The model can return something that
passes every validation check and still means the opposite of what was asked,
so nothing enters the rule base until a person agrees it is right.

Separate file from the rule store: a proposal is not something the house knows.
"""
import threading
import time
import uuid

from . import jsonfile

MAX_PENDING = 8
TTL_S = 900


class PendingStore:
    def __init__(self, path, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._items = []
        self.load()

    def load(self):
        # Only well-formed proposals are kept: one entry without a time made
        # every later read of the store raise.
        self._items = [
            item for item in jsonfile.load(self.path, [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
            and isinstance(item.get("rule"), dict)
            and isinstance(item.get("created_at"), (int, float))
            and not isinstance(item.get("created_at"), bool)
        ]

    def save(self):
        jsonfile.save(self.path, self._items)

    def _drop_expired(self):
        cutoff = self._clock() - TTL_S
        before = len(self._items)
        self._items = [i for i in self._items if i["created_at"] >= cutoff]
        return before - len(self._items)

    def add(self, rule, conflict=None):
        with self._lock:
            self._drop_expired()
            proposal_id = uuid.uuid4().hex[:12]
            self._items.append({
                "id": proposal_id,
                "rule": rule,
                "conflict": conflict,
                "created_at": self._clock(),
            })
            if len(self._items) > MAX_PENDING:
                self._items = self._items[-MAX_PENDING:]
            self.save()
        return proposal_id

    def get(self, proposal_id):
        with self._lock:
            self._drop_expired()
            for item in self._items:
                if item["id"] == proposal_id:
                    return item
        return None

    def pop(self, proposal_id):
        with self._lock:
            self._drop_expired()
            for index, item in enumerate(self._items):
                if item["id"] == proposal_id:
                    self._items.pop(index)
                    self.save()
                    return item
        return None

    def purge(self):
        with self._lock:
            dropped = self._drop_expired()
            if dropped:
                self.save()
        return dropped

    def all(self):
        with self._lock:
            self._drop_expired()
            return list(self._items)
