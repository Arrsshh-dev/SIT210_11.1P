// Libraries used by the sensors, display and motors.
#include <Wire.h>
#include <Servo.h>
#include <Adafruit_INA219.h>
#include <LiquidCrystal_I2C.h>

// LDR pins and servo pins.
const int TOP_LDR = A0;
const int BOTTOM_LDR = A1;
const int LEFT_LDR = A2;
const int RIGHT_LDR = A3;

const int HORIZONTAL_PIN = 9;
const int VERTICAL_PIN = 10;

// Objects used to control the servos, power sensor and LCD.
Servo horizontalServo;
Servo verticalServo;

Adafruit_INA219 ina219;
LiquidCrystal_I2C lcd(0x27, 20, 4);

bool ina219Available = false;

// Servo movement limits and starting positions.
const int HORIZONTAL_MIN = 0;
const int HORIZONTAL_MAX = 180;

const int VERTICAL_MIN = 0;
const int VERTICAL_MAX = 60;

int horizontalPosition = 90;
int verticalPosition = 25;

// Tracking settings control how often and how far the servos move.
const int TOLERANCE = 40;

const int NORMAL_STEP = 1;
const int DIAGNOSTIC_STEP = 3;

const unsigned long TRACKING_INTERVAL = 200;
const unsigned long DIAGNOSTIC_WAIT = 900;

const int MIN_DIAGNOSTIC_ERROR = 80;

// These values decide when an LDR is faulty or has recovered.
const int LDR_FAULT_THRESHOLD = 45;
const int LDR_RECOVERY_THRESHOLD = 60;

const unsigned long LDR_FAULT_DELAY = 2000;
const unsigned long LDR_RECOVERY_DELAY = 5000;

// Settings used to detect when the overall light level is too low.
const int GLOBAL_LOW_LIGHT_THRESHOLD = 80;
const int GLOBAL_LIGHT_RECOVERY_THRESHOLD = 100;

const unsigned long LOW_LIGHT_ENTER_DELAY = 3000;
const unsigned long LOW_LIGHT_EXIT_DELAY = 3000;

bool lowLightMode = false;

unsigned long lowLightCandidateSince = 0;
unsigned long brightCandidateSince = 0;

// Motor checks need repeated failed results before a fault is suspected.
const int REQUIRED_FAILED_TESTS = 4;
const int MIN_ERROR_IMPROVEMENT = 5;

const float LIGHT_STABILITY_PERCENT = 0.15f;
const int MIN_LIGHT_STABILITY = 40;

// Time intervals for sending data and refreshing the LCD.
const unsigned long DATA_INTERVAL = 1000;
const unsigned long LCD_INTERVAL = 1000;

// Latest readings from the four LDR sensors.
int topValue = 0;
int bottomValue = 0;
int leftValue = 0;
int rightValue = 0;

// Electrical readings from the INA219 sensor.
float loadVoltage = 0.0;
float loadCurrentmA = 0.0;
float loadPowermW = 0.0;
float loadEnergyWh = 0.0;

// Timers used to run tasks at set intervals.
unsigned long lastTracking = 0;
unsigned long lastData = 0;
unsigned long lastLCD = 0;
unsigned long lastEnergy = 0;

// Stores the fault and recovery timing for each LDR.
struct SensorStatus {
  bool fault = false;
  unsigned long lowSince = 0;
  unsigned long recoverySince = 0;
};

SensorStatus sensor[4];

const char* sensorNames[4] = {
  "TOP", "BOTTOM", "LEFT", "RIGHT"
};

String ldrFaults = "";

// Tracks whether the Raspberry Pi is responding.
bool piOnline = false;

unsigned long lastPiHeartbeat = 0;

const unsigned long PI_TIMEOUT_MS = 4000;

String piCommand = "";

// Stores the result and timing of each motor test.
struct MotorDiagnostic {
  bool pending = false;
  bool suspected = false;

  int previousError = 0;
  int previousLight = 0;

