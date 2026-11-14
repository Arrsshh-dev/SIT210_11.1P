

#include <Wire.h>
#include <Servo.h>
#include <Adafruit_INA219.h>
#include <LiquidCrystal_I2C.h>

// ============================================
// SOLARTRACK - INTEGRATED CONTROLLER
// Arduino Nano 33 IoT
// ============================================

// -------- LDR PINS --------
const int TOP_LDR = A0;
const int BOTTOM_LDR = A1;
const int LEFT_LDR = A2;
const int RIGHT_LDR = A3;

// -------- SERVO PINS --------
const int HORIZONTAL_PIN = 9;
const int VERTICAL_PIN = 10;

Servo horizontalServo;
Servo verticalServo;

// -------- SERVO LIMITS --------
int horizontalPosition = 90;
int verticalPosition = 0;

const int HORIZONTAL_MIN = 0;
const int HORIZONTAL_MAX = 180;

const int VERTICAL_MIN = 0;
const int VERTICAL_MAX = 60;

// -------- TRACKING SETTINGS --------
const int TOLERANCE = 40;
const unsigned long TRACKING_INTERVAL = 150;

unsigned long previousTrackingTime = 0;

// -------- I2C DEVICES --------
Adafruit_INA219 ina219;
LiquidCrystal_I2C lcd(0x27, 20, 4);

bool ina219Available = false;

// -------- LDR READINGS --------
int topValue = 0;
int bottomValue = 0;
int leftValue = 0;
int rightValue = 0;

// -------- ENERGY MEASUREMENTS --------
// These measurements represent the LCD load.
float loadVoltage = 0.0;
float loadCurrentmA = 0.0;
float loadPowermW = 0.0;
float loadEnergyWh = 0.0;

unsigned long previousEnergyTime = 0;

// -------- UPDATE INTERVALS --------
const unsigned long DATA_INTERVAL = 1000;
const unsigned long LCD_INTERVAL = 1000;

unsigned long previousDataTime = 0;
unsigned long previousLCDTime = 0;


// ============================================
// SETUP
// ============================================

void setup()
{
  Serial.begin(9600);

  // Dedicated UART for Raspberry Pi
  Serial1.begin(9600);

  // I2C initialization
  Wire.begin();
  Wire.setClock(100000);

  // Nano 33 IoT ADC readings: 0-1023
  analogReadResolution(10);

  // Initialize servos
  horizontalServo.attach(HORIZONTAL_PIN);
  verticalServo.attach(VERTICAL_PIN);

  horizontalServo.write(horizontalPosition);
  verticalServo.write(verticalPosition);

  // Initialize LCD
  lcd.init();
  lcd.backlight();

  lcd.setCursor(0, 0);
  lcd.print("SOLARTRACK SYSTEM");
  lcd.setCursor(0, 1);
  lcd.print("Initializing...");

  // Initialize INA219
  ina219Available = ina219.begin();

  if (ina219Available)
  {
    Serial.println("INA219 detected at 0x40");
  }
  else
  {
    Serial.println("WARNING: INA219 not detected");
  }

  delay(1500);
  lcd.clear();

  unsigned long now = millis();

  previousTrackingTime = now;
  previousEnergyTime = now;
  previousDataTime = now;
  previousLCDTime = now;

  // CSV header for Raspberry Pi
  Serial1.println(
    "HEADER,time_ms,top,bottom,left,right,"
    "horizontal_deg,vertical_deg,load_voltage_V,"
    "load_current_mA,load_power_mW,load_energy_Wh,ina_ok"
  );

  Serial.println("SOLARTRACK READY");
}


// ============================================
// READ FOUR LDR SENSORS
// ============================================

void readLDRs()
{
  topValue = analogRead(TOP_LDR);
  bottomValue = analogRead(BOTTOM_LDR);

  leftValue = analogRead(LEFT_LDR);
  rightValue = analogRead(RIGHT_LDR);
}


