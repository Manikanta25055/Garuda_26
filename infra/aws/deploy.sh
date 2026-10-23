#!/bin/bash
# Create (or update) Drishti's AWS add-ons and write the Pi's settings file.
#
# Run on a computer whose AWS CLI is signed in as an account admin -- your Mac,
# not the Pi. The Pi only ever gets the upload-only key this writes.
#
#     bash infra/aws/deploy.sh --alert-email you@gmail.com --sender-email you@gmail.com
#     bash infra/aws/deploy.sh ... --phone +9198xxxxxxxx --budget 5
#     bash infra/aws/deploy.sh ... --new-key      # replace the Pi's key
#
# Writes ./drishti-aws.env (chmod 600). Copy it to the Pi and append it to
# .env there (infra/aws/README.md, step 3); then delete this copy.
set -euo pipefail

REGION="ap-south-1"
STACK="drishti-addons"
ALERT_EMAIL=""
SENDER_EMAIL=""
PHONE=""
BUDGET="5"
NEW_KEY=0
OUT="drishti-aws.env"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --stack) STACK="$2"; shift 2 ;;
    --alert-email) ALERT_EMAIL="$2"; shift 2 ;;
    --sender-email) SENDER_EMAIL="$2"; shift 2 ;;
    --phone) PHONE="$2"; shift 2 ;;
    --budget) BUDGET="$2"; shift 2 ;;
    --new-key) NEW_KEY=1; shift ;;
    --out) OUT="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$ALERT_EMAIL" ] || [ -z "$SENDER_EMAIL" ]; then
  echo "need --alert-email and --sender-email (the Pi's EMAIL_SENDER)" >&2
  exit 2
fi

echo "[aws] account $(aws sts get-caller-identity --query Account --output text), region $REGION"
aws cloudformation deploy --region "$REGION" --stack-name "$STACK" \
  --template-file "$DIR/drishti.yaml" --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --parameter-overrides "AlertEmail=$ALERT_EMAIL" "SenderEmail=$SENDER_EMAIL" \
                        "AlertPhone=$PHONE" "MonthlyBudgetUSD=$BUDGET"

output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
BUCKET="$(output BucketName)"
TOPIC="$(output TopicArn)"
PI_USER="$(output PiUserName)"
NS="$(output MetricNamespace)"
echo "[aws] bucket $BUCKET, topic $TOPIC, Pi user $PI_USER"

if [ -e "$OUT" ] && [ "$NEW_KEY" = 0 ]; then
  echo "[aws] $OUT already exists; not making another key (pass --new-key to replace it)."
  exit 0
fi
if [ "$NEW_KEY" = 1 ]; then
  for old in $(aws iam list-access-keys --user-name "$PI_USER" --query 'AccessKeyMetadata[].AccessKeyId' --output text); do
    echo "[aws] deleting the Pi's old key $old"
    aws iam delete-access-key --user-name "$PI_USER" --access-key-id "$old"
  done
fi
read -r KEY_ID KEY_SECRET < <(aws iam create-access-key --user-name "$PI_USER" \
  --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)

umask 077
cat > "$OUT" <<EOF

# ── AWS add-ons (infra/aws, written $(date +%Y-%m-%d)) ────────────────────────
DRISHTI_AWS_REGION=$REGION
DRISHTI_S3_BUCKET=$BUCKET
DRISHTI_SNS_TOPIC_ARN=$TOPIC
DRISHTI_CW_NAMESPACE=$NS
GARUDA_MAIL_BACKEND=ses
# DRISHTI_BACKUP_CAPTURES=1        # also back up captures marked keep or made by an alert
AWS_ACCESS_KEY_ID=$KEY_ID
AWS_SECRET_ACCESS_KEY=$KEY_SECRET
EOF
chmod 600 "$OUT"
echo "[aws] wrote $OUT. Next: infra/aws/README.md, steps 2 and 3."