  unsigned long testStarted = 0;

  int failedTests = 0;
};

MotorDiagnostic horizontalDiagnostic;
MotorDiagnostic verticalDiagnostic;

// Reads an LDR several times to get a steadier value.
int readStableLDR(int pin) {

  analogRead(pin);
  delay(5);

  long total = 0;

  for (int i = 0; i < 10; i++) {
    total += analogRead(pin);
    delay(2);
  }

  return total / 10;
}

// Reads all four LDR sensors.
void readLDRs() {

  topValue = readStableLDR(TOP_LDR);
  bottomValue = readStableLDR(BOTTOM_LDR);
  leftValue = readStableLDR(LEFT_LDR);
  rightValue = readStableLDR(RIGHT_LDR);

}

// Makes a text list of any LDR sensors currently marked faulty.
void rebuildLDRFaultList() {

  ldrFaults = "";

  for (int i = 0; i < 4; i++) {

    if (sensor[i].fault) {

      if (ldrFaults.length() > 0) {
        ldrFaults += ";";
      }

      ldrFaults += sensorNames[i];
    }
  }
}

// Checks for low light and detects LDR faults or recovery.
void updateSensorFaults() {

  int values[4] = {
    topValue,
    bottomValue,
    leftValue,
    rightValue
  };

  unsigned long now = millis();

  bool allSensorsLow = true;
  int brightSensorCount = 0;
  int maximumReading = 0;

  for (int i = 0; i < 4; i++) {

    if (values[i] >= GLOBAL_LOW_LIGHT_THRESHOLD) {
      allSensorsLow = false;
    }

    if (values[i] >= GLOBAL_LIGHT_RECOVERY_THRESHOLD) {
      brightSensorCount++;
    }

    if (values[i] > maximumReading) {
      maximumReading = values[i];
    }
  }

  if (!lowLightMode) {

    brightCandidateSince = 0;

    if (allSensorsLow) {

      if (lowLightCandidateSince == 0) {
        lowLightCandidateSince = now;
      }

      if (now - lowLightCandidateSince >=
          LOW_LIGHT_ENTER_DELAY) {

        lowLightMode = true;

        lowLightCandidateSince = 0;

        horizontalDiagnostic.pending = false;
        verticalDiagnostic.pending = false;

        Serial.println(
          "LOW LIGHT DETECTED - TRACKING PAUSED"
        );
      }

    } else {

      lowLightCandidateSince = 0;
    }
  }

  else {

    lowLightCandidateSince = 0;

    if (maximumReading >= GLOBAL_LIGHT_RECOVERY_THRESHOLD) {

      if (brightCandidateSince == 0) {
        brightCandidateSince = now;
      }

      if (now - brightCandidateSince >=
          LOW_LIGHT_EXIT_DELAY) {

        lowLightMode = false;

        brightCandidateSince = 0;

        Serial.println(
          "LIGHT DETECTED - TRACKING RESUMED"
        );
      }

    } else {

      brightCandidateSince = 0;
    }
  }

  if (lowLightMode) {

    for (int i = 0; i < 4; i++) {
      sensor[i].lowSince = 0;
      sensor[i].recoverySince = 0;
    }

    rebuildLDRFaultList();

    return;
  }

  bool sufficientLightForFaultDiagnosis =
    (brightSensorCount >= 2);

  for (int i = 0; i < 4; i++) {

    if (sensor[i].fault) {

      if (values[i] >= LDR_RECOVERY_THRESHOLD) {

        if (sensor[i].recoverySince == 0) {
          sensor[i].recoverySince = now;
        }

        if (now - sensor[i].recoverySince >=
            LDR_RECOVERY_DELAY) {

          sensor[i].fault = false;
          sensor[i].lowSince = 0;
          sensor[i].recoverySince = 0;

          Serial.print(sensorNames[i]);
          Serial.println(" LDR RECOVERED");
        }

      } else {

        sensor[i].recoverySince = 0;
      }

      continue;
    }

    if (sufficientLightForFaultDiagnosis &&
        values[i] < LDR_FAULT_THRESHOLD) {

      if (sensor[i].lowSince == 0) {
        sensor[i].lowSince = now;
      }

      if (now - sensor[i].lowSince >=
          LDR_FAULT_DELAY) {

        sensor[i].fault = true;
        sensor[i].recoverySince = 0;

        Serial.print(sensorNames[i]);
        Serial.println(" LDR FAULT - ISOLATED");
      }

    } else {

      sensor[i].lowSince = 0;
    }
  }

  rebuildLDRFaultList();
}

