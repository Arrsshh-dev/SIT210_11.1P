# SolarTrack dashboard + fault-tolerant telemetry update

This package adds separate administrator and technician dashboards, role-enforced APIs, individual user accounts, fault/recovery history, and persistent five-minute hardware-fault email alerts. It preserves the existing Isolation Forest model and its feature list. It does **not** retrain the model or add duplicate sensor samples.

## Package layout

- `SolarTrack_Dashboard_ML/app.py` — Flask app, authentication, role checks, dashboards and APIs.
- `SolarTrack_Dashboard_ML/alert_worker.py` — independent persistent alert worker; SMTP runs outside the SQLite write transaction so telemetry collection is not blocked.
- `SolarTrack_Dashboard_ML/train_model.py` — the original training script retained unchanged for reference; do not run it during this dashboard update.
- `SolarTrack_Dashboard_ML/templates/` — distinct admin and technician interfaces plus sign-in page.
- `receiver.py` — compatible with the original 13-field telemetry, an intermediate 16-field frame, and the new 17-field frame.
- `SolarTrack_Arduino_FaultTolerant_Telemetry.ino` — latest fault-tolerant Arduino sketch with the additional status telemetry and Pi heartbeat.

## Important before installation

1. Stop the existing receiver and dashboard processes.
2. Back up the database, CSV, receiver and dashboard directory. Do not delete the existing database, CSV, or model file.
3. The dashboard package ZIP does not contain the trained model. Keep your existing `solartrack_anomaly_model.joblib` in the dashboard folder. If it is stored elsewhere, set `SOLARTRACK_MODEL` to its exact path.
4. Upload the included Arduino sketch to the Nano 33 IoT. The new dashboard cannot display the Arduino's LDR and axis-specific motor fault states until this firmware is running. The existing local LDR isolation and motor-diagnostic logic is retained; the new code only transmits those statuses and responds to the Pi heartbeat.
5. The new telemetry adds columns to each existing reading row, not additional reading rows. The ML feature list remains unchanged and uses raw LDR readings (including raw `bottom`).

## Raspberry Pi installation

Assuming the existing layout is:

```text
~/SolarTrack/backend/
  receiver.py
  solartrack.db
  solartrack_readings.csv
  SolarTrack_Dashboard_ML/
```

Back up first:

```bash
cd ~/SolarTrack/backend
cp receiver.py receiver.py.before_dashboard_update.bak
cp solartrack.db solartrack.db.before_dashboard_update.bak
cp solartrack_readings.csv solartrack_readings.csv.before_dashboard_update.bak
cp -a SolarTrack_Dashboard_ML SolarTrack_Dashboard_ML.before_dashboard_update.bak
```

Copy the new `receiver.py` to `~/SolarTrack/backend/receiver.py`. Replace the dashboard app/templates and add `alert_worker.py` and `requirements.txt` inside `~/SolarTrack/backend/SolarTrack_Dashboard_ML/`. Keep the trained model file in that folder. Do not replace the existing database or CSV with files from this package.

Install dependencies in the existing virtual environment:

```bash
cd ~/SolarTrack/backend/SolarTrack_Dashboard_ML
source venv/bin/activate
pip install -r requirements.txt
```

## Environment configuration

Set a stable secret key and initial administrator credentials before the first start. Generate the secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Export the values in the shell used to start the app (or place them in a root-/service-owned environment file with restrictive permissions):

```bash
export SOLARTRACK_SECRET_KEY='PASTE_A_LONG_RANDOM_SECRET_HERE'
export SOLARTRACK_BOOTSTRAP_ADMIN_EMAIL='admin@example.com'
export SOLARTRACK_BOOTSTRAP_ADMIN_PASSWORD='USE_A_UNIQUE_PASSWORD_AT_LEAST_12_CHARACTERS'
export SOLARTRACK_HTTPS=0
```

For a `.env` file, use the same `KEY=value` lines without `export`. The receiver and dashboard load the backend-level `.env` automatically.

The initial administrator is created only when the `users` table is empty. After the first successful startup and login, remove `SOLARTRACK_BOOTSTRAP_ADMIN_PASSWORD` from the shell and delete that line from `.env` (the bootstrap account is not recreated once any user exists). Do not put secrets in source code, browser JavaScript, Git, or chat.

