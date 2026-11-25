from __future__ import annotations
import os, sqlite3, secrets
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone
from contextlib import contextmanager

import joblib
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')
load_dotenv(BASE_DIR.parent / '.env')
DB_PATH = Path(os.getenv('SOLARTRACK_DB', str(BASE_DIR.parent / 'solartrack.db')))
if not DB_PATH.exists() and (BASE_DIR / 'solartrack.db').exists():
    DB_PATH = BASE_DIR / 'solartrack.db'
MODEL_PATH = Path(os.getenv('SOLARTRACK_MODEL', str(BASE_DIR / 'solartrack_anomaly_model.joblib')))
CSV_PATH = Path(os.getenv('SOLARTRACK_CSV', str(DB_PATH.parent / 'solartrack_readings.csv')))
FEATURES = ["top", "bottom", "left", "right", "vertical_error", "horizontal_error",
            "mean_ldr", "ldr_range", "load_voltage_V", "load_current_mA", "load_power_mW"]

try:
    MODEL_BUNDLE = joblib.load(MODEL_PATH) if MODEL_PATH.exists() else None
except Exception:
    MODEL_BUNDLE = None

app = Flask(__name__)
app.secret_key = os.getenv('SOLARTRACK_SECRET_KEY') or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.getenv('SOLARTRACK_HTTPS', '0') == '1',
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 8)


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL, password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','technician')),
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)''')
        con.execute('''CREATE TABLE IF NOT EXISTS fault_episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fault_code TEXT NOT NULL,
            started_at TEXT NOT NULL, recovered_at TEXT, notified_at TEXT)''')
        con.execute('''CREATE TABLE IF NOT EXISTS email_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, attempted_at TEXT NOT NULL,
            sent_at TEXT, fault_codes TEXT NOT NULL, recipients TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1, error TEXT)''')
        con.execute('''CREATE TABLE IF NOT EXISTS fault_state (
            fault_code TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 0,
            first_seen_at TEXT, notified_at TEXT, last_seen_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT)''')
        cols = {r['name'] for r in con.execute('PRAGMA table_info(readings)')}
        # Migrate existing table in place; no extra sensor-reading rows are created.
        if cols:
            for name, sql in {
                'ldr_fault': "TEXT DEFAULT ''",
                'motor_fault': 'INTEGER DEFAULT 0',
                'horizontal_motor_suspected': 'INTEGER DEFAULT 0',
                'vertical_motor_suspected': 'INTEGER DEFAULT 0',
                'pi_online': 'INTEGER DEFAULT 0',
            }.items():
                if name not in cols:
                    con.execute(f'ALTER TABLE readings ADD COLUMN {name} {sql}')
        # Bootstrap the first administrator only when both variables are supplied.
        email = os.getenv('SOLARTRACK_BOOTSTRAP_ADMIN_EMAIL', '').strip().lower()
        password = os.getenv('SOLARTRACK_BOOTSTRAP_ADMIN_PASSWORD', '')
        if email and password and not con.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            con.execute('INSERT INTO users(email,full_name,password_hash,role,created_at) VALUES(?,?,?,?,?)',
                        (email, 'SolarTrack Administrator', generate_password_hash(password), 'admin', utcnow()))


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def current_user():
    return session.get('user')


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user():
            if request.path.startswith('/api/'):
                return jsonify(error='Authentication required'), 401
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapped


def role_required(role):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user().get('role') != role:
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return deco


def csrf_ok():
    token = session.get('csrf_token')
    supplied = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return bool(token and supplied and secrets.compare_digest(token, supplied))


@app.before_request
def enforce_csrf():
    if request.method in {'POST','PUT','PATCH','DELETE'} and request.endpoint not in (None, 'login'):
        if not csrf_ok():
            abort(400, 'Invalid or missing CSRF token')


@app.get('/')
def home():
    if not current_user(): return redirect(url_for('login'))
    return redirect(url_for('admin_dashboard' if current_user()['role'] == 'admin' else 'technician_dashboard'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        with db() as con:
            user = con.execute('SELECT * FROM users WHERE email=? AND active=1', (email,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user'] = {'id': user['id'], 'email': user['email'],
                               'name': user['full_name'], 'role': user['role']}
            session['csrf_token'] = secrets.token_urlsafe(32)
            session.permanent = True
            return redirect(url_for('home'))
        flash('Invalid email or password, or account is inactive.', 'error')
    session.setdefault('csrf_token', secrets.token_urlsafe(32))
    return render_template('login.html', csrf=session['csrf_token'], bootstrap=not bool(os.getenv('SOLARTRACK_BOOTSTRAP_ADMIN_EMAIL')))


@app.post('/logout')
@login_required
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.get('/technician')
@role_required('technician')
def technician_dashboard():
    return render_template('technician.html', user=current_user(), csrf=session['csrf_token'])


@app.get('/admin')
@role_required('admin')
def admin_dashboard():
    return render_template('admin.html', user=current_user(), csrf=session['csrf_token'])


def read_readings(limit=60):
    if not DB_PATH.exists(): return []
    with db() as con:
        try:
            rows = con.execute(f'SELECT * FROM readings ORDER BY id DESC LIMIT {int(limit)}').fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in reversed(rows)]


def model_prediction(row):
    if MODEL_BUNDLE is None: return 'model_unavailable', None
    try:
        bundle = MODEL_BUNDLE
        frame = pd.DataFrame([row])
        for col in ['top','bottom','left','right','load_voltage_V','load_current_mA','load_power_mW']:
            frame[col] = pd.to_numeric(frame.get(col), errors='coerce')
        frame['vertical_error'] = frame['top'] - frame['bottom']
        frame['horizontal_error'] = frame['left'] - frame['right']
        frame['mean_ldr'] = frame[['top','bottom','left','right']].mean(axis=1)
        frame['ldr_range'] = frame[['top','bottom','left','right']].max(axis=1) - frame[['top','bottom','left','right']].min(axis=1)
        x = frame[bundle.get('features', FEATURES)]
        pred = 'anomaly' if int(bundle['pipeline'].predict(x)[0]) == -1 else 'normal'
        score = round(float(-bundle['pipeline'].decision_function(x)[0]), 4)
        return pred, score
    except Exception as exc:
        app.logger.exception('ML prediction failed: %s', exc)
        return 'model_error', None


def parse_faults(row):
    faults = []
    for code in str(row.get('ldr_fault') or '').split(';'):
        code = code.strip().upper()
        if code in {'TOP','BOTTOM','LEFT','RIGHT'}: faults.append(f'{code}_LDR')
    if int(row.get('horizontal_motor_suspected') or 0): faults.append('HORIZONTAL_MOTOR_SUSPECTED')
    if int(row.get('vertical_motor_suspected') or 0): faults.append('VERTICAL_MOTOR_SUSPECTED')
    # Backward compatibility: old combined motor flag, without inventing an axis.
    if not any(x.endswith('_MOTOR_SUSPECTED') for x in faults) and int(row.get('motor_fault') or 0):
        faults.append('MOTOR_SUSPECTED_AXIS_UNKNOWN')
    return faults


@app.get('/api/technician/status')
@login_required
def technician_status():
    rows = read_readings(60)
    if not rows: return jsonify(error='No telemetry is available yet. Check the receiver and Arduino serial connection.'), 503
    latest = rows[-1]
    prediction, score = model_prediction(latest)
    try:
        sample_time = datetime.fromisoformat(str(latest.get('timestamp')).replace('Z', '+00:00'))
        if sample_time.tzinfo is None: sample_time = sample_time.replace(tzinfo=timezone.utc)
        telemetry_fresh = (datetime.now(timezone.utc) - sample_time).total_seconds() <= 5
    except Exception:
        telemetry_fresh = False
    pi_online = bool(int(latest.get('pi_online') or 0)) and telemetry_fresh
    faults = parse_faults(latest) if telemetry_fresh else []
    history = []
    for r in rows:
        history.append({k: r.get(k) for k in ['timestamp','top','bottom','left','right','horizontal_deg','vertical_deg','load_power_mW']})
    ldr_faults = [f for f in faults if f.endswith('_LDR')]
    h_motor = 'HORIZONTAL_MOTOR_SUSPECTED' in faults
    v_motor = 'VERTICAL_MOTOR_SUSPECTED' in faults
    h_axis_available = not h_motor and not any(x in ldr_faults for x in ('LEFT_LDR','RIGHT_LDR'))
    v_axis_available = not v_motor and not any(x in ldr_faults for x in ('TOP_LDR','BOTTOM_LDR'))
    return jsonify(timestamp=latest.get('timestamp'), top=latest.get('top'), bottom=latest.get('bottom'),
                   left=latest.get('left'), right=latest.get('right'),
                   horizontal_deg=latest.get('horizontal_deg'), vertical_deg=latest.get('vertical_deg'),
                   load_voltage_V=latest.get('load_voltage_V'), load_current_mA=latest.get('load_current_mA'),
                   load_power_mW=latest.get('load_power_mW'), load_energy_Wh=latest.get('load_energy_Wh'),
                   ina_ok=latest.get('ina_ok'), pi_online=pi_online, telemetry_fresh=telemetry_fresh,
                   ldr_faults=ldr_faults,
                   horizontal_motor_suspected=h_motor, vertical_motor_suspected=v_motor,
                   horizontal_axis_available=h_axis_available, vertical_axis_available=v_axis_available,
                   prediction=prediction, anomaly_score=score, hardware_faults=faults, history=history)


@app.get('/api/technician/fault-history')
@login_required
def technician_fault_history():
    with db() as con:
        rows = con.execute('SELECT fault_code,started_at,recovered_at FROM fault_episodes ORDER BY id DESC LIMIT 100').fetchall()
    return jsonify([dict(r) for r in rows])


@app.get('/api/admin/overview')
@role_required('admin')
def admin_overview():
    rows = read_readings(1)
    with db() as con:
        user_count = con.execute('SELECT COUNT(*) FROM users WHERE active=1').fetchone()[0]
        technician_count = con.execute("SELECT COUNT(*) FROM users WHERE active=1 AND role='technician'").fetchone()[0]
        open_faults = con.execute('SELECT COUNT(*) FROM fault_state WHERE active=1').fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM fault_state WHERE active=1 AND notified_at IS NULL").fetchone()[0]
        emails = con.execute('SELECT COUNT(*) FROM email_notifications WHERE status="sent"').fetchone()[0]
    return jsonify(latest_timestamp=rows[-1].get('timestamp') if rows else None,
                   users=user_count, technicians=technician_count, active_faults=open_faults,
                   pending_alerts=pending, sent_emails=emails)


@app.get('/api/admin/users')
@role_required('admin')
def admin_users():
    with db() as con:
        rows = con.execute('SELECT id,email,full_name,role,active,created_at FROM users ORDER BY id').fetchall()
    return jsonify([dict(r) for r in rows])


@app.post('/api/admin/users')
@role_required('admin')
def create_user():
    data = request.get_json(silent=True) or {}
    email = str(data.get('email','')).strip().lower()
    name = str(data.get('full_name','')).strip()
    password = str(data.get('password',''))
    role = str(data.get('role','technician'))
    if '@' not in email or not name or len(password) < 12 or role not in ('admin','technician'):
        return jsonify(error='Provide a valid email, full name, role, and password of at least 12 characters.'), 400
    try:
        with db() as con:
            con.execute('INSERT INTO users(email,full_name,password_hash,role,created_at) VALUES(?,?,?,?,?)',
                        (email,name,generate_password_hash(password),role,utcnow()))
        return jsonify(message='Account created.'), 201
    except sqlite3.IntegrityError:
        return jsonify(error='An account with this email already exists.'), 409


@app.patch('/api/admin/users/<int:user_id>')
@role_required('admin')
def update_user(user_id):
    data = request.get_json(silent=True) or {}
    if data.get('password') and len(str(data['password'])) < 12:
        return jsonify(error='Password must be at least 12 characters.'), 400
    with db() as con:
        target = con.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not target: return jsonify(error='User not found.'), 404
        active = int(bool(data.get('active', target['active'])))
        role = data.get('role', target['role'])
        if role not in ('admin','technician'): return jsonify(error='Invalid role.'), 400
        if user_id == current_user()['id'] and (not active or role != 'admin'):
            return jsonify(error='You cannot deactivate or demote your own account.'), 400
        if target['role'] == 'admin' and (not active or role != 'admin'):
            admins = con.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            if admins <= 1: return jsonify(error='At least one active administrator must remain.'), 400
        con.execute('UPDATE users SET active=?, role=? WHERE id=?', (active,role,user_id))
        if data.get('password'):
            password = str(data['password'])
            con.execute('UPDATE users SET password_hash=? WHERE id=?', (generate_password_hash(password),user_id))
    return jsonify(message='Account updated.')


@app.get('/api/admin/notifications')
@role_required('admin')
def admin_notifications():
    with db() as con:
        rows = con.execute('SELECT id,attempted_at,sent_at,fault_codes,recipients,status,attempts,error FROM email_notifications ORDER BY id DESC LIMIT 200').fetchall()
    return jsonify([dict(r) for r in rows])


@app.get('/api/admin/fault-history')
@role_required('admin')
def admin_fault_history():
    with db() as con:
        rows = con.execute('SELECT fault_code,started_at,recovered_at,notified_at FROM fault_episodes ORDER BY id DESC LIMIT 200').fetchall()
    return jsonify([dict(r) for r in rows])


@app.get('/health')
def health():
    return jsonify(status='ok')


init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
