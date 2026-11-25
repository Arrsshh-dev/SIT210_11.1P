from pathlib import Path
import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "solartrack_readings.csv"
MODEL_PATH = BASE_DIR / "solartrack_anomaly_model.joblib"
FEATURES = ["top", "bottom", "left", "right", "vertical_error",
            "horizontal_error", "mean_ldr", "ldr_range",
            "load_voltage_V", "load_current_mA", "load_power_mW"]

def add_features(frame):
    frame = frame.copy()
    cols = ["top", "bottom", "left", "right", "load_voltage_V",
            "load_current_mA", "load_power_mW"]
    for col in cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["vertical_error"] = frame["top"] - frame["bottom"]
    frame["horizontal_error"] = frame["left"] - frame["right"]
    frame["mean_ldr"] = frame[["top", "bottom", "left", "right"]].mean(axis=1)
    frame["ldr_range"] = frame[["top", "bottom", "left", "right"]].max(axis=1) - frame[["top", "bottom", "left", "right"]].min(axis=1)
    return frame

def main():
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Place CSV at {CSV_PATH}")
    data = pd.read_csv(CSV_PATH)
    if "condition" not in data:
        raise ValueError("CSV requires a condition column.")
    normal = data[data["condition"].astype(str).str.strip().str.lower().eq("normal")].copy()
    if len(normal) < 100:
        raise ValueError(f"Only {len(normal)} normal rows found; collect at least 100.")
    X = add_features(normal)[FEATURES]
    model = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", RobustScaler()),
        ("detector", IsolationForest(n_estimators=300, contamination=0.05,
                                     max_samples="auto", random_state=42))
    ])
    model.fit(X)
    joblib.dump({"pipeline": model, "features": FEATURES}, MODEL_PATH)
    print(f"Trained on {len(normal)} verified-normal rows. Saved {MODEL_PATH}")
    print("ML anomaly detection does not diagnose the failed component.")

if __name__ == "__main__":
    main()
