"""Persistent 5-minute hardware-fault alert worker. Run exactly one instance."""
import os, time, sqlite3, smtplib, fcntl
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from app import DB_PATH, init_db

POLL_SECONDS = 5
PERSIST_SECONDS = 300
LOCK_PATH = Path(os.getenv('SOLARTRACK_WORKER_LOCK', '/tmp/solartrack-alert-worker.lock'))


def now_utc(): return datetime.now(timezone.utc)
def iso(dt=None): return (dt or now_utc()).isoformat(timespec='seconds')
def parse_time(value):
    try: return datetime.fromisoformat(value.replace('Z','+00:00'))
    except Exception: return now_utc()


def connect():
    con=sqlite3.connect(DB_PATH,timeout=15); con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON'); return con


def current_faults(row):
    faults=set()
    for item in str(row['ldr_fault'] or '').split(';'):
        item=item.strip().upper()
        if item in {'TOP','BOTTOM','LEFT','RIGHT'}: faults.add(item+'_LDR')
    if int(row['horizontal_motor_suspected'] or 0): faults.add('HORIZONTAL_MOTOR_SUSPECTED')
    if int(row['vertical_motor_suspected'] or 0): faults.add('VERTICAL_MOTOR_SUSPECTED')
    if not any('MOTOR' in x for x in faults) and int(row['motor_fault'] or 0):
        faults.add('MOTOR_SUSPECTED_AXIS_UNKNOWN')
    return faults


def update_episodes(con, active_faults, timestamp):
    states={r['fault_code']:dict(r) for r in con.execute('SELECT * FROM fault_state').fetchall()}
    for code,state in states.items():
        if state['active'] and code not in active_faults:
            con.execute('UPDATE fault_state SET active=0,first_seen_at=NULL,notified_at=NULL,last_seen_at=?,attempt_count=0,next_retry_at=NULL WHERE fault_code=?',(timestamp,code))
            con.execute('UPDATE fault_episodes SET recovered_at=? WHERE fault_code=? AND recovered_at IS NULL',(timestamp,code))
    for code in active_faults:
        state=states.get(code)
        if not state or not state['active']:
            con.execute('''INSERT INTO fault_state(fault_code,active,first_seen_at,notified_at,last_seen_at,attempt_count,next_retry_at)
                           VALUES(?,1,?,NULL,?,0,NULL)
                           ON CONFLICT(fault_code) DO UPDATE SET active=1,first_seen_at=excluded.first_seen_at,
                           notified_at=NULL,last_seen_at=excluded.last_seen_at,attempt_count=0,next_retry_at=NULL''',
                        (code,timestamp,timestamp))
            con.execute('INSERT INTO fault_episodes(fault_code,started_at) VALUES(?,?)',(code,timestamp))
        else:
            con.execute('UPDATE fault_state SET last_seen_at=? WHERE fault_code=?',(timestamp,code))


def recipients(con):
    override=os.getenv('SOLARTRACK_ALERT_RECIPIENTS','').strip()
    if override: return sorted({x.strip() for x in override.split(';') if '@' in x})
    return [r['email'] for r in con.execute("SELECT email FROM users WHERE active=1 AND role='technician' ORDER BY email").fetchall()]


def send_email(to_list, fault_codes, first_seen):
    host=os.getenv('SOLARTRACK_SMTP_HOST','').strip()
    port=int(os.getenv('SOLARTRACK_SMTP_PORT','587'))
    username=os.getenv('SOLARTRACK_SMTP_USERNAME','').strip()
    password=os.getenv('SOLARTRACK_SMTP_PASSWORD','')
    sender=os.getenv('SOLARTRACK_SMTP_FROM',username).strip()
    if not all([host,username,password,sender]): raise RuntimeError('SMTP environment variables are incomplete.')
    if not to_list: raise RuntimeError('No active technician recipients are configured.')
    names={'TOP_LDR':'Top LDR sensor','BOTTOM_LDR':'Bottom LDR sensor','LEFT_LDR':'Left LDR sensor',
           'RIGHT_LDR':'Right LDR sensor','HORIZONTAL_MOTOR_SUSPECTED':'Horizontal motor (suspected)',
           'VERTICAL_MOTOR_SUSPECTED':'Vertical motor (suspected)',
           'MOTOR_SUSPECTED_AXIS_UNKNOWN':'Motor fault (axis not available in legacy telemetry)'}
    msg=EmailMessage(); msg['Subject']='SolarTrack: persistent hardware fault detected'
    msg['From']=sender; msg['To']=', '.join(to_list)
    lines=['SolarTrack has detected the following hardware fault(s) persisting for at least five minutes:','']
    for code in fault_codes: lines.append(f"- {names.get(code,code)} (first detected: {first_seen.get(code, 'unknown')})")
    lines += ['', 'This alert is based on the Arduino fault-tolerance status. ML anomaly scores are not used to trigger hardware-fault emails.',
              'Check the SolarTrack dashboard before taking maintenance action.']
    msg.set_content('\n'.join(lines))
    if port==465:
        with smtplib.SMTP_SSL(host,port,timeout=20) as server:
            server.login(username,password); server.send_message(msg)
    else:
        with smtplib.SMTP(host,port,timeout=20) as server:
            server.ehlo(); server.starttls(); server.ehlo()
            server.login(username,password); server.send_message(msg)


