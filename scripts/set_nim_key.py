#!/usr/bin/env python3
"""Install an NVIDIA NIM API key (and optionally a model) into .env.

    python3 scripts/set_nim_key.py                      # key from nvidia_nim_api.txt
    python3 scripts/set_nim_key.py path/to/key.txt --model openai/gpt-oss-20b

The key is checked against the live endpoint before anything is written, so a
typo cannot replace a working key with a broken one. The key is never printed.
garuda-web reads .env at start-up: restart the service afterwards.
"""
import argparse
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "basic_pipelines"))
from garuda_auto.envfile import set_vars  # noqa: E402

ENV_PATH = ROOT / ".env"
BASE_URL = "https://integrate.api.nvidia.com/v1"
KEY_RE = re.compile(r"nvapi-[A-Za-z0-9_-]{20,}")
DEFAULT_MODEL = "openai/gpt-oss-20b"
DEFAULT_FALLBACKS = "deepseek-ai/deepseek-v4.1-flash"


def read_key(path):
    keys = KEY_RE.findall(Path(path).read_text())
    if len(keys) != 1:
        sys.exit(f"expected exactly one nvapi- key in {path}, found {len(keys)}")
    return keys[0]


def check(key, model):
    resp = requests.post(f"{BASE_URL}/chat/completions",
                         headers={"Authorization": f"Bearer {key}"},
                         json={"model": model, "max_tokens": 8,
                               "messages": [{"role": "user", "content": "ping"}]},
                         timeout=60)
    if resp.status_code != 200:
        sys.exit(f"key check failed with {model}: HTTP {resp.status_code} "
                 f"{resp.text[:160]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("key_file", nargs="?", default=str(ROOT / "nvidia_nim_api.txt"))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--fallbacks", default=DEFAULT_FALLBACKS,
                    help="comma-separated models tried when the first is retired or down")
    ap.add_argument("--no-check", action="store_true", help="skip the live test call")
    args = ap.parse_args()

    key = read_key(args.key_file)
    if not args.no_check:
        check(key, args.model)
    set_vars(ENV_PATH, {"NIM_API_KEY": key, "NIM_MODEL": args.model,
                        "NIM_FALLBACK_MODELS": args.fallbacks})
    print(f"NIM key ...{key[-4:]} and model {args.model} written to {ENV_PATH}.")
    print("Restart garuda-web for the running service to pick it up.")


if __name__ == "__main__":
    main()