// ============================================
// SOLAR TRACKING
// ============================================

void trackSun()
{
  unsigned long now = millis();

  if (now - previousTrackingTime < TRACKING_INTERVAL)
    return;

  previousTrackingTime = now;

  int horizontalError = leftValue - rightValue;
  int verticalError = topValue - bottomValue;

  // Horizontal tracking
  // Direction retained from your previous working setup.

  if (horizontalError > TOLERANCE)
  {
    horizontalPosition--;
  }
  else if (horizontalError < -TOLERANCE)
  {
    horizontalPosition++;
  }

  horizontalPosition = constrain(
    horizontalPosition,
    HORIZONTAL_MIN,
    HORIZONTAL_MAX
  );

  // Vertical tracking

  if (verticalError > TOLERANCE)
  {
    verticalPosition++;
  }
  else if (verticalError < -TOLERANCE)
  {
    verticalPosition--;
  }

  // Mechanical safety restriction: 0-60 degrees
  verticalPosition = constrain(
    verticalPosition,
    VERTICAL_MIN,
    VERTICAL_MAX
  );

  horizontalServo.write(horizontalPosition);
  verticalServo.write(verticalPosition);
}


// ============================================
// READ INA219 AND CALCULATE ENERGY
// ============================================

void readLoad()
{
  unsigned long now = millis();

  if (ina219Available)
  {
    loadVoltage = ina219.getBusVoltage_V();
    loadCurrentmA = ina219.getCurrent_mA();

    loadPowermW = loadVoltage * loadCurrentmA;
  }
  else
  {
    loadVoltage = 0;
    loadCurrentmA = 0;
    loadPowermW = 0;
  }

  float elapsedHours =
    (now - previousEnergyTime) / 3600000.0;

  // Accumulate only positive measured load energy.
  if (ina219Available && loadPowermW > 0)
  {
    loadEnergyWh +=
      (loadPowermW / 1000.0) * elapsedHours;
  }

  previousEnergyTime = now;
}


// ============================================
// SEND TELEMETRY TO RASPBERRY PI
// ============================================

void sendData()
{
  unsigned long now = millis();

  if (now - previousDataTime < DATA_INTERVAL)
    return;

  previousDataTime = now;

  // One comma-separated DATA record.
  Serial1.print("DATA,");

  Serial1.print(now);
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

  Serial1.println(ina219Available ? 1 : 0);

  // USB debugging output
  Serial.print("LDR: ");
  Serial.print(topValue);
  Serial.print(",");
  Serial.print(bottomValue);
  Serial.print(",");
  Serial.print(leftValue);
  Serial.print(",");
  Serial.print(rightValue);

  Serial.print(" | Servo: ");
  Serial.print(horizontalPosition);
  Serial.print(",");
  Serial.print(verticalPosition);

  Serial.print(" | Load power: ");
  Serial.print(loadPowermW, 2);
  Serial.println(" mW");
}


// ============================================
// UPDATE LCD
// ============================================

void updateLCD()
{
  unsigned long now = millis();

  if (now - previousLCDTime < LCD_INTERVAL)
    return;

  previousLCDTime = now;

  lcd.setCursor(0, 0);
  lcd.print("H:");
  lcd.print(horizontalPosition);
  lcd.print(" V:");
  lcd.print(verticalPosition);
  lcd.print("          ");

  lcd.setCursor(0, 1);
  lcd.print("V: ");
  lcd.print(loadVoltage, 2);
  lcd.print(" V          ");

  lcd.setCursor(0, 2);
  lcd.print("I: ");
  lcd.print(loadCurrentmA, 1);
  lcd.print(" mA         ");

  lcd.setCursor(0, 3);
  lcd.print("P: ");
  lcd.print(loadPowermW, 1);
  lcd.print(" mW         ");
}


// ============================================
// MAIN LOOP
// ============================================

void loop()
{
  readLDRs();

  trackSun();

  readLoad();

  sendData();

  updateLCD();
}
