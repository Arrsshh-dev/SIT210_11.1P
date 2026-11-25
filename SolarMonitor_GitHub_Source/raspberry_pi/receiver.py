"""SolarTrack serial receiver. One DATA row per Arduino telemetry frame; no ML retraining/data duplication."""
import csv, os, sqlite3, time
from pathlib import Path
from datetime import datetime, timezone
import serial
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')
SERIAL_PORT = os.getenv('SOLARTRACK_SERIAL_PORT', '/dev/serial0')
BAUD_RATE = int(os.getenv('SOLARTRACK_BAUD_RATE', '9600'))
DB_FILE = Path(os.getenv('SOLARTRACK_DB', str(BASE_DIR / 'solartrack.db')))
CSV_FILE = Path(os.getenv('SOLARTRACK_CSV', str(DB_FILE.parent / 'solartrack_readings.csv')))
HEARTBEAT_INTERVAL = 1.0
CURRENT_CONDITION = 'unknown'  # Never label unverified rows as normal.
FIELDS = ['timestamp','time_ms','top','bottom','left','right','horizontal_deg','vertical_deg',
          'load_voltage_V','load_current_mA','load_power_mW','load_energy_Wh','ina_ok','condition',
          'ldr_fault','motor_fault','horizontal_motor_suspected','vertical_motor_suspected','pi_online']


def migrate_csv():
    if not CSV_FILE.exists(): return
    with open(CSV_FILE, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f); rows = list(reader); old = reader.fieldnames or []
    if old == FIELDS: return
    temp = CSV_FILE.with_suffix('.tmp')
    with open(temp, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS); writer.writeheader()
        for row in rows:
            row.setdefault('condition','unknown'); row.setdefault('ldr_fault','')
            row.setdefault('motor_fault','0'); row.setdefault('horizontal_motor_suspected','0')
            row.setdefault('vertical_motor_suspected','0'); row.setdefault('pi_online','0')
            # Do not rewrite historical sensor values or fabricate fault diagnoses.
            writer.writerow({k: row.get(k, '') for k in FIELDS})
    os.replace(temp, CSV_FILE)
    print('CSV schema extended in place; historical readings preserved.')


def initialize_database():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_FILE, timeout=10)
    con.execute('''CREATE TABLE IF NOT EXISTS readings (
      id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, time_ms INTEGER,
      top INTEGER, bottom INTEGER, left INTEGER, right INTEGER,
      horizontal_deg INTEGER, vertical_deg INTEGER, load_voltage_V REAL,
      load_current_mA REAL, load_power_mW REAL, load_energy_Wh REAL, ina_ok INTEGER,
      condition TEXT DEFAULT 'unknown', ldr_fault TEXT DEFAULT '', motor_fault INTEGER DEFAULT 0,
      horizontal_motor_suspected INTEGER DEFAULT 0, vertical_motor_suspected INTEGER DEFAULT 0,
      pi_online INTEGER DEFAULT 0)''')
    cols = {r[1] for r in con.execute('PRAGMA table_info(readings)')}
    additions = {'condition':"TEXT DEFAULT 'unknown'",'ldr_fault':"TEXT DEFAULT ''",
                 'motor_fault':'INTEGER DEFAULT 0','horizontal_motor_suspected':'INTEGER DEFAULT 0',
                 'vertical_motor_suspected':'INTEGER DEFAULT 0','pi_online':'INTEGER DEFAULT 0'}
    for name, decl in additions.items():
        if name not in cols: con.execute(f'ALTER TABLE readings ADD COLUMN {name} {decl}')
    con.commit(); return con


def initialize_csv():
    CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CSV_FILE.exists():
        with open(CSV_FILE,'w',newline='',encoding='utf-8') as f: csv.writer(f).writerow(FIELDS)


