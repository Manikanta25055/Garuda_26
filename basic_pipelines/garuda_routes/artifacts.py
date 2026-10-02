"""Artifacts over HTTP: the pages Narada writes, served so they can do no harm.

The page is the model's own writing. It is served with a policy that gives it
no network and no origin, for a sandboxed frame in the chat; it reaches the
house only by asking the chat page (a message), which calls /call below as the
signed-in person. So a page can do what that person's own buttons can, never a
thing that needs a confirm card, and nothing at all if nobody has it open.
"""
import collections
import hmac
import time

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# What the frame's page may do. No network at all; scripts and styles only as
# written in the page; framed by this site only; no origin of its own.
ARTIFACT_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "img-src data: blob:; media-src data: blob:; font-src data:; "
                "connect-src 'none'; form-action 'none'; base-uri 'none'; "
                "frame-ancestors 'self'; sandbox allow-scripts")

# Put in front of every page: `garuda.call` and `garuda.resize`, by message to
# the chat page, and the frame's height kept in step with its content.
BOOTSTRAP = """<script>
(function () {
  var waiting = {}, next = 1;
  function post(message) { parent.postMessage(Object.assign({garuda: true}, message), '*'); }
  function height() {
    // The page's own height, not the frame's: scrollHeight of the root is never
    // less than the frame, so a short page would keep a tall frame.
    var d = document.documentElement, b = document.body;
    return Math.ceil(Math.max(d ? d.offsetHeight : 0, b ? b.scrollHeight : 0));
  }
  function resize() { post({type: 'resize', height: height()}); }
  window.garuda = {
    call: function (name, args) {
      return new Promise(function (resolve, reject) {
        var id = next++;
        waiting[id] = [resolve, reject];
        post({type: 'call', id: id, capability: String(name), args: args || {}});
      });
    },
    resize: resize
  };
  window.addEventListener('message', function (event) {
    var m = event.data || {};
    if (event.source !== parent || !m.garuda || m.type !== 'result' || !waiting[m.id]) return;
    var pair = waiting[m.id];
    delete waiting[m.id];
    if (m.error) pair[1](new Error(m.error)); else pair[0](m.result);
  });
  window.addEventListener('load', function () {
    resize();
    if (window.ResizeObserver) new ResizeObserver(resize).observe(document.documentElement);
  });
})();
</script>"""


# What a page may change. It runs with no tap from anyone, the moment it is
# opened, so it gets the things a page of buttons is for: switching devices,
# running a scene or a shortcut, a timer. Everything else that changes the
# house (modes that silence alerts, memory, settings, emails, recordings) is
# asked of Narada in words.
PAGE_MAY_CHANGE = frozenset({"set_device", "all_off", "run_scene", "run_shortcut",
                             "cancel_shortcut", "schedule_action"})
CALLS_PER_MINUTE = 40       # for one page: a loop in it must not chatter a relay


class ArtifactCallRequest(BaseModel):
    capability: str = Field(min_length=1, max_length=64)
    args: dict = Field(default_factory=dict, max_length=16)


class ArtifactPinRequest(BaseModel):
    pinned: bool = True


def with_bootstrap(html):
    """The page with the bridge script placed first in its head."""
    lowered = html.lower()
    at = lowered.find("<head>")
    if at != -1:
        at += len("<head>")
        return html[:at] + BOOTSTRAP + html[at:]
    at = lowered.find("<html")
    if at != -1:
        at = html.find(">", at) + 1
        return html[:at] + BOOTSTRAP + html[at:]
    return "<!doctype html>" + BOOTSTRAP + html


def build_artifacts_router(core):
    router = APIRouter(prefix="/api/narada/artifacts")
    recent = collections.defaultdict(collections.deque)      # artifact id -> times of its calls

    def _mine(artifact_id, session):
        entry = core.ARTIFACTS.get(artifact_id)
        if entry is None:
            raise HTTPException(404, "No such artifact")
        if session["role"] != "admin" and entry["by"] != session["username"]:
            raise HTTPException(403, "That artifact is someone else's.")
        return entry

    @router.get("")
    async def list_artifacts(session=Depends(core.require_session)):
        admin = session["role"] == "admin"
        return {"artifacts": [a for a in core.ARTIFACTS.all()
                              if admin or a["by"] == session["username"]]}

    @router.get("/{artifact_id}/view")
    async def view_artifact(artifact_id: str, k: str = ""):
        """The page itself, for the chat's frame. No session: a frame with no
        origin sends none. The key in the address is the permission, and the
        page holds nothing but what Narada wrote for the person who has it."""
        entry = core.ARTIFACTS.get(artifact_id)
        if entry is None or not hmac.compare_digest(k.encode(), entry["key"].encode()):
            raise HTTPException(404, "No such artifact")
        html = await anyio.to_thread.run_sync(core.ARTIFACTS.html, artifact_id)
        if html is None:
            raise HTTPException(404, "No such artifact")
        return HTMLResponse(with_bootstrap(html), headers={
            "Content-Security-Policy": ARTIFACT_CSP, "X-Frame-Options": "SAMEORIGIN",
            "Cache-Control": "private, max-age=300"})

    @router.post("/{artifact_id}/call")
    async def call_from_artifact(artifact_id: str, data: ArtifactCallRequest,
                                 session=Depends(core.require_session)):
        """A page asking for a capability, relayed by the chat page as this person."""
        _mine(artifact_id, session)
        capability = core.AGENT_CAPABILITIES.get(data.capability)
        if capability is None or capability.name in core.ARTIFACT_BLOCKED:
            raise HTTPException(400, f"A page cannot use {data.capability}.")
        if capability.tier == "confirm":
            raise HTTPException(400, f"{data.capability} needs a card; ask Narada for it.")
        if capability.tier != "read" and capability.name not in PAGE_MAY_CHANGE:
            raise HTTPException(400, f"A page cannot use {data.capability}; ask Narada for it.")
        calls, now = recent[artifact_id], time.monotonic()
        while calls and now - calls[0] > 60:
            calls.popleft()
        if len(calls) >= CALLS_PER_MINUTE:
            raise HTTPException(429, "This page is asking too often. Wait a minute.")
        calls.append(now)
        out = await anyio.to_thread.run_sync(
            lambda: core.AGENT._run_tool(capability.name, dict(data.args), session["username"],
                                         session["role"]))
        out = {k: v for k, v in out.items() if not k.startswith("_")} if isinstance(out, dict) else out
        if capability.tier != "read" and "error" not in out:
            core.log_system_update(f"Artifact {artifact_id}: {session['username']} used "
                                   f"{capability.name}")
        return {"result": out}

    @router.post("/{artifact_id}/pin")
    async def pin_artifact(artifact_id: str, data: ArtifactPinRequest,
                           session=Depends(core.require_session)):
        _mine(artifact_id, session)
        return {"ok": True, "artifact": core.ARTIFACTS.pin(artifact_id, data.pinned)}

    @router.delete("/{artifact_id}")
    async def delete_artifact(artifact_id: str, session=Depends(core.require_session)):
        _mine(artifact_id, session)
        core.ARTIFACTS.delete(artifact_id)
        return {"ok": True}

    return router
