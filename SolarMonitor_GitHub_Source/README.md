# SolarMonitor / SolarTrack — Source Code

This repository contains the Arduino firmware and Raspberry Pi/dashboard source files for the SolarTrack solar monitoring and tracking project.

## Repository structure

```text
SolarMonitor_GitHub_Source/
├── arduino/
│   └── SolarTrack_FaultTolerant_Telemetry.ino
├── raspberry_pi/
│   └── receiver.py
├── dashboard/
│   ├── app.py
│   ├── alert_worker.py
│   ├── train_model.py
│   ├── requirements.txt
│   └── templates/
│       ├── admin.html
│       ├── login.html
│       └── technician.html
├── docs/
│   └── Dashboard_Package_Notes.md
├── .gitignore
└── README.md
```

## Components

- **Arduino firmware:** Nano 33 IoT tracking controller, four LDR inputs, dual servo control, INA219 monitoring, fault diagnostics and Pi heartbeat/telemetry.
- **Raspberry Pi receiver:** reads serial telemetry, maintains CSV/SQLite records, and sends the Pi heartbeat.
- **Flask dashboard:** authentication, administrator/technician roles, live readings, history and ML anomaly display.
- **Alert worker:** independent persistent hardware-fault email notification process.
- **Training script:** retained for reproducibility/reference. Do not run it on the deployed system unless intentionally retraining the model.

## Version provenance — read before flashing firmware

The Python backend and dashboard files are from the latest complete saved package dated **24 November 2026** (`SolarTrack_Updated_Package.zip`, version 2). This package's receiver supports the 17-field telemetry schema and Pi heartbeat used by the deployed architecture.

The Arduino file included here is the latest complete sketch recoverable from that same saved package. The final firmware was subsequently discussed/modified during the **25 November 2026** hardware test (including the final low-light behaviour and threshold decisions), but that exact final IDE sketch was not saved as a separate source attachment in the available project files. Therefore, this `.ino` is a recoverable source snapshot, **not a verified byte-for-byte copy of the final 25 November IDE firmware**. Before flashing or presenting it as the final firmware, compare it with the sketch currently running on the Nano 33 IoT and replace it with that exact final `.ino` if it differs.

## Setup overview

1. Install the Arduino libraries listed in the firmware comments and select **Arduino Nano 33 IoT** in Arduino IDE.
2. On the Raspberry Pi, install the packages in `dashboard/requirements.txt` into your project virtual environment.
3. Run `raspberry_pi/receiver.py` from the backend directory and run `dashboard/app.py` and `dashboard/alert_worker.py` from the dashboard directory, or use your existing systemd services.
4. Keep the existing trained `solartrack_anomaly_model.joblib` in the dashboard directory. The model is intentionally not included in this source-only archive.
5. Configure runtime secrets and SMTP credentials in a local `.env` file. Never commit that file.

See `docs/Dashboard_Package_Notes.md` for the fuller deployment, environment-variable, role-permission and alert-behaviour notes.

## Important: files intentionally excluded

This is a **source-code archive**, not a full deployment backup. It intentionally excludes:

- `.env`, passwords, secret keys, SMTP credentials and other secrets
- Python virtual environments and installed packages
- SQLite database and CSV telemetry/history
- trained ML model binary (`solartrack_anomaly_model.joblib`)
- logs, caches and generated runtime files

Keep those private/runtime files backed up separately. Do not upload them to a public GitHub repository.