// Checks whether the horizontal axis is safe to use.
bool horizontalAxisAvailable() {

  return !sensor[2].fault &&
         !sensor[3].fault &&
         !horizontalDiagnostic.suspected;
}

// Checks whether the vertical axis is safe to use.
bool verticalAxisAvailable() {

  return !sensor[0].fault &&
         !sensor[1].fault &&
         !verticalDiagnostic.suspected;
}

// Checks whether the light level stayed fairly steady during a motor test.
bool lightWasStable(int previousLight,
                    int currentLight) {

  int difference = abs(currentLight - previousLight);

  int allowedDifference =
    max(MIN_LIGHT_STABILITY,
        (int)(previousLight * LIGHT_STABILITY_PERCENT));

  return difference <= allowedDifference;
}

// Checks whether the horizontal motor movement improved the sensor readings.
void checkHorizontalMotor() {

  if (!horizontalDiagnostic.pending) {
    return;
  }

  if (millis() - horizontalDiagnostic.testStarted <
      DIAGNOSTIC_WAIT) {
    return;
  }

  horizontalDiagnostic.pending = false;

  int currentError = abs(leftValue - rightValue);
  int currentLight = leftValue + rightValue;

  if (currentLight < 160) {
    Serial.println(
      "Horizontal diagnostic inconclusive: insufficient light."
    );
    return;
  }

  bool stable = lightWasStable(
    horizontalDiagnostic.previousLight,
    currentLight
  );

  if (!stable) {

    Serial.println(
      "Horizontal diagnostic inconclusive: light changed."
    );

    return;
  }

  int improvement =
    horizontalDiagnostic.previousError - currentError;

  if (improvement >= MIN_ERROR_IMPROVEMENT) {

    horizontalDiagnostic.failedTests = 0;

    Serial.print("Horizontal motor response OK: ");
    Serial.println(improvement);

  } else {

    horizontalDiagnostic.failedTests++;

    Serial.print("Horizontal response not detected: ");
    Serial.print(horizontalDiagnostic.failedTests);
    Serial.print("/");
    Serial.println(REQUIRED_FAILED_TESTS);

    if (horizontalDiagnostic.failedTests >=
        REQUIRED_FAILED_TESTS) {

      horizontalDiagnostic.suspected = true;

      Serial.println(
        "HORIZONTAL MOTOR SUSPECTED - AXIS ISOLATED"
      );
    }
  }
}

// Checks whether the vertical motor movement improved the sensor readings.
void checkVerticalMotor() {

  if (!verticalDiagnostic.pending) {
    return;
  }

  if (millis() - verticalDiagnostic.testStarted <
      DIAGNOSTIC_WAIT) {
    return;
  }

  verticalDiagnostic.pending = false;

  int currentError = abs(topValue - bottomValue);
  int currentLight = topValue + bottomValue;

  if (currentLight < 160) {
    Serial.println(
      "Vertical diagnostic inconclusive: insufficient light."
    );
    return;
  }

  bool stable = lightWasStable(
    verticalDiagnostic.previousLight,
    currentLight
  );

  if (!stable) {

    Serial.println(
      "Vertical diagnostic inconclusive: light changed."
    );

    return;
  }

  int improvement =
    verticalDiagnostic.previousError - currentError;

  if (improvement >= MIN_ERROR_IMPROVEMENT) {

    verticalDiagnostic.failedTests = 0;

    Serial.print("Vertical motor response OK: ");
    Serial.println(improvement);

  } else {

    verticalDiagnostic.failedTests++;

    Serial.print("Vertical response not detected: ");
    Serial.print(verticalDiagnostic.failedTests);
    Serial.print("/");
    Serial.println(REQUIRED_FAILED_TESTS);

    if (verticalDiagnostic.failedTests >=
        REQUIRED_FAILED_TESTS) {

      verticalDiagnostic.suspected = true;

      Serial.println(
        "VERTICAL MOTOR SUSPECTED - AXIS ISOLATED"
      );
    }
  }
}