def process_once():
    con=connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT * FROM readings ORDER BY id DESC LIMIT 1').fetchone()
        if not row:
            con.commit(); return
        # Do not infer recovery or start an alert timer from stale/offline telemetry.
        if not int(row['pi_online'] or 0) or (now_utc()-parse_time(row['timestamp'])).total_seconds()>15:
            con.commit(); return
        stamp=row['timestamp']; active=current_faults(row)
        update_episodes(con,active,stamp)
        now=now_utc()
        due=con.execute('''SELECT * FROM fault_state WHERE active=1 AND notified_at IS NULL
                           AND first_seen_at IS NOT NULL''').fetchall()
        due=[dict(r) for r in due if (now-parse_time(r['first_seen_at'])).total_seconds()>=PERSIST_SECONDS
             and (not r['next_retry_at'] or parse_time(r['next_retry_at'])<=now)]
        if not due:
            con.commit(); return
        codes=sorted(r['fault_code'] for r in due)
        first={r['fault_code']:r['first_seen_at'] for r in due}
        to_list=recipients(con); attempted=iso()
        # Claim this batch and commit before SMTP. Serial telemetry writes are never
        # held behind a network operation; the retry lease also prevents two workers
        # from claiming the same fault episode at once.
        attempt_number=max(r['attempt_count'] for r in due)+1
        lease=iso(now+timedelta(seconds=120))
        for state in due:
            con.execute('''UPDATE fault_state SET attempt_count=attempt_count+1,next_retry_at=?
                           WHERE fault_code=? AND active=1 AND notified_at IS NULL AND first_seen_at=?''',
                        (lease,state['fault_code'],state['first_seen_at']))
        cur=con.execute('''INSERT INTO email_notifications(attempted_at,sent_at,fault_codes,recipients,status,attempts,error)
                           VALUES(?,NULL,?,?,?, ?,NULL)''',
                        (attempted,';'.join(codes),';'.join(to_list),'sending',attempt_number))
        notification_id=cur.lastrowid
        con.commit()
    except Exception:
        con.rollback(); raise
    finally:
        con.close()

    # SMTP runs outside SQLite transactions so receiver.py can continue writing rows.
    try:
        send_email(to_list,codes,first)
        sent=iso()
        con=connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            con.execute("UPDATE email_notifications SET status='sent',sent_at=?,error=NULL WHERE id=?",(sent,notification_id))
            for code in codes:
                con.execute('''UPDATE fault_state SET notified_at=?,next_retry_at=NULL
                               WHERE fault_code=? AND active=1 AND notified_at IS NULL AND first_seen_at=?''',
                            (sent,code,first[code]))
                con.execute('''UPDATE fault_episodes SET notified_at=? WHERE fault_code=?
                               AND started_at=? AND recovered_at IS NULL''',(sent,code,first[code]))
            con.commit()
        finally: con.close()
        print('Alert email sent for:',', '.join(codes))
    except Exception as exc:
        con=connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            con.execute("UPDATE email_notifications SET status='failed',error=? WHERE id=?",(str(exc)[:1000],notification_id))
            for state in due:
                attempts=state['attempt_count']+1
                delay=min(60*(2**min(attempts-1,5)),1800)
                retry=iso(now_utc()+timedelta(seconds=delay))
                con.execute('''UPDATE fault_state SET next_retry_at=?
                               WHERE fault_code=? AND active=1 AND notified_at IS NULL AND first_seen_at=?''',
                            (retry,state['fault_code'],state['first_seen_at']))
            con.commit()
        finally: con.close()
        print('Alert email failed; retry scheduled:',exc)


def main():
    init_db()
    LOCK_PATH.parent.mkdir(parents=True,exist_ok=True)
    lock=open(LOCK_PATH,'w')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Another SolarTrack alert worker is already running.')
    print(f'SolarTrack alert worker running; persistence threshold={PERSIST_SECONDS}s')
    while True:
        try: process_once()
        except Exception as exc: print('Worker cycle error:',exc)
        time.sleep(POLL_SECONDS)

if __name__=='__main__': main()
