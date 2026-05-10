#include "CytronMotorDriver.h"
#include <SoftwareSerial.h>

// ── TF-Luna UART ──────────────────────────────────────────
SoftwareSerial luna(12, 13);

// ── Motor setup ───────────────────────────────────────────
CytronMD motor1(PWM_DIR, 5, 8);
CytronMD motor2(PWM_DIR, 6, 9);

// ── Joystick ──────────────────────────────────────────────
#define VRX      A0
#define VRY      A1
#define CENTER   512
#define DEADZONE 60

// ── Ultrasonic ────────────────────────────────────────────
#define trigL 2
#define echoL 3
#define trigR 4
#define echoR 7
#define trigB 10
#define echoB 11

// ── Settings ──────────────────────────────────────────────
#define MOTOR_SPEED    180
#define STOP_DIST       30
// ✅ Increased timeout — Pi sends every 150ms so 1000ms is safe
#define CMD_TIMEOUT   1000

// ── State ─────────────────────────────────────────────────
char          currentCmd    = 'S';
bool          piControl     = false;
unsigned long lastCmdTime   = 0;
int           lastValidDist = 800;

// ── TF-Luna ───────────────────────────────────────────────
int getTFLunaDistance()
{
  unsigned long start = millis();
  while (true)
  {
    if (millis() - start > 150) return lastValidDist; // ✅ reduced from 200ms
    if (!luna.available()) continue;
    if (luna.read() != 0x59) continue;

    unsigned long t2 = millis();
    while (!luna.available()) {
      if (millis() - t2 > 30) return lastValidDist; // ✅ reduced from 50ms
    }
    if (luna.read() != 0x59) continue;

    byte data[7];
    for (int i = 0; i < 7; i++) {
      unsigned long t3 = millis();
      while (!luna.available()) {
        if (millis() - t3 > 30) return lastValidDist;
      }
      data[i] = luna.read();
    }

    int dist = data[0] | (data[1] << 8);
    if (dist <= 0 || dist > 800) return lastValidDist;
    lastValidDist = dist;
    return dist;
  }
}

// ── Ultrasonic ────────────────────────────────────────────
long getDistance(int trig, int echo)
{
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  long duration = pulseIn(echo, HIGH, 30000);
  long dist = duration * 0.034 / 2;
  if (dist <= 0 || dist > 200) dist = 200;
  return dist;
}

// ── Motor commands ────────────────────────────────────────
void moveForward()  {
  motor1.setSpeed(MOTOR_SPEED);
  motor2.setSpeed(MOTOR_SPEED);
}
void moveBackward() {
  motor1.setSpeed(-MOTOR_SPEED);
  motor2.setSpeed(-MOTOR_SPEED);
}
void turnLeft()     {
  motor1.setSpeed(-MOTOR_SPEED);
  motor2.setSpeed(MOTOR_SPEED);
}
void turnRight()    {
  motor1.setSpeed(MOTOR_SPEED);
  motor2.setSpeed(-MOTOR_SPEED);
}
void stopMotors()   {
  motor1.setSpeed(0);
  motor2.setSpeed(0);
  currentCmd = 'S';
  piControl  = false;
}

void setup()
{
  Serial.begin(9600);
  luna.begin(115200);
  delay(1000);

  pinMode(trigL, OUTPUT); pinMode(echoL, INPUT);
  pinMode(trigR, OUTPUT); pinMode(echoR, INPUT);
  pinMode(trigB, OUTPUT); pinMode(echoB, INPUT);

  stopMotors();
  Serial.println("Wheelchair Ready!");
}

void loop()
{
  // ── ✅ Drain ALL pending serial bytes ─────────────────
  // Pi sends every 150ms — multiple bytes may be queued.
  // Read ALL of them and use only the LAST valid one.
  char lastValidCmd = 0;
  while (Serial.available() > 0)
  {
    char cmd = Serial.read();
    if (cmd == '\n' || cmd == '\r' || cmd == ' ') continue;
    if (cmd == 'F' || cmd == 'B' || cmd == 'L' ||
        cmd == 'R' || cmd == 'S' || cmd == 'J')
    {
      lastValidCmd = cmd;
    }
  }

  // Process the last valid command received
  if (lastValidCmd != 0)
  {
    if (lastValidCmd == 'J') {
      piControl = false;
      Serial.println("Joystick mode");
    }
    else if (lastValidCmd == 'S') {
      stopMotors();
      piControl = false;
      Serial.println("STOP received");
    }
    else {
      currentCmd  = lastValidCmd;
      lastCmdTime = millis();  // ✅ reset timeout
      piControl   = true;
      Serial.print("CMD: "); Serial.println(lastValidCmd);
    }
  }

  // ── Auto stop timeout ─────────────────────────────────
  if (piControl && (millis() - lastCmdTime) > CMD_TIMEOUT)
  {
    stopMotors();
    Serial.println("TIMEOUT");
    return;
  }

  // ── Read sensors ──────────────────────────────────────
  int  frontDist = getTFLunaDistance();
  long leftDist  = getDistance(trigL, echoL);
  long rightDist = getDistance(trigR, echoR);
  long backDist  = getDistance(trigB, echoB);

  // ── Print sensors every 1000ms only ──────────────────
  static unsigned long lastPrint = 0;
  if (millis() - lastPrint > 1000) {
    Serial.print("F:"); Serial.print(frontDist);
    Serial.print(" L:"); Serial.print(leftDist);
    Serial.print(" R:"); Serial.print(rightDist);
    Serial.print(" B:"); Serial.println(backDist);
    lastPrint = millis();
  }

  // ── Obstacle checks ───────────────────────────────────
  if (currentCmd == 'F' && frontDist < STOP_DIST) {
    stopMotors(); Serial.println("BLOCKED:Ahead"); return;
  }
  if (currentCmd == 'B' && backDist < STOP_DIST) {
    stopMotors(); Serial.println("BLOCKED:Behind"); return;
  }
  if (currentCmd == 'L' && leftDist < STOP_DIST) {
    stopMotors(); Serial.println("BLOCKED:Left"); return;
  }
  if (currentCmd == 'R' && rightDist < STOP_DIST) {
    stopMotors(); Serial.println("BLOCKED:Right"); return;
  }

  // ── Execute command ───────────────────────────────────
  if (piControl)
  {
    if      (currentCmd == 'F') moveForward();
    else if (currentCmd == 'B') moveBackward();
    else if (currentCmd == 'L') turnLeft();
    else if (currentCmd == 'R') turnRight();
    return;
  }

  // ── Joystick fallback ─────────────────────────────────
  int rawX = analogRead(VRX) - CENTER;
  int rawY = analogRead(VRY) - CENTER;
  int x    = (abs(rawX) < DEADZONE) ? 0 : rawX;
  int y    = (abs(rawY) < DEADZONE) ? 0 : rawY;

  int leftSpeed  = constrain(
      map(y+x, -512, 512, -255, 255), -255, 255);
  int rightSpeed = constrain(
      map(y-x, -512, 512, -255, 255), -255, 255);

  if (y > 0 && frontDist < STOP_DIST) { stopMotors(); return; }
  if (y < 0 && backDist  < STOP_DIST) { stopMotors(); return; }
  if (x < 0 && leftDist  < STOP_DIST) { stopMotors(); return; }
  if (x > 0 && rightDist < STOP_DIST) { stopMotors(); return; }

  motor1.setSpeed(leftSpeed);
  motor2.setSpeed(rightSpeed);
}