// Adjusts the horizontal servo based on the left and right LDR readings.
void trackHorizontal() {

  if (!horizontalAxisAvailable()) {
    return;
  }

  if (horizontalDiagnostic.pending) {
    return;
  }

  int error = leftValue - rightValue;
  int absoluteError = abs(error);

  if (absoluteError <= TOLERANCE) {
    return;
  }

  bool canMove = (error > 0)
    ? horizontalPosition > HORIZONTAL_MIN
    : horizontalPosition < HORIZONTAL_MAX;

  if (!canMove) {
    return;
  }

  if (absoluteError >= MIN_DIAGNOSTIC_ERROR &&
      (leftValue + rightValue) >= 160) {

    horizontalDiagnostic.previousError = absoluteError;

    horizontalDiagnostic.previousLight =
      leftValue + rightValue;

    horizontalDiagnostic.testStarted = millis();
    horizontalDiagnostic.pending = true;

    if (error > 0) {
      horizontalPosition -= DIAGNOSTIC_STEP;
    } else {
      horizontalPosition += DIAGNOSTIC_STEP;
    }

  } else {

    if (error > 0) {
      horizontalPosition -= NORMAL_STEP;
    } else {
      horizontalPosition += NORMAL_STEP;
    }
  }

  horizontalPosition = constrain(
    horizontalPosition,
    HORIZONTAL_MIN,
    HORIZONTAL_MAX
  );

  horizontalServo.write(horizontalPosition);
}

// Adjusts the vertical servo based on the top and bottom LDR readings.
void trackVertical() {

  if (!verticalAxisAvailable()) {
    return;
  }

  if (verticalDiagnostic.pending) {
    return;
  }

  int error = topValue - bottomValue;
  int absoluteError = abs(error);

  if (absoluteError <= TOLERANCE) {
    return;
  }

  bool canMove = (error > 0)
    ? verticalPosition < VERTICAL_MAX
    : verticalPosition > VERTICAL_MIN;

  if (!canMove) {
    return;
  }

  if (absoluteError >= MIN_DIAGNOSTIC_ERROR &&
      (topValue + bottomValue) >= 160) {

    verticalDiagnostic.previousError = absoluteError;

    verticalDiagnostic.previousLight =
      topValue + bottomValue;

    verticalDiagnostic.testStarted = millis();
    verticalDiagnostic.pending = true;

    if (error > 0) {
      verticalPosition += DIAGNOSTIC_STEP;
    } else {
      verticalPosition -= DIAGNOSTIC_STEP;
    }

  } else {

    if (error > 0) {
      verticalPosition += NORMAL_STEP;
    } else {
      verticalPosition -= NORMAL_STEP;
    }
  }

  verticalPosition = constrain(
    verticalPosition,
    VERTICAL_MIN,
    VERTICAL_MAX
  );

  verticalServo.write(verticalPosition);
}

// Runs the tracking checks at the selected time interval.
void trackSun() {

  if (millis() - lastTracking < TRACKING_INTERVAL) {
    return;
  }

  lastTracking = millis();

  if (lowLightMode) {
    return;
  }

  checkHorizontalMotor();
  checkVerticalMotor();

  trackHorizontal();
  trackVertical();
}

