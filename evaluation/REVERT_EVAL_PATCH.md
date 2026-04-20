# Revert P1-4 Evaluation Test Scaffolding

Run these steps after the 36 h eval completes (or if aborted). All changes below
are test-only and must be reverted before the system is considered production-clean.

## 1. Revert `Garuda_web.py` admin-OTP bypass

File: `basic_pipelines/Garuda_web.py`, around line 2442 inside the `/api/login` handler.

Replace this block:

```python
if u in USERS and USERS[u].get("role") == "admin":
    # Test-only bypass for the P1-4 evaluation harness. Set GARUDA_EVAL_OTP_BYPASS=1
    # in the environment before starting the server to allow a named service admin
    # (GARUDA_EVAL_SERVICE_ADMIN) to sign in via /api/login without the email OTP.
    _bypass = os.environ.get("GARUDA_EVAL_OTP_BYPASS", "") == "1"
    _allowed = os.environ.get("GARUDA_EVAL_SERVICE_ADMIN", "")
    if not (_bypass and _allowed and u == _allowed):
        raise HTTPException(403, "Admin accounts must sign in via the Admin Access flow.")
```

with the original:

```python
if u in USERS and USERS[u].get("role") == "admin":
    raise HTTPException(403, "Admin accounts must sign in via the Admin Access flow.")
```

## 2. Remove env-var exports from `start_garuda.sh`

File: `/home/manikanta/start_garuda.sh`

Delete these three lines (inserted right before the `python3 basic_pipelines/Garuda_web.py ...` invocation):

```bash
# P1-4 evaluation harness: test-only admin OTP bypass (reversible)
export GARUDA_EVAL_OTP_BYPASS=1
export GARUDA_EVAL_SERVICE_ADMIN=svc_eval
```

## 3. Remove the `svc_eval` service admin

File: `basic_pipelines/system_logs/users.json`

Delete the `"svc_eval": { ... }` entry (role=admin, display_name=Eval Service).
The pre-eval backup is at `basic_pipelines/system_logs/users.json.bak_eval`.

Quick restore:

```bash
cp basic_pipelines/system_logs/users.json.bak_eval basic_pipelines/system_logs/users.json
```

## 4. Restart Garuda

```bash
sudo systemctl restart garuda.service
systemctl status garuda.service
```

## 5. Verify revert

```bash
# svc_eval must no longer exist (or its login must fail even with bypass env gone)
curl -s -X POST http://127.0.0.1:8080/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"svc_eval","password":"eval_admin_X92!"}'
# expect: 401 or 403 with "Admin accounts must sign in via the Admin Access flow."

# existing admin still needs OTP
curl -s -X POST http://127.0.0.1:8080/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"root"}'
# expect: 403 "Admin accounts must sign in via the Admin Access flow."
```

## 6. Optional cleanup

- Remove backup once revert is confirmed:
  `rm basic_pipelines/system_logs/users.json.bak_eval`
- Eval output under `evaluation/out/<run_id>/` is safe to keep; it's the
  dataset for the IEEE Access paper "System-Level Evaluation" subsection.
- `evaluation/eval_36h.py` and `evaluation/voice_ground_truth.json` can stay
  in the repo as reproducibility artifacts.

## Credentials (for reference only; should be invalidated after revert)

- Service admin username: `svc_eval`
- Service admin password: `eval_admin_X92!`
- PBKDF2 hash stored: `pbkdf2:sha256:600000:1647ae03d8538cf1fa7bcf3d1022a396:f5982c498bb5c6839af4337e62a2530a3e2b3a24898c4d8c13150d214a6888cf`
