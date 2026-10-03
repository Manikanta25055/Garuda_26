"""Rules awaiting the user's confirmation.

A synthesised rule is not yet knowledge. The model can return something that
passes every validation check and still means the opposite of what was asked,
so nothing enters the rule base until a person agrees it is right.

Separate file from the rule store: a proposal is not something the house knows.
"""
import json
import os
import tempfile
import threading
import time
import uuid

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
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._items = data if isinstance(data, list) else []
        except (OSError, ValueError):
            self._items = []

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._items, fh, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

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
