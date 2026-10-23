#!/usr/bin/env python3
"""The Pi's side of the optional AWS add-ons, run by two systemd timers.

    python3 scripts/aws_agent.py heartbeat   # every minute: health to CloudWatch
    python3 scripts/aws_agent.py backup      # nightly: changed files to S3
    python3 scripts/aws_agent.py backup --dry-run

heartbeat sends Heartbeat=1 (the Pi is up and online), ServiceUp (garuda-web
answers /api/health), CpuTempC and DiskUsedPercent. The alarms in
infra/aws/drishti.yaml email when Heartbeat stops arriving: the one failure
the Pi cannot report about itself.

backup uploads what cannot be recreated: the daily state archives the server
already writes (system_logs/backups/auto), the model files in resources/, and,
only with DRISHTI_BACKUP_CAPTURES=1, captures marked keep or made by an alert.
A file is sent again only when its size or time changed (system_logs/
aws_backup_manifest.json). The bucket keeps every version, and the Pi's key
cannot read or delete any of them.

Both read the AWS settings from .env (see basic_pipelines/garuda_core/cloud.py).
With no network they log the failure and exit 0; the next run catches up.
"""
import argparse
import json
import logging
import os
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from basic_pipelines.garuda_core import cloud                      # noqa: E402
from basic_pipelines.garuda_core.storage import _atomic_json_write  # noqa: E402

log = logging.getLogger("aws_agent")

MODEL_EXTS = {".hef", ".pt", ".onnx", ".json", ".so"}
MANIFEST = "aws_backup_manifest.json"


def _load_env():
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def data_dir():
    return Path(os.environ.get("DRISHTI_DATA_DIR") or ROOT / "basic_pipelines" / "system_logs")


# ── heartbeat ────────────────────────────────────────────────────────────────

def cpu_temp_c(path="/sys/class/thermal/thermal_zone0/temp"):
    try:
        return round(int(Path(path).read_text().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def service_up(port=None, timeout=5):
    port = port or os.environ.get("GARUDA_PORT", "8080")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return 1 if r.status == 200 and json.load(r).get("status") == "ok" else 0
    except Exception:
        return 0


def disk_used_percent(path="/"):
    try:
        u = shutil.disk_usage(path)
        return round(100.0 * u.used / u.total, 1)
    except OSError:
        return None


def heartbeat():
    values = {"Heartbeat": 1, "ServiceUp": service_up(), "CpuTempC": cpu_temp_c(),
              "DiskUsedPercent": disk_used_percent()}
    try:
        cloud.put_metrics(values)
    except Exception as exc:
        log.warning("heartbeat not sent: %s", exc)
        return values, False
    return values, True


# ── backup ───────────────────────────────────────────────────────────────────

def backup_candidates(root=ROOT, data=None, captures=None):
    """[(path, key)] of everything worth keeping offsite."""
    data = Path(data or data_dir())
    out = []
    auto = data / "backups" / "auto"
    if auto.is_dir():
        out += [(p, f"state/{p.name}") for p in sorted(auto.glob("garuda-state-*.tar.gz"))]
    res = Path(root) / "resources"
    if res.is_dir():
        out += [(p, f"models/{p.relative_to(res).as_posix()}") for p in sorted(res.rglob("*"))
                if p.is_file() and p.suffix.lower() in MODEL_EXTS]
    if captures is None:
        captures = os.environ.get("DRISHTI_BACKUP_CAPTURES", "") == "1"
    if captures:
        cap = data / "captures"
        try:
            items = json.loads((cap / "index.json").read_text())
        except (OSError, ValueError):
            items = []
        for it in items:
            if it.get("status") == "saved" and (it.get("keep") or it.get("alert")) and it.get("file"):
                p = cap / Path(it["file"]).name
                if p.is_file():
                    out.append((p, f"captures/{p.name}"))
    return out


def _stamp(path):
    st = path.stat()
    return [st.st_size, int(st.st_mtime)]


def backup(dry_run=False, data=None, root=ROOT, captures=None):
    """Upload what changed since the last run. Returns (sent, skipped, failed)."""
    data = Path(data or data_dir())
    mpath = data / MANIFEST
    try:
        manifest = json.loads(mpath.read_text())
    except (OSError, ValueError):
        manifest = {}
    sent, skipped, failed = [], [], []
    for path, key in backup_candidates(root, data, captures):
        try:
            stamp = _stamp(path)
        except OSError:
            continue
        if manifest.get(key) == stamp:
            skipped.append(key)
            continue
        if dry_run:
            sent.append(key)
            continue
        try:
            cloud.upload_file(path, key)
        except Exception as exc:
            log.warning("backup of %s failed: %s", key, exc)
            failed.append(key)
            if isinstance(exc, cloud.CloudOff):
                break
            continue
        manifest[key] = stamp
        sent.append(key)
        _atomic_json_write(str(mpath), manifest)     # a cut run keeps what it sent
    return sent, skipped, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("heartbeat", "backup"))
    ap.add_argument("--dry-run", action="store_true", help="backup: list what would be sent")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _load_env()
    if not cloud.enabled():
        log.info("DRISHTI_AWS_REGION is not set; the AWS add-ons are off")
        return 0
    if args.command == "heartbeat":
        values, ok = heartbeat()
        log.info("heartbeat %s: %s", "sent" if ok else "NOT sent", values)
    else:
        sent, skipped, failed = backup(dry_run=args.dry_run)
        verb = "would send" if args.dry_run else "sent"
        log.info("backup: %s %d, unchanged %d, failed %d", verb, len(sent), len(skipped), len(failed))
        for key in sent:
            log.info("  %s %s", verb, key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
