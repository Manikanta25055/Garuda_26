# Drishti AWS add-ons

Optional extras around the Pi. Detection, alerts, captures and Narada keep
running on the Pi with the internet unplugged. AWS adds:

| Piece | What it does | Runs from |
| --- | --- | --- |
| S3 + Glacier Deep Archive | Nightly offsite copy of the daily state archives, model files (`resources/`), and, if you opt in, kept or alert captures. Archived after 30 days. | `drishti-aws-backup.timer` (03:30) |
| SES | Alert, tamper and OTP emails, on a key that can only send from `EMAIL_SENDER`. Gmail SMTP is still the fallback if `EMAIL_SENDER_PASS` stays set. | `garuda_core/mailer.py` |
| SNS | The same alerts as a push to `drishti-alerts` (email, and SMS if you give a phone number). | `garuda_services/alerts.py` |
| CloudWatch | Heartbeat, service up, camera up, CPU temperature and disk use every minute; alarms when the Pi goes offline, the service is down, the camera stops, it runs hot or the disk fills. | `drishti-aws-heartbeat.timer` |
| AWS Budgets | Emails at 80% of $5 spent, or when the month is forecast to go over. | AWS |

Everything is in region **ap-south-1 (Mumbai)**. Expected cost is about $2 to $4
a month for one Pi.

## Setup

### 1. Create the AWS resources (on your Mac)

You need the AWS CLI signed in as an admin of your account (`aws configure`,
or `aws configure sso`). Then, from this repo:

```bash
bash infra/aws/deploy.sh \
  --alert-email gonugondlaveeramanikanta@gmail.com \
  --sender-email <the EMAIL_SENDER address in the Pi's .env>
# add --phone +91XXXXXXXXXX for SMS alerts
```

This creates the `drishti-addons` CloudFormation stack and writes
`drishti-aws.env` (chmod 600) holding the Pi's settings and its upload-only
key. Don't commit that file or paste it into chat.

### 2. Click the confirmation emails

AWS sends three kinds, all to addresses you own:

- **AWS Notification - Subscription Confirmation** (SNS): click *Confirm subscription*, or alarms and alerts never arrive.
- **Amazon Web Services – Email Address Verification Request** (SES), for the sender and the alert address: click both links.
- SES starts in its *sandbox*: it only sends to verified addresses, up to 200 a day. That is enough for alerts to you. To email anyone else (an OTP to another user), request production access in the SES console under *Account dashboard*.

### 3. Put the settings on the Pi

```bash
scp drishti-aws.env manikanta@<pi>:/home/manikanta/Projects/Garuda_26/
ssh manikanta@<pi>
cd ~/Projects/Garuda_26
cat drishti-aws.env >> .env && rm drishti-aws.env && chmod 600 .env
bash infra/aws/install_pi.sh
```

Then delete `drishti-aws.env` from your Mac too.

`install_pi.sh` installs boto3, enables both timers, sends one heartbeat, runs
the first backup and restarts `garuda-web`.

### 4. Check it

- `python3 scripts/aws_agent.py backup --dry-run` lists what the next backup would send.
- `journalctl -u drishti-aws-heartbeat -n 5` should say `heartbeat sent`.
- CloudWatch console → Alarms: five `drishti-*` alarms in OK after about 10 minutes.
- `/api/system/info` no longer warns that the email password is missing once SES is on.

## Turning things off

- All of it: remove `DRISHTI_AWS_REGION` from `.env` and restart `garuda-web`. The timers then exit at once.
- Email back to Gmail only: remove `GARUDA_MAIL_BACKEND=ses`.
- Remove the AWS side: delete the `drishti-addons` stack. The backup bucket is kept on purpose; empty and delete it by hand if you really mean it.

## Replacing the Pi's key

```bash
bash infra/aws/deploy.sh --alert-email ... --sender-email ... --new-key
```

This deletes the old key and writes a new `drishti-aws.env`; repeat step 3.

## Restoring

Backups are under `state/`, `models/` and `captures/` in the bucket. Anything
older than 30 days is in Deep Archive: restore it first (S3 console → object →
*Initiate restore*, Standard tier, ready in about 12 hours), then download it.
A state archive is restored the same way as a local one (see
`basic_pipelines/garuda_core/backup.py`).
