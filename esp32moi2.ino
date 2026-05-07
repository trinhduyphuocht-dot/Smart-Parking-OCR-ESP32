#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <ESP32Servo.h>
#include <WiFi.h>
#include <BH1750.h>
#include <FirebaseESP32.h>

// --- 1. CẤU HÌNH ---
#define FIREBASE_HOST "smartparkingsystem-1e749-default-rtdb.asia-southeast1.firebasedatabase.app"
#define FIREBASE_AUTH "ZR9iivMINUuuCCxkGYQBjEOdNLaNaNinNalrO1vN"
const char* ssid = "Duybucu";
const char* password = "01234567899";

// PINOUT
#define IR_GATE_IN 15
#define IR_GATE_OUT 4
#define SERVO_IN 13
#define BUZZER 23

// ĐƯỜNG DẪN JSON
#define PATH_SLOTS        "/ParkingSystem/Sensors/Slots"
#define PATH_ENV          "/ParkingSystem/Sensors/Environment"
#define PATH_FIRE1        "/ParkingSystem/Sensors/Environment/fire1"
#define PATH_FIRE2        "/ParkingSystem/Sensors/Environment/fire2"
#define PATH_PLATE        "/ParkingSystem/GateControl/EntryGate/plate"
#define PATH_ENTRY_SERVO  "/ParkingSystem/GateControl/EntryGate/servo"
#define PATH_EXIT_SERVO   "/ParkingSystem/GateControl/ExitGate/servo"

LiquidCrystal_I2C lcd(0x27, 16, 2);
BH1750 lightMeter;
Servo barrierIn;
FirebaseData fbdo, fbdo_f;
FirebaseAuth auth;
FirebaseConfig config;

enum State { IDLE, SHOW_INFO, WAIT_CAR, DONE };
State currentState = IDLE;
String serverPlate = "---";
String lastEntryManual = "close", lastExitManual = "close"; 

unsigned long lastFirebaseUpdate = 0;
bool lastIrOutState = HIGH; 
unsigned long gateOpenTime = 0; 

// --- HÀM TIỆN ÍCH ---
void beep(int count, int duration) {
    for (int i = 0; i < count; i++) {
        digitalWrite(BUZZER, HIGH); delay(duration);
        digitalWrite(BUZZER, LOW); if (i < count - 1) delay(duration);
    }
}

void printLCD(int col, int row, String text) {
    lcd.setCursor(col, row);
    lcd.print(text);
    for (int i = text.length(); i < 16; i++) lcd.print(" ");
}

void setup() {
    Serial.begin(115200);
    // Chân cảm biến 4 slot
    pinMode(14, INPUT); pinMode(27, INPUT); pinMode(26, INPUT); pinMode(33, INPUT);
    pinMode(IR_GATE_IN, INPUT); pinMode(IR_GATE_OUT, INPUT); 
    pinMode(BUZZER, OUTPUT); digitalWrite(BUZZER, LOW);
    pinMode(12, OUTPUT); pinMode(18, OUTPUT);

    // Ép Servo về 0 rồi mới attach để tránh nhảy Barie
    barrierIn.write(0); 
    barrierIn.attach(SERVO_IN, 500, 2400);

    Wire.begin(21, 22); lcd.init(); lcd.backlight();
    lightMeter.begin();
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) delay(500);
    
    config.host = FIREBASE_HOST; config.signer.tokens.legacy_token = FIREBASE_AUTH;
    Firebase.begin(&config, &auth);
    Serial.println("[NODE 2] OK!");
}