Configure email on the Pi (use your provider's SMTP app password, not your normal account password):

```bash
export SOLARTRACK_SMTP_HOST='smtp.example.com'
export SOLARTRACK_SMTP_PORT='587'
export SOLARTRACK_SMTP_USERNAME='solartrack@example.com'
export SOLARTRACK_SMTP_PASSWORD='PROVIDER_APP_PASSWORD'
export SOLARTRACK_SMTP_FROM='solartrack@example.com'
```

The alert worker sends to active technician accounts. To override the recipients, optionally set `SOLARTRACK_ALERT_RECIPIENTS='tech1@example.com;tech2@example.com'`. If no active technician recipient exists and no override is set, delivery is recorded as failed and retried.

Optional paths/serial settings:

```bash
export SOLARTRACK_DB="$HOME/SolarTrack/backend/solartrack.db"
export SOLARTRACK_CSV="$HOME/SolarTrack/backend/solartrack_readings.csv"
export SOLARTRACK_MODEL="$HOME/SolarTrack/backend/SolarTrack_Dashboard_ML/solartrack_anomaly_model.joblib"
export SOLARTRACK_SERIAL_PORT='/dev/serial0'
export SOLARTRACK_BAUD_RATE='9600'
```

## Start services for local testing

Use three separate terminals. Do not start duplicate receiver or alert-worker processes.

**Terminal 1 — serial receiver**

```bash
cd ~/SolarTrack/backend
source SolarTrack_Dashboard_ML/venv/bin/activate
python receiver.py
```

**Terminal 2 — dashboard**

```bash
cd ~/SolarTrack/backend/SolarTrack_Dashboard_ML
source venv/bin/activate
python app.py
```

Open `http://<raspberry-pi-ip>:5000/login` on the same trusted network for initial testing. The Flask development server is for testing only; do not expose it directly to the public internet. For a production run behind a trusted HTTPS tunnel/reverse proxy, use one Gunicorn worker initially (SQLite + one alert worker):

```bash
cd ~/SolarTrack/backend/SolarTrack_Dashboard_ML
source venv/bin/activate
export SOLARTRACK_HTTPS=1
gunicorn --workers 1 --bind 127.0.0.1:5000 app:app
```

**Terminal 3 — email alert worker**

```bash
cd ~/SolarTrack/backend/SolarTrack_Dashboard_ML
source venv/bin/activate
python alert_worker.py
```

The alert worker is a separate process so SMTP delays do not block serial data collection. It uses a Linux process lock to prevent a second local worker from running concurrently. For reliable operation after reboot, install these as supervised systemd services. Keep the receiver and alert worker running continuously.

## Access separation

- **Administrator:** system overview, account creation/activation/role changes, fault/recovery history, email attempts, recipient addresses, delivery outcomes and SMTP error details.
- **Technician:** live readings, angles, LDR fault state, separate horizontal/vertical motor suspicion, power metrics, ML anomaly status and fault history. No account management or email audit endpoints.

Authorization is enforced on the Flask backend. Hiding a section in HTML is not the security boundary. Each user has a separate account and password hash.

## Fault-alert behaviour

- LDR and motor suspicion statuses originate from the Arduino's existing fault-tolerance logic.
- Each fault has an independent persistence timer.
- A fault that recovers before five minutes does not trigger an email.
- At five minutes, all currently qualifying, not-yet-notified fault episodes are consolidated into one email.
- A continuing fault is not emailed repeatedly. Recovery closes the episode; a later recurrence starts a new timer.
- Failed SMTP attempts are recorded for administrators and retried with increasing delays.
- ML anomaly detection is informational and does not trigger hardware-fault emails.
- The timer and notification state are persisted in SQLite, so restarting the worker does not reset a fault's five-minute timer.

## Verification checklist

1. Confirm the receiver reports `LDR=...`, `H-motor=...`, `V-motor=...` and `Pi=1` after firmware upload.
2. Sign in as the bootstrap administrator; create one technician account.
3. Sign out and verify the technician sees the field operations console, not administration controls.
4. Verify a technician receives `403` from `/api/admin/notifications` and `/api/admin/users`.
5. Verify raw bottom LDR values match the Arduino ADC values; no 18.9 calibration factor is applied in the dashboard.
6. Verify the ML model loads and its prediction remains separate from hardware faults. If the model file is absent, the dashboard reports that it is unavailable; it does not silently retrain.
7. Test email with a controlled fault condition only after the system is operating safely. Confirm a fault recovering before five minutes sends no alert, and a persistent fault sends one consolidated email after five minutes.
8. Confirm that the receiver continues adding readings while the alert worker is performing SMTP delivery.

## Notes and limitations

- The motor states are **suspected**, not mechanically confirmed: this prototype infers movement failure from LDR response and has no independent shaft encoder/position feedback.
- Historical records from the older firmware have empty/default fault columns. The new firmware is required for live hardware fault visibility.
- `pi_online` becomes meaningful after the new sketch receives the receiver's `PING` heartbeat.
- Remote deployment should use HTTPS (for example, a Cloudflare Tunnel) and a production WSGI server such as Gunicorn behind the trusted tunnel/reverse proxy. Set `SOLARTRACK_HTTPS=1` when HTTPS is active. Do not expose port 5000 directly to the internet.
