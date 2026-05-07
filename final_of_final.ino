#include <WiFi.h>
#include <HTTPClient.h>
#include <DIYables_MiniMp3.h>
#include <ESP32Servo.h>

// ─── 1. THÔNG SỐ KẾT NỐI ─────────────────────────────────────────────
#define WIFI_SSID     "OTOHOANHAO"
#define WIFI_PASSWORD "123123123"
#define FIREBASE_HOST "smartparkingsystem-1e749-default-rtdb.asia-southeast1.firebasedatabase.app"
#define FIREBASE_AUTH "ZR9iivMINUuuCCxkGYQBjEOdNLaNaNinNalrO1vN"

// ─── 2. ĐƯỜNG DẪN FIREBASE ───────────────────────────────────────────
#define PATH_ENTRY_PLATE   "/ParkingSystem/GateControl/EntryGate/plate"
#define PATH_EXIT_PLATE    "/ParkingSystem/GateControl/ExitGate/plate"
#define PATH_EXIT_SERVO    "/ParkingSystem/GateControl/ExitGate/servo"
#define PATH_SLOTS         "/ParkingSystem/Sensors/Slots"
#define PATH_FLAME_ALERT   "/ParkingSystem/Sensors/Environment/flame_alert"
#define PATH_STATUS        "/ParkingSystem/CurrentVehicle/status"
#define PATH_FIRE1         "/ParkingSystem/Sensors/Environment/fire1"
#define PATH_FIRE2         "/ParkingSystem/Sensors/Environment/fire2"

// ─── 3. CẤU HÌNH PHẦN CỨNG ────────────────────────────────────────────
#define PIN_SERVO    13
#define PIN_FLAME_1  34
#define PIN_FLAME_2  35

DIYables_MiniMp3 mp3;
Servo barrier;
bool isReady = false;

// ─── 4. TRA CỨU CHỮ CÁI ──────────────────────────────────────────────
int getFileForLetter(char c) {
    switch (toupper(c)) {
        case 'A': return 20; case 'B': return 21; case 'C': return 22;
        case 'D': return 23; case 'E': return 24; case 'G': return 25;
        case 'H': return 26; case 'K': return 27; case 'L': return 28;
        case 'M': return 29; case 'N': return 30; case 'P': return 31;
        case 'S': return 32; case 'T': return 33; case 'U': return 34;
        case 'V': return 35; case 'X': return 36; case 'Y': return 37;
        case 'F': return 38; default: return -1;
    }
}

// ─── 5. HÀM FIREBASE (HTTP REST) ─────────────────────────────────────
String fbGet(String path) {
    HTTPClient http;
    String url = "https://" + String(FIREBASE_HOST) + path + ".json?auth=" + String(FIREBASE_AUTH);
    http.begin(url);
    String payload = "null";
    if (http.GET() > 0) payload = http.getString();
    http.end();
    payload.trim();
    payload.replace("\"", "");
    return payload;
}

void fbPutString(String path, String value) {
    HTTPClient http;
    String url = "https://" + String(FIREBASE_HOST) + path + ".json?auth=" + String(FIREBASE_AUTH);
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    http.PUT("\"" + value + "\"");
    http.end();
}

void fbPutBool(String path, bool value) {
    HTTPClient http;
    String url = "https://" + String(FIREBASE_HOST) + path + ".json?auth=" + String(FIREBASE_AUTH);
    http.begin(url);
    http.PUT(value ? "true" : "false");
    http.end();
}

// Hàm ghi giá trị số (cho fire1, fire2)
void fbPutInt(String path, int value) {
    HTTPClient http;
    String url = "https://" + String(FIREBASE_HOST) + path + ".json?auth=" + String(FIREBASE_AUTH);
    http.begin(url);
    http.PUT(String(value));
    http.end();
}

// ─── 6. HÀM TIỆN ÍCH MP3 & BIỂN SỐ ───────────────────────────────────
void playMp3(int fileNum, String desc) {
    Serial.printf("[MP3-LOG] Phát file: %04d | %s\n", fileNum, desc.c_str());
    mp3.play(fileNum);
}

bool isValidPlate(String plate) {
    plate.trim();
    return !(plate == "---" || plate == "none" || plate == "null" || plate.length() < 4);
}

void phatAmThanhBienSo(String plate) {
    if (!isValidPlate(plate)) return;
    for (int i = 0; i < (int)plate.length(); i++) {
        char c = plate[i];
        if (c == '-' || c == '.' || c == ' ') continue;
        int file = isdigit(c) ? (10 + (c - '0')) : getFileForLetter(c);
        if (file != -1) { playMp3(file, String(c)); delay(1000); }
    }
}

