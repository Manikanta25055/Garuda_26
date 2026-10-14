"""Is every fix actually live? Asks the running service, not the files.

    python3 scripts/check_deploy.py                       # this Pi, and the public site
    python3 scripts/check_deploy.py --public ''           # this Pi only
    python3 scripts/check_deploy.py --local http://127.0.0.1:8099 --public ''

scripts/deploy_garuda.sh runs it after a restart. Each fix is checked the way
a visitor meets it: the headers and pages the service sends, the routes it
answers, the files it serves (through the tunnel too, so a cache in between
cannot hide an old version). Then the things only the owner can do, which no
deploy can: credentials published before, accounts made for testing.

Exit status 0 when every check passes; warnings about the owner's to-do list
do not fail it. Standard library only: it runs on the Pi as it is.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results = {"PASS": 0, "FAIL": 0, "WARN": 0}


def say(kind, what, detail=""):
    results[kind] += 1
    print(f"  {kind:<4}  {what}" + (f"  ({detail})" if detail else ""))


# Cloudflare refuses Python's own user agent ("Python-urllib/3.x") with a 403
# before the request reaches the tunnel, so the public checks name themselves.
USER_AGENT = "garuda-check-deploy/1 (+curl-compatible)"


def fetch(url, method="GET", body=None, headers=None, timeout=15):
    """(status, headers, text); status 0 when nothing answered."""
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")
    except Exception as e:                               # refused, timed out, TLS
        return 0, {}, str(e)


def git(*args):
    try:
        return subprocess.check_output(["git", "-C", ROOT, *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def check_site(base, label, want_commit):
    print(f"\n{label}: {base}")
    status, headers, text = fetch(base + "/api/health")
    if status != 200:
        say("FAIL", "the service answers", f"/api/health gave {status or text[:80]}")
        return
    live = json.loads(text).get("commit", "?")
    if want_commit and live[:7] != want_commit[:7]:
        say("FAIL", "runs the deployed commit", f"live {live}, checkout {want_commit}")
    else:
        say("PASS", "runs the deployed commit", live)

    status, headers, page = fetch(base + "/")
    csp = headers.get("Content-Security-Policy", "") if headers else ""
    script_src = next((d.strip() for d in csp.split(";") if d.strip().startswith("script-src")), "")
    if script_src and "'unsafe-inline'" not in script_src and "data:" not in script_src:
        say("PASS", "no inline script allowed (Content-Security-Policy)", script_src)
    else:
        say("FAIL", "no inline script allowed (Content-Security-Policy)", script_src or "no policy header")
    handlers = re.findall(r"<[^>]*\son[a-z]+\s*=\s*[\"']", page)
    say("PASS" if "/static/actions.js" in page and not handlers else "FAIL",
        "page has no inline handlers and loads actions.js",
        f"{len(handlers)} inline handler(s)" if handlers else "")

    assets = {name: fetch(base + "/static/" + name)[2] for name in ("app.js", "island.js", "style.css", "home.js")}
    for name, needle, what in [
        ("app.js", "_heatmapFit", "Alert Activity fills a phone's card"),
        ("app.js", "G.closeFeedback", "feedback: Write tab for admins, close button"),
        ("app.js", "_refreshAccess", "sign-in tokens kept out of localStorage"),
        ("app.js", "const macSafari", "Chrome/Brave on a Mac get the light rendering"),
        ("island.js", "placeShadow", "Dynamic Island animates without re-blurring"),
        ("style.css", "calc(100dvh - (88px", "Narada's facts panel is never cut off at the top"),
        ("style.css", "nx-progress 1.5s", "no animation left running when hidden"),
        ("home.js", "const handler = (type, fn", "home page buttons carry no script"),
    ]:
        say("PASS" if needle in assets[name] else "FAIL", what, f"/static/{name}")

    status, _, _ = fetch(base + "/api/forgot/reset", "POST",
                         json.dumps({"otp": "000000", "new_password": "Xx123456"}).encode(),
                         {"Content-Type": "application/json"})
    say("PASS" if status == 422 else "FAIL", "a password reset must name the account", f"got {status}")
    big = json.dumps({"message": "x" * 1_100_000}).encode()
    status, _, _ = fetch(base + "/api/feedback", "POST", big, {"Content-Type": "application/json"})
    say("PASS" if status == 413 else "FAIL", "request bodies over 1 MB are refused", f"got {status}")
    status, _, _ = fetch(base + "/api/refresh/revoke", "POST", b"", {})
    say("PASS" if status == 200 else "FAIL", "signing out ends the refresh token too", f"got {status}")


def check_checkout():
    print(f"\nThis checkout: {ROOT}")
    def has(path, needle):
        try:
            return needle in open(os.path.join(ROOT, path), encoding="utf-8").read()
        except OSError:
            return False
    for path, needle, what in [
        ("basic_pipelines/garuda_auto/switch_guard.py", "class SwitchGuard", "relays: the switch guard"),
        ("basic_pipelines/garuda_auto/transports.py", "self.guard.check", "relays: every switch goes through it"),
        ("basic_pipelines/garuda_routes/control.py", "if _HEARTBEAT_KEY and hmac", "the dead-man switch needs its key"),
        ("basic_pipelines/garuda_services/home.py", 'return "user"', "Drishti sign-in never makes an admin"),
        ("basic_pipelines/garuda_auto/validator.py", "def rule_from_model", "rules keep only their own fields"),
    ]:
        say("PASS" if has(path, needle) else "FAIL", what)
    for path, needle, what in [
        ("README.md", "rps_0OS1", "RunPod key out of the README"),
        ("README.md", "Credentails - user", "demo sign-in out of the README"),
        ("scripts/garuda-monitor.service", "GARUDA_MON_PASS=user", "monitor password out of its unit file"),
    ]:
        say("FAIL" if has(path, needle) else "PASS", what)


def verify_password(pw, stored):
    if stored.startswith("pbkdf2:"):
        try:
            _, algo, iters, salt, dk = stored.split(":")
            got = hashlib.pbkdf2_hmac(algo, pw.encode(), bytes.fromhex(salt), int(iters)).hex()
            return hmac.compare_digest(got, dk)
        except ValueError:
            return False
    return hmac.compare_digest(pw.encode(), stored.encode())


def check_owner_todo():
    print("\nOnly you can do these (no deploy can):")
    users_file = os.path.join(ROOT, "basic_pipelines", "system_logs", "users.json")
    try:
        users = json.load(open(users_file, encoding="utf-8"))
    except (OSError, ValueError):
        users = None
    if users is None:
        say("WARN", "accounts not checked", f"cannot read {users_file}")
    else:
        if "svc_eval" in users:
            say("WARN", "delete the svc_eval admin account: its password was published")
        else:
            say("PASS", "no svc_eval test account")
        weak = [u for u, rec in users.items()
                if isinstance(rec, dict) and verify_password(u, str(rec.get("password", "")))]
        if weak:
            say("WARN", "change these passwords: each is the same as its username", ", ".join(weak))
        else:
            say("PASS", "no account's password is its own username")
    env = {}
    try:
        for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    if env.get("DEADMAN_ENABLED") == "1" and not env.get("HEARTBEAT_KEY"):
        say("WARN", "set HEARTBEAT_KEY in .env: with DEADMAN_ENABLED=1 the switch now keeps alerting without it")
    unit = "/etc/systemd/system/garuda-monitor.service"
    if os.path.exists(unit) and "GARUDA_MON_PASS=user" in open(unit, encoding="utf-8").read():
        say("WARN", "the installed monitor unit still holds a password", unit)
    say("WARN", "revoke the RunPod S3 key that was in the README (it stays in git history)",
        "cannot be checked from here")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", default=f"http://127.0.0.1:{os.environ.get('GARUDA_PORT', '8080')}")
    ap.add_argument("--public", default="https://garuda.veeramanikanta.in")
    ap.add_argument("--commit", default=git("rev-parse", "--short", "HEAD"))
    args = ap.parse_args()
    check_site(args.local, "The service on this Pi", args.commit)
    if args.public:
        check_site(args.public.rstrip("/"), "The public site (through the tunnel)", args.commit)
    check_checkout()
    check_owner_todo()
    print(f"\n{results['PASS']} passed, {results['FAIL']} failed, {results['WARN']} for you to do")
    return 1 if results["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