// Reads voltage and current, then calculates power and energy.
void readLoad() {

  unsigned long now = millis();

  if (ina219Available) {

    loadVoltage = ina219.getBusVoltage_V();
    loadCurrentmA = ina219.getCurrent_mA();

    loadPowermW = loadVoltage * loadCurrentmA;

  } else {

    loadVoltage = 0;
    loadCurrentmA = 0;
    loadPowermW = 0;
  }

  float elapsedHours =
    (now - lastEnergy) / 3600000.0f;

  if (ina219Available && loadPowermW > 0) {

    loadEnergyWh +=
      (loadPowermW / 1000.0f) * elapsedHours;
  }

  lastEnergy = now;
}

// Reads heartbeat messages from the Raspberry Pi and replies to PING.
void readPiCommands() {

  while (Serial1.available()) {

    char c = (char)Serial1.read();

    if (c == '\n') {

      piCommand.trim();

      if (piCommand == "PING") {

        lastPiHeartbeat = millis();
        piOnline = true;

        Serial1.println("PONG");
      }

      piCommand = "";

    } else if (c != '\r' && piCommand.length() < 32) {

      piCommand += c;
    }
  }

  if (millis() - lastPiHeartbeat > PI_TIMEOUT_MS) {
    piOnline = false;
  }
}

// Sends sensor, motor, power and connection data to the Raspberry Pi.
void sendData() {

  if (millis() - lastData < DATA_INTERVAL) {
    return;
  }

  lastData = millis();

  Serial1.print("DATA,");

  Serial1.print(millis());
  Serial1.print(",");

  Serial1.print(topValue);
  Serial1.print(",");

  Serial1.print(bottomValue);
  Serial1.print(",");

  Serial1.print(leftValue);
  Serial1.print(",");

  Serial1.print(rightValue);
  Serial1.print(",");

  Serial1.print(horizontalPosition);
  Serial1.print(",");

  Serial1.print(verticalPosition);
  Serial1.print(",");

  Serial1.print(loadVoltage, 3);
  Serial1.print(",");

  Serial1.print(loadCurrentmA, 3);
  Serial1.print(",");

  Serial1.print(loadPowermW, 3);
  Serial1.print(",");

  Serial1.print(loadEnergyWh, 6);
  Serial1.print(",");

  Serial1.print(ina219Available ? 1 : 0);
  Serial1.print(",");

  Serial1.print(ldrFaults);
  Serial1.print(",");

  Serial1.print(
    horizontalDiagnostic.suspected ? 1 : 0
  );

  Serial1.print(",");

  Serial1.print(
    verticalDiagnostic.suspected ? 1 : 0
  );

  Serial1.print(",");

  Serial1.println(piOnline ? 1 : 0);
}

// Updates the LCD with angles, power and system status.
void updateLCD() {

  if (millis() - lastLCD < LCD_INTERVAL) {
    return;
  }

  lastLCD = millis();

  lcd.setCursor(0, 0);

  lcd.print("H:");
  lcd.print(horizontalPosition);

  lcd.print(" V:");
  lcd.print(verticalPosition);

  lcd.print("       ");

  lcd.setCursor(0, 1);

  lcd.print("P:");
  lcd.print(loadPowermW, 1);
  lcd.print("mW             ");

  lcd.setCursor(0, 2);

  if (lowLightMode) {

    lcd.print("LOW LIGHT - NIGHT  ");

  } else if (ldrFaults.length() > 0) {

    lcd.print("LDR FAULT: ");
    lcd.print(ldrFaults.substring(0, 9));
    lcd.print("    ");

  } else if (horizontalDiagnostic.suspected ||
             verticalDiagnostic.suspected) {

    lcd.print("MOTOR SUSPECTED    ");

  } else {

    lcd.print("SENSORS: NORMAL    ");
  }

  lcd.setCursor(0, 3);

  if (lowLightMode) {

    lcd.print("TRACKING PAUSED    ");

  } else if (horizontalDiagnostic.suspected &&
             verticalDiagnostic.suspected) {

    lcd.print("BOTH AXES ISOLATED ");

  } else if (horizontalDiagnostic.suspected) {

    lcd.print("H MOTOR ISOLATED   ");

  } else if (verticalDiagnostic.suspected) {

    lcd.print("V MOTOR ISOLATED   ");

  } else if (!horizontalAxisAvailable()) {

    lcd.print("H AXIS ISOLATED    ");

  } else if (!verticalAxisAvailable()) {

    lcd.print("V AXIS ISOLATED    ");

  } else {

    lcd.print("TRACKING ACTIVE    ");
  }
}