// ─── 7. THÔNG BÁO Ô TRỐNG ────────────────────────────────────────────
void thongBaoOTrong() {
    int emptySlots[4];
    int count = 0;
    for (int i = 1; i <= 4; i++) {
        String s = fbGet(String(PATH_SLOTS) + "/slot" + String(i));
        if (s == "0" || s == "false") emptySlots[count++] = i;
    }
    if (count == 0) {
        playMp3(4, "Bãi đầy"); delay(2500);
    } else {
        playMp3(3, "Ô trống là"); delay(2500);
        for (int i = 0; i < count; i++) {
            playMp3(10 + emptySlots[i], "Số " + String(emptySlots[i]));
            delay(1200);
            if (count > 1 && i == count - 2) { playMp3(7, "và"); delay(1000); }
        }
    }
}

// ─── 8. SETUP ────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    Serial2.begin(9600, SERIAL_8N1, 16, 17);

    barrier.write(0); 
    barrier.attach(PIN_SERVO, 500, 2400);
    delay(500); 

    pinMode(PIN_FLAME_1, INPUT); pinMode(PIN_FLAME_2, INPUT);
    delay(2000);
    mp3.begin(Serial2); mp3.setVolume(30);

    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }

    fbPutString(PATH_ENTRY_PLATE, "---");
    fbPutString(PATH_EXIT_PLATE,  "---");
    fbPutString(PATH_EXIT_SERVO,  "close");
    fbPutBool(PATH_FLAME_ALERT,   false);
    fbPutInt(PATH_FIRE1, 0);
    fbPutInt(PATH_FIRE2, 0);

    playMp3(8, "Startup"); delay(5000); 
    isReady = true;
    Serial.println("\n>>> NAM MINH SAN SANG.");
}

// ─── 9. LOOP ─────────────────────────────────────────────────────────
void loop() {
    if (!isReady) return;

    // --- A. BÁO CHÁY (Cải tiến Reset tự động) ---
    bool coChay = (digitalRead(PIN_FLAME_1) == LOW || digitalRead(PIN_FLAME_2) == LOW);
    static bool fireStateOnFirebase = false; // Theo dõi trạng thái trên Firebase

    if (coChay) {
        barrier.write(90); // Mở cổng ngay lập tức
        if (!fireStateOnFirebase) { // Chỉ cập nhật một lần khi bắt đầu cháy
            fireStateOnFirebase = true;
            fbPutBool(PATH_FLAME_ALERT, true);
            fbPutInt(PATH_FIRE1, 1);
            fbPutInt(PATH_FIRE2, 1);
            playMp3(5, "Báo cháy");
            Serial.println("[CRITICAL] PHÁT HIỆN CHÁY - Đã báo lên Firebase.");
        }
        delay(1000); // Delay nhỏ để ổn định vòng lặp khi đang cháy
        return; 
    } 
    else {
        // Nếu trước đó đang cháy mà giờ hết cháy -> Reset về FALSE
        if (fireStateOnFirebase) {
            fireStateOnFirebase = false;
            fbPutBool(PATH_FLAME_ALERT, false);
            fbPutInt(PATH_FIRE1, 0);
            fbPutInt(PATH_FIRE2, 0);
            barrier.write(0); // Đóng Barie lại
            Serial.println("[SAFE] ĐÃ HẾT CHÁY - Đã Reset Firebase.");
        }
    }

    // --- CÁC LOGIC KHÁC ---
    static unsigned long lastCheck = 0;
    if (millis() - lastCheck < 3000) return; 
    lastCheck = millis();

    String pOut = fbGet(PATH_EXIT_PLATE);
    String pIn  = fbGet(PATH_ENTRY_PLATE);
    String exCmd = fbGet(PATH_EXIT_SERVO);
    String curStatus = fbGet(PATH_STATUS);

    Serial.printf("[LOG] In:[%s] Out:[%s] Status:[%s]\n", pIn.c_str(), pOut.c_str(), curStatus.c_str());

    if (exCmd == "open") { barrier.write(90); return; }

    if (isValidPlate(pOut)) {
        if (curStatus == "3") { 
            Serial.println("[SUCCESS] Thanh toán OK -> Chỉ phát Tạm biệt & Mở Barie");
            playMp3(6, "Chào tạm biệt"); 
            barrier.write(90); 
            delay(6000); 
            barrier.write(0); 
            fbPutString(PATH_EXIT_PLATE, "---");
            fbPutString(PATH_ENTRY_PLATE, "---");
        }
        return;
    }

    if (isValidPlate(pIn)) {
        playMp3(1, "Chào mừng"); delay(2500);
        playMp3(2, "Xe có biển số"); delay(2000);
        phatAmThanhBienSo(pIn);
        thongBaoOTrong();
        fbPutString(PATH_ENTRY_PLATE, "---");
        return;
    }

    if (exCmd == "close" || exCmd == "---" || exCmd == "null") { barrier.write(0); }
}