"""One way to talk to NVIDIA NIM, shared by everything that needs a model.

NIM retires hosted models on a schedule. The model this project shipped with
reached end of life on 2026-09-01 and every request after that came back 410,
so rule synthesis stopped working without anything on the screen saying why.
A model id is therefore a preference, not a dependency: `chat()` walks an
ordered list and moves on when a model is gone, missing or overloaded, and it
remembers which one answered so the settings page can show it.

Blocking by design. Callers on the event loop go through `anyio.to_thread`.
"""
import logging
import threading
import time

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Measured 2026-10-01 with two-action home commands ("hall light on and
# privacy off"), 8 runs each: nemotron-3-super got every tool call right in
# ~2-3 s; glm-5.3-flash also 8/8 but slower; gpt-oss-20b (the 2026-09 pick)
# dropped one of the two actions every time. Kimi K3, GLM 5.3 and Gemma 4
# timed out on this account.
DEFAULT_MODELS = ("nvidia/nemotron-3-super-120b-a12b", "z-ai/glm-5.3-flash",
                  "openai/gpt-oss-20b")

# Worth trying the next model for. A 400 or 401 is our fault or the key's and
# would fail the same way on every model.
_SKIP_STATUS = frozenset({404, 408, 409, 410, 429, 500, 502, 503, 504})


class NimUnavailable(Exception):
    """No model produced an answer. The message is safe to show a user."""


def parse_models(primary, fallbacks=""):
    """Primary first, then fallbacks, then defaults; no duplicates, no blanks."""
    out = []
    for item in [primary, *str(fallbacks or "").split(","), *DEFAULT_MODELS]:
        item = (item or "").strip()
        if item and item not in out:
            out.append(item)
    return out


class NimChat:
    def __init__(self, api_key="", models=DEFAULT_MODELS, base_url=DEFAULT_BASE_URL,
                 timeout=30, post=None, clock=time.time):
        self.api_key = api_key or ""
        self.models = list(models) or list(DEFAULT_MODELS)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._post = post or requests.post
        self._clock = clock
        self._lock = threading.Lock()
        self.tokens_used = 0
        self.calls = 0
        self.last_model = ""
        self.last_error = ""
        self.last_latency_s = None
        # A model that just 410'd is not retried on every request.
        self._dead_until = {}

    @property
    def configured(self):
        return bool(self.api_key)

    def configure(self, api_key=None, models=None):
        with self._lock:
            if api_key is not None:
                self.api_key = api_key.strip()
            if models:
                self.models = list(models)
            self._dead_until.clear()

    def _candidates(self):
        now = self._clock()
        alive = [m for m in self.models if self._dead_until.get(m, 0) <= now]
        return alive or list(self.models)

    def chat(self, messages, *, tools=None, max_tokens=1024, temperature=0.2,
             timeout=None):
        """Return the first choice's message dict. Raises NimUnavailable."""
        if not self.api_key:
            raise NimUnavailable("no NVIDIA NIM API key is configured")
        last = "no model answered"
        for model in self._candidates():
            body = {"model": model, "messages": messages,
                    "max_tokens": max_tokens, "temperature": temperature}
            if tools:
                body["tools"] = tools
                body["tool_choice"] = "auto"
            started = self._clock()
            try:
                resp = self._post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json=body, timeout=timeout or self.timeout)
            except requests.exceptions.RequestException as exc:
                last = f"{model}: {type(exc).__name__}"
                log.warning("NIM %s failed: %s", model, exc)
                continue
            status = getattr(resp, "status_code", 200)
            if status in _SKIP_STATUS:
                last = f"{model}: HTTP {status}"
                if status in (404, 410):
                    self._dead_until[model] = self._clock() + 3600
                continue
            if status >= 400:
                self.last_error = f"HTTP {status}"
                raise NimUnavailable(
                    "the NIM key was rejected" if status in (401, 403)
                    else f"the model service refused the request (HTTP {status})")
            try:
                payload = resp.json()
                message = payload["choices"][0]["message"]
            except (ValueError, KeyError, IndexError, TypeError):
                last = f"{model}: malformed response"
                continue
            with self._lock:
                self.calls += 1
                self.tokens_used += int((payload.get("usage") or {}).get("total_tokens") or 0)
                self.last_model = model
                self.last_error = ""
                self.last_latency_s = round(self._clock() - started, 2)
            message = dict(message)
            message["_finish_reason"] = payload["choices"][0].get("finish_reason")
            return message
        self.last_error = last
        raise NimUnavailable(f"the model service is unavailable ({last})")

    def status(self):
        return {
            "configured": self.configured,
            "models": list(self.models),
            "last_model": self.last_model,
            "last_error": self.last_error,
            "last_latency_s": self.last_latency_s,
            "calls": self.calls,
            "tokens_used": self.tokens_used,
        }