void loop() {
    // --- 1. BÁO CHÁY KHẨN CẤP (SIREN) ---
    int f1 = 0, f2 = 0;
    if (Firebase.getInt(fbdo_f, PATH_FIRE1)) f1 = fbdo_f.intData();
    if (Firebase.getInt(fbdo_f, PATH_FIRE2)) f2 = fbdo_f.intData();

    if (f1 > 0 || f2 > 0) {
        digitalWrite(BUZZER, HIGH); delay(50); // Hú siêu nhanh
        digitalWrite(BUZZER, LOW); delay(50);
printLCD(0, 0, "!!! FIRE !!!"); printLCD(0, 1, "   EMERGENCY");
        return; 
    }

    // --- 2. CÒI CHO MỞ/ĐÓNG THỦ CÔNG (CẢ 2 CỔNG) ---
    static unsigned long lastCheckManual = 0;
    if (millis() - lastCheckManual > 500) {
        lastCheckManual = millis();
        // Cổng vào thủ công
        if (Firebase.getString(fbdo, PATH_ENTRY_SERVO)) {
            String sIn = fbdo.stringData();
            if (sIn != lastEntryManual) { beep(1, 200); lastEntryManual = sIn; }
        }
        // Cổng ra thủ công
        if (Firebase.getString(fbdo, PATH_EXIT_SERVO)) {
            String sOut = fbdo.stringData();
            if (sOut != lastExitManual) { beep(1, 200); lastExitManual = sOut; }
        }
    }

    // --- 3. LOGIC CỔNG VÀO & HIỂN THỊ SLOT (KHÔI PHỤC GỐC) ---
    float lux = lightMeter.readLightLevel();
    digitalWrite(12, (lux < 50) ? HIGH : LOW);
    digitalWrite(18, (lux < 50) ? HIGH : LOW);

    if (lastEntryManual == "open") {
        barrierIn.write(90);
        printLCD(0, 0, "  MANUAL OPEN"); printLCD(0, 1, "   GATE OPEN");
    } else {
        // Tự động
        int free = digitalRead(14) + digitalRead(27) + digitalRead(26) + digitalRead(33);
        
        switch (currentState) {
            case IDLE:
                // KHÔI PHỤC HIỂN THỊ SLOT CỦA BẠN
                if (free <= 0) {
                    printLCD(0, 0, "   FULL SLOT!");
                    printLCD(0, 1, " NO PARKING NOW");
                } else {
                    printLCD(0, 0, "S1:" + String(digitalRead(14)?"V":"X") + " S2:" + String(digitalRead(27)?"V":"X"));
                    printLCD(0, 1, "S3:" + String(digitalRead(26)?"V":"X") + " S4:" + String(digitalRead(33)?"V":"X"));
                }
                barrierIn.write(0);
                
                // Kiểm tra biển số để vào
                if (Firebase.getString(fbdo, PATH_PLATE)) {
                    String p = fbdo.stringData();
                    if (p != "---" && p != "") { serverPlate = p; currentState = SHOW_INFO; }
                }
                break;

            case SHOW_INFO:
                printLCD(0, 0, "MOI VAO:"); printLCD(0, 1, serverPlate);
                beep(2, 100); 
                barrierIn.write(90); // Mở Servo vào
                gateOpenTime = millis();
                currentState = WAIT_CAR;
                break;

            case WAIT_CAR:
                if (millis() - gateOpenTime > 5000) {
                    barrierIn.write(0); // Đóng Servo vào
                    beep(1, 200);
                    currentState = DONE;
                } break;

            case DONE:
                Firebase.setString(fbdo, PATH_PLATE, "---");
                currentState = IDLE;
                break;
        }
    }

    // --- 4. CẢM BIẾN VẬT LÝ XE RA (CÒI) ---
    bool irOut = digitalRead(IR_GATE_OUT);
    if (irOut == LOW && lastIrOutState == HIGH) { beep(1, 400); }
lastIrOutState = irOut;

    // --- 5. GỬI DỮ LIỆU CẢM BIẾN ---
    if (millis() - lastFirebaseUpdate > 3000) {
        lastFirebaseUpdate = millis();
        Firebase.setBool(fbdo, String(PATH_SLOTS) + "/slot1", !digitalRead(14));
        Firebase.setBool(fbdo, String(PATH_SLOTS) + "/slot2", !digitalRead(27));
        Firebase.setBool(fbdo, String(PATH_SLOTS) + "/slot3", !digitalRead(26));
        Firebase.setBool(fbdo, String(PATH_SLOTS) + "/slot4", !digitalRead(33));
        Firebase.setFloat(fbdo, String(PATH_ENV) + "/lux", lux);
    }
}
