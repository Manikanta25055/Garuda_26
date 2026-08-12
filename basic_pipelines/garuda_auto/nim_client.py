"""Compile an utterance into a rule using NVIDIA NIM.

The egress boundary is enforced here. What leaves the device: the user's
transcribed words, the descriptor SCHEMA, the device list, and the utterances
of existing rules. What never leaves: frames, crops, keypoints, audio, and any
current reading of any descriptor field. A compiler does not need to know what
the room looks like right now, so it is not told.

The schema is passed in rather than imported, because the vocabulary now
depends on which devices the user has added.
"""
import json
import logging
import re

import anyio.to_thread
import requests

from .validator import validate_rule

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

_SYSTEM_PROMPT = """You compile spoken home-automation instructions into JSON rules.

Return exactly one JSON object and nothing else. No prose, no code fence.

Shape:
{"source_utterance": str,
 "when": {"all": [ {"field": str, "op": str, "value": str|number}, ... ]},
 "then": [ {"device": str, "action": str}, ... ],
 "cooldown_s": number}

Use "any" instead of "all" when the user means one condition is enough.
Only use the fields, operators, devices and actions given in the schema.
Never invent a field or a device. If the instruction cannot be expressed with
the given schema, return {"error": "<short reason>"} instead.
Durations are in seconds. "five minutes" is 300.
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class NimClient:
    def __init__(self, api_key, model, base_url=DEFAULT_BASE_URL, timeout=20):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.tokens_used = 0

    def build_request(self, utterance, existing_rules, schema):
        """Assemble the request body. Schema only -- never live values."""
        known = [r.get("source_utterance", "") for r in existing_rules][:32]
        user_content = json.dumps({
            "schema": schema.schema_for_prompt(),
            "already_known": known,
            "instruction": utterance,
        })
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "max_tokens": 512,
        }

    @staticmethod
    def _extract_json(content):
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            content = content[content.find("\n") + 1:] if "\n" in content else content
            if content.lstrip().startswith("json"):
                content = content.lstrip()[4:]
        match = _JSON_RE.search(content)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except ValueError:
            return None

    def synthesize(self, utterance, existing_rules, schema):
        """Return (rule, "") on success or (None, reason) on any failure.

        Blocking. Call synthesize_async from async code -- a 20 second timeout
        on the event loop stalls the MJPEG stream, the websocket broadcaster
        and every concurrent request for its duration.
        """
        if not self.api_key:
            return None, "no NIM API key configured"
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=self.build_request(utterance, existing_rules, schema),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            log.warning("NIM request failed: %s", exc)
            return None, f"could not reach the rule service: {type(exc).__name__}"

        self.tokens_used += int(payload.get("usage", {}).get("total_tokens", 0) or 0)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None, "malformed response from the rule service"

        parsed = self._extract_json(content)
        if parsed is None:
            return None, "could not parse a rule from the response"
        if "error" in parsed:
            return None, str(parsed["error"])

        # The model does not get to decide what the user said.
        parsed["source_utterance"] = utterance

        ok, reason = validate_rule(parsed, schema)
        if not ok:
            return None, reason
        return parsed, ""

    async def synthesize_async(self, utterance, existing_rules, schema):
        """Run the blocking call on a worker thread, off the event loop."""
        return await anyio.to_thread.run_sync(
            self.synthesize, utterance, existing_rules, schema)