def save_reading(con, data):
    # The receiver still writes exactly one sensor row per DATA frame.
    with open(CSV_FILE,'a',newline='',encoding='utf-8') as f:
        csv.DictWriter(f,fieldnames=FIELDS).writerow(data)
    columns = ','.join(FIELDS); marks = ','.join('?' for _ in FIELDS)
    con.execute(f'INSERT INTO readings ({columns}) VALUES ({marks})',[data[k] for k in FIELDS])
    con.commit()


def process_line(line, con):
    line = line.strip()
    if not line or line.startswith('HEADER'): return
    p = line.split(',')
    # 13 = original DATA format; 16 = older extension with combined motor flag;
    # 17 = current extension with independent H/V motor suspicion flags.
    if p[0] != 'DATA' or len(p) not in (13,16,17):
        if line.startswith('DATA,'): print('Ignoring unsupported DATA frame length:', len(p))
        return
    try:
        if len(p) == 13:
            ldr, h_motor, v_motor, pi = '', 0, 0, 0
        elif len(p) == 16:
            ldr, combined, pi = p[13], int(p[14]), int(p[15])
            h_motor, v_motor = combined, 0  # Legacy combined signal; axis is unknown.
        else:
            ldr, h_motor, v_motor, pi = p[13], int(p[14]), int(p[15]), int(p[16])
        data = {'timestamp':datetime.now(timezone.utc).isoformat(), 'time_ms':int(p[1]),
                'top':int(p[2]),'bottom':int(p[3]),'left':int(p[4]),'right':int(p[5]),
                'horizontal_deg':int(p[6]),'vertical_deg':int(p[7]),'load_voltage_V':float(p[8]),
                'load_current_mA':float(p[9]),'load_power_mW':float(p[10]),'load_energy_Wh':float(p[11]),
                'ina_ok':int(p[12]),'condition':CURRENT_CONDITION,'ldr_fault':ldr,
                'horizontal_motor_suspected':int(h_motor),'vertical_motor_suspected':int(v_motor),
                'motor_fault':int(bool(h_motor or v_motor)),'pi_online':int(pi)}
        if not 0 <= data['horizontal_deg'] <= 180: raise ValueError('Horizontal angle outside 0–180')
        if not 0 <= data['vertical_deg'] <= 60: raise ValueError('Vertical angle outside 0–60')
        if data['ina_ok'] not in (0,1) or data['pi_online'] not in (0,1): raise ValueError('Invalid status flag')
        if data['horizontal_motor_suspected'] not in (0,1) or data['vertical_motor_suspected'] not in (0,1): raise ValueError('Invalid motor flag')
        save_reading(con,data)
        print(f"H={data['horizontal_deg']} V={data['vertical_deg']} P={data['load_power_mW']:.1f}mW "
              f"LDR={data['ldr_fault'] or 'none'} H-motor={data['horizontal_motor_suspected']} "
              f"V-motor={data['vertical_motor_suspected']} Pi={data['pi_online']}")
    except (ValueError,TypeError,IndexError) as exc:
        print('Invalid telemetry:',exc)


def main():
    print(f'SOLARTRACK RECEIVER | serial={SERIAL_PORT} | database={DB_FILE}')
    migrate_csv(); initialize_csv(); con=initialize_database(); last_ping=0
    try:
        with serial.Serial(SERIAL_PORT,BAUD_RATE,timeout=0.2,write_timeout=0.2) as ser:
            time.sleep(2); ser.reset_input_buffer()
            print('Serial connected. Pi heartbeat enabled.')
            while True:
                now=time.monotonic()
                if now-last_ping >= HEARTBEAT_INTERVAL:
                    ser.write(b'PING\n'); last_ping=now
                raw=ser.readline()
                if raw:
                    try: process_line(raw.decode('utf-8'),con)
                    except UnicodeDecodeError: print('Invalid serial encoding.')
    except serial.SerialException as exc: print('Serial connection error:',exc)
    except KeyboardInterrupt: print('\nReceiver stopped.')
    finally: con.close()

if __name__ == '__main__': main()