// Prints sensor readings and system status to the Serial Monitor.
void printDiagnostics() {

  static unsigned long lastPrint = 0;

  if (millis() - lastPrint < 2000) {
    return;
  }

  lastPrint = millis();

  Serial.println("--------------------------------");

  Serial.print("TOP: ");
  Serial.print(topValue);

  Serial.print(" BOTTOM: ");
  Serial.print(bottomValue);

  Serial.print(" LEFT: ");
  Serial.print(leftValue);

  Serial.print(" RIGHT: ");
  Serial.println(rightValue);

  Serial.print("Horizontal angle: ");
  Serial.println(horizontalPosition);

  Serial.print("Vertical angle: ");
  Serial.println(verticalPosition);

  Serial.print("Environmental status: ");

  Serial.println(
    lowLightMode ? "LOW LIGHT - PAUSED" : "LIGHT AVAILABLE"
  );

  Serial.print("LDR faults: ");

  Serial.println(
    ldrFaults.length() ? ldrFaults : "NONE"
  );

  Serial.print("Horizontal motor: ");

  Serial.println(
    horizontalDiagnostic.suspected
      ? "SUSPECTED"
      : "NO FAULT DETECTED"
  );

  Serial.print("Vertical motor: ");

  Serial.println(
    verticalDiagnostic.suspected
      ? "SUSPECTED"
      : "NO FAULT DETECTED"
  );

  Serial.print("Raspberry Pi: ");

  Serial.println(
    piOnline ? "ONLINE" : "OFFLINE"
  );
}

// Runs once at startup and prepares the hardware.
void setup() {

  Serial.begin(115200);
  Serial1.begin(9600);

  Wire.begin();

  analogReadResolution(10);

  horizontalServo.attach(HORIZONTAL_PIN);
  verticalServo.attach(VERTICAL_PIN);

  horizontalServo.write(horizontalPosition);
  verticalServo.write(verticalPosition);

  delay(1000);

  lcd.init();
  lcd.backlight();

  lcd.setCursor(0, 0);
  lcd.print("SOLARTRACK");

  lcd.setCursor(0, 1);
  lcd.print("Initializing...");

  ina219Available = ina219.begin();

  if (!ina219Available) {
    Serial.println("WARNING: INA219 unavailable.");
  }

  unsigned long now = millis();

  lastTracking = now;
  lastData = now;
  lastLCD = now;
  lastEnergy = now;

  Serial1.println(
    "HEADER,time_ms,top,bottom,left,right,"
    "horizontal_deg,vertical_deg,load_voltage_V,"
    "load_current_mA,load_power_mW,load_energy_Wh,ina_ok,"
    "ldr_fault,horizontal_motor_suspected,vertical_motor_suspected,pi_online"
  );

  lcd.clear();

  Serial.println("SOLARTRACK READY");
  Serial.println("Raw LDR readings enabled.");
  Serial.println("Bottom calibration disabled.");
  Serial.println("Global low-light detection enabled.");
  Serial.println("Individual LDR fault detection enabled.");
  Serial.println("Software motor diagnostics enabled.");
}

// Main program: read sensors, track the sun and update the system.
void loop() {

  readPiCommands();

  readLDRs();

  updateSensorFaults();

  trackSun();

  readLoad();

  sendData();

  updateLCD();

  printDiagnostics();
}
