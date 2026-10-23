#!/bin/bash
# On the Pi, after the AWS settings are in .env: install boto3, the two timers,
# and prove each add-on works once.
#
#     bash infra/aws/install_pi.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$DIR")")"
cd "$PROJECT_DIR"

if ! grep -q '^DRISHTI_AWS_REGION=' .env 2>/dev/null; then
  echo "[aws] .env has no DRISHTI_AWS_REGION: append drishti-aws.env to it first (README step 3)." >&2
  exit 1
fi
chmod 600 .env

# The timers run the system python3; garuda-web runs inside setup_env.sh's venv.
python3 -c "import boto3" 2>/dev/null || sudo apt-get install -y python3-boto3
if ! bash -c 'source ./setup_env.sh >/dev/null 2>&1; python3 -c "import boto3"' 2>/dev/null; then
  bash -c 'source ./setup_env.sh >/dev/null 2>&1; python3 -m pip install "boto3>=1.26"'
fi

sudo cp "$DIR"/systemd/drishti-aws-*.service "$DIR"/systemd/drishti-aws-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now drishti-aws-heartbeat.timer drishti-aws-backup.timer

echo "[aws] heartbeat:"
python3 scripts/aws_agent.py heartbeat
echo "[aws] first backup (this can take a while for the model files):"
python3 scripts/aws_agent.py backup
echo "[aws] restarting garuda-web so alert email goes through SES and alerts reach SNS"
sudo systemctl restart garuda-web
systemctl list-timers 'drishti-aws-*' --no-pager
