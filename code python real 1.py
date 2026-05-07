import warnings
warnings.filterwarnings("ignore", category=UserWarning)

import cv2
import easyocr
import numpy as np
import threading
import time
import collections
import queue
import tkinter as tk
from tkinter import font
from PIL import Image, ImageTk
from datetime import datetime
import os
import math
import requests

# --- THÊM THƯ VIỆN FIREBASE & FLASK ---
import firebase_admin
from firebase_admin import credentials
from firebase_admin import db
from flask import Flask, request, jsonify
import logging

# --- CẤU HÌNH CAMERA & SERVER WEB ---
URL_CAM_VAO = "http://172.20.10.12:81/stream"
URL_CAM_RA = "http://172.20.10.11:81/stream"

# Địa chỉ Web Server (Local)
WEB_SERVER_IN = "http://172.20.10.9:5000/car_in"
WEB_SERVER_OUT = "http://172.20.10.9:5000/car_out"

# =========================================================
# 1. KHỞI TẠO FIREBASE & FLASK SERVER (CHẠY NGẦM DƯỚI NỀN)
# =========================================================
print("Đang kết nối Firebase...")
try:
    if not firebase_admin._apps:
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred, {
            'databaseURL': 'https://smartparkingsystem-1e749-default-rtdb.asia-southeast1.firebasedatabase.app/'
        })
    print("KẾT NỐI FIREBASE THÀNH CÔNG!")
except Exception as e:
    print(f"Lỗi khởi tạo Firebase: {e}")

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
flask_app = Flask(__name__)

# Tích hợp nhận diện cảm biến Slot từ ESP32
@flask_app.route('/get_action', methods=['GET'])
def get_action():
    try:
        s1 = request.args.get('s1')
        s2 = request.args.get('s2')
        s3 = request.args.get('s3')
        s4 = request.args.get('s4')
        if s1 is not None:
            db.reference('ParkingSystem/Sensors/Slots').update({
                'slot1': int(s1), 'slot2': int(s2), 'slot3': int(s3), 'slot4': int(s4)
            })

        action = db.reference('ParkingSystem/GateControl/action').get()
        if not action: action = "none"
        plate = db.reference('ParkingSystem/CurrentVehicle/license_plate').get()
        if not plate: plate = ""

        if action != "none":
            db.reference('ParkingSystem/GateControl/action').set("none")

        return jsonify({"action": action, "plate": plate})
    except Exception as e:
        return jsonify({"action": "none", "plate": ""})

# --- WEBHOOK (XỬ LÝ THANH TOÁN) ---
@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        content = data.get('content', '').upper()
        print(f"\n[WEBHOOK] Nhận tín hiệu tiền về! Nội dung CK: {content}")

        current_vehicle = db.reference('ParkingSystem/CurrentVehicle').get()
        if current_vehicle:
            plate_waiting = current_vehicle.get('license_plate', '').replace("-", "").replace(" ", "").upper()
            if plate_waiting and plate_waiting in content:
                print(f"--- KHỚP BIỂN SỐ {plate_waiting}! TỰ ĐỘNG BÁO APP & MỞ CỔNG ---")
                
                # Cập nhật status lên 3 để App Android nhảy màn hình "Thanh toán thành công"
                db.reference('ParkingSystem/CurrentVehicle/status').set(3)
                
                # Đẩy biển số lên ESP32 Cổng Ra để tự động mở Barie
                db.reference('ParkingSystem/GateControl/ExitGate').update({'plate': plate_waiting})
                
                return "SUCCESS", 200
        return "NOT_MATCH", 200
    except Exception as e:
        print(f"[WEBHOOK] Lỗi xử lý: {e}")
        return "ERROR", 500

def run_flask_server():
    print("Đang bật API Webhook ở cổng 5000...")
    flask_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# =========================================================
# 2. HỆ THỐNG GIAO DIỆN & AI CAMERA CỐT LÕI
# =========================================================
class SmartParkingSystem:
    def calculate_fee(self, time_in_unix, time_out_unix):
        minutes = (time_out_unix - time_in_unix) / 60.0
        if minutes <= 0: return 30000

        total_hours = math.ceil(minutes / 60.0)
        if total_hours == 0: total_hours = 1

        days = total_hours // 24
        rem_h = total_hours % 24
        fee = days * 300000 

        if rem_h > 0:
            if rem_h <= 4: day_fee = rem_h * 30000
            elif rem_h <= 5: day_fee = 120000
            else: 
                day_fee = 120000 + (rem_h - 5) * 30000 
                if day_fee > 300000: day_fee = 300000
            fee += day_fee
        return int(fee)

    def push_to_web_server(self, url, payload):
        try:
            res = requests.post(url, json=payload, timeout=2)
            print(f"[WEB LOCAL] Bắn thành công! Phản hồi: {res.text}")
        except Exception as e:
            print(f"[WEB LOCAL] Bắn xịt (Máy Local chưa bật Web hoặc sai link)")

    def push_to_firebase_vao(self, plate, current_unix_time, current_time_str):
        try:
            db.reference('ParkingSystem/History').child(plate).set({
                'license_plate': plate, 'time_in': current_unix_time, 'date_in': current_time_str,      
                'time_out': 0, 'date_out': "---", 'total_amount': 0
            })
            
            db.reference('ParkingSystem/GateControl/EntryGate').update({'plate': plate, 'time_in': current_time_str})
            
            self.ui_queue.put({"type": "update_bill", "time_in": current_time_str, "time_out": "...", "amount": "0 VNĐ"})
            print(f"[FIREBASE] Đã ghi xe VÀO: {plate}")
        except Exception as e:
            print(f"[FIREBASE] Lỗi ghi xe VÀO: {e}")

    def push_to_firebase_ra(self, plate, current_unix_time, current_time_str):
        try:
            # --- LỚP BẢO VỆ: CHỐNG GHI ĐÈ KHI ĐANG THANH TOÁN ---
            current_veh = db.reference('ParkingSystem/CurrentVehicle').get()
            if current_veh:
                db_plate = str(current_veh.get('license_plate', '')).replace("-", "").replace(" ", "").upper()
                cam_plate = plate.replace("-", "").replace(" ", "").upper()
                
                if db_plate == cam_plate:
                    st = str(current_veh.get('status', '0'))
                    if st in ['2', '3']:
                        print(f"[AI CAMERA] Xe {plate} đang bận (Status {st}). Không ghi đè Firebase!")
                        return
            
            # --- TÍNH TIỀN CHO XE MỚI RA ---
            history_data = db.reference('ParkingSystem/History').child(plate).get()
            if history_data and 'time_in' in history_data:
                time_in = history_data['time_in']
                time_in_str = history_data.get('date_in', "Không rõ")
                amount = self.calculate_fee(time_in, current_unix_time)
            else:
                time_in_str = "Không có dữ liệu vào"
                amount = 30000 
                
            db.reference('ParkingSystem/History').child(plate).update({
                'time_out': current_unix_time, 'date_out': current_time_str, 'total_amount': amount
            })
            db.reference('ParkingSystem/GateControl/ExitGate').update({'plate': plate, 'time_out': current_time_str})
            db.reference('ParkingSystem/CurrentVehicle').update({
                'license_plate': plate, 'amount': amount, 'status': 2, 'date_in': time_in_str, 'date_out': current_time_str
            })
            
            self.ui_queue.put({"type": "update_bill", "time_in": time_in_str, "time_out": current_time_str, "amount": f"{amount:,} VNĐ"})
            print(f"[FIREBASE] Đã ghi xe RA và báo APP: {plate}")
        except Exception as e:
            print(f"[FIREBASE] Lỗi ghi xe RA: {e}")

    def send_to_server(self, plate, cam_type):
        if plate == self.last_sent_plate and cam_type == "VAO": return
        self.last_sent_plate = plate
        
        current_unix_time = int(time.time())
        current_time_str = datetime.fromtimestamp(current_unix_time).strftime("%H:%M:%S %d/%m/%Y")

        if cam_type == "VAO":
            threading.Thread(target=self.push_to_web_server, args=(WEB_SERVER_IN, {"plate": plate}), daemon=True).start()
            threading.Thread(target=self.push_to_firebase_vao, args=(plate, current_unix_time, current_time_str), daemon=True).start()

        elif cam_type == "RA":
            threading.Thread(target=self.push_to_web_server, args=(WEB_SERVER_OUT, {"plate": plate}), daemon=True).start()
            threading.Thread(target=self.push_to_firebase_ra, args=(plate, current_unix_time, current_time_str), daemon=True).start()
            
    def __init__(self, root):
        self.last_sent_plate = ""
        self.root = root
        self.root.title("HỆ THỐNG QUẢN LÝ BÃI ĐỖ XE THÔNG MINH")
        self.root.geometry("1250x780") 
        self.root.configure(bg="#f0f2f5")

        title_font = font.Font(family="Helvetica", size=22, weight="bold")
        cam_title_font = font.Font(family="Helvetica", size=12, weight="bold")
        self.plate_big_font = font.Font(family="Helvetica", size=45, weight="bold")
        self.plate_small_font = font.Font(family="Helvetica", size=14, weight="bold")
        info_font = font.Font(family="Helvetica", size=12, weight="bold")

        self.default_bg = ImageTk.PhotoImage(Image.new('RGB', (400, 300), color='black'))
        self.known_plates = set()

        header_frame = tk.Frame(self.root, bg="#00bfff", height=60)
        header_frame.pack(side=tk.TOP, fill=tk.X)
        header_frame.pack_propagate(False) 

        tk.Label(header_frame, text="BÃI ĐỖ XE THÔNG MINH", font=title_font, bg="#00bfff", fg="#333333").place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        main_frame = tk.Frame(self.root, bg="#f0f2f5")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left_frame = tk.Frame(main_frame, bg="#ffffff", relief=tk.SOLID, borderwidth=1, width=420, height=650)
        left_frame.pack(side=tk.LEFT, padx=5, pady=5)
        left_frame.pack_propagate(False)
        tk.Label(left_frame, text="CAMERA VÀO", font=cam_title_font, bg="#ffffff").pack(pady=5)
        self.video_vao_label = tk.Label(left_frame, image=self.default_bg, bg="black", width=400, height=300)
        self.video_vao_label.pack(pady=5)
        self.plate_vao_big = tk.Label(left_frame, text="---\n---", font=self.plate_big_font, bg="#111111", fg="white", width=10, height=2)
        self.plate_vao_big.pack(pady=10)
        self.plate_vao_small = tk.Label(left_frame, text="---*---", font=self.plate_small_font, bg="#f4f4f4", relief=tk.SOLID, borderwidth=1, width=30, pady=5)
        self.plate_vao_small.pack(pady=5)

        center_frame = tk.Frame(main_frame, bg="#ffffff", relief=tk.SOLID, borderwidth=1, width=420, height=650)
        center_frame.pack(side=tk.LEFT, padx=5, pady=5)
        center_frame.pack_propagate(False)
        tk.Label(center_frame, text="CAMERA RA", font=cam_title_font, bg="#ffffff").pack(pady=5)
        self.video_ra_label = tk.Label(center_frame, image=self.default_bg, bg="black", width=400, height=300)
        self.video_ra_label.pack(pady=5)
        self.plate_ra_big = tk.Label(center_frame, text="---\n---", font=self.plate_big_font, bg="#111111", fg="white", width=10, height=2)
        self.plate_ra_big.pack(pady=10)
        self.plate_ra_small = tk.Label(center_frame, text="---*---", font=self.plate_small_font, bg="#f4f4f4", relief=tk.SOLID, borderwidth=1, width=30, pady=5)
        self.plate_ra_small.pack(pady=5)

        right_frame = tk.Frame(main_frame, bg="#f0f2f5", width=350, height=650)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.time_label = tk.Label(right_frame, text="00:00:00 - 00/00/0000", font=info_font, bg="#ffffff", relief=tk.SOLID, borderwidth=1)
        self.time_label.pack(fill=tk.X, pady=(0, 5), ipady=5)

        info_grid = tk.Frame(right_frame, bg="#f0f2f5")
        info_grid.pack(fill=tk.X)
        self.info_vars = {
            "SỐ XE": tk.StringVar(value="0"),
            "VÀO": tk.StringVar(value="..."),
            "RA": tk.StringVar(value="..."),
            "TIỀN": tk.StringVar(value="0 VNĐ")
        }
        row_idx = 0
        for key, var in self.info_vars.items():
            tk.Label(info_grid, text=key, font=info_font, bg="#f0f2f5", anchor="e", width=6).grid(row=row_idx, column=0, pady=5, padx=5, sticky="e")
            bg_col = "#32CD32" if key == "SỐ XE" else "#FFD700" if key == "TIỀN" else "#ffffff"
            tk.Label(info_grid, textvariable=var, font=info_font, bg=bg_col, fg="black", relief=tk.SOLID, borderwidth=1, width=20, anchor="center").grid(row=row_idx, column=1, pady=5, ipady=4)
            row_idx += 1

        btn_frame = tk.Frame(right_frame, bg="#f0f2f5")
        btn_frame.pack(pady=10, fill=tk.X)
        self.auto_mode = True 
        self.btn_thu_cong = tk.Button(btn_frame, text="THỦ CÔNG", bg="#f0f0f0", fg="black", font=info_font, width=12, command=lambda: self.set_mode(False))
        self.btn_thu_cong.grid(row=0, column=0, padx=5, pady=5)
        self.btn_tu_dong = tk.Button(btn_frame, text="TỰ ĐỘNG", bg="#1E90FF", fg="white", font=info_font, width=12, command=lambda: self.set_mode(True))
        self.btn_tu_dong.grid(row=0, column=1, padx=5, pady=5)
        tk.Button(btn_frame, text="VÀO", bg="#FF3333", fg="black", font=info_font, width=12, command=lambda: self.trigger_scan("VAO")).grid(row=1, column=0, padx=5, pady=5)
        tk.Button(btn_frame, text="RA", bg="#FF3333", fg="black", font=info_font, width=12, command=lambda: self.trigger_scan("RA")).grid(row=1, column=1, padx=5, pady=5)
        
        self.default_snapshot = ImageTk.PhotoImage(Image.new('RGB', (250, 100), color='#cccccc'))
        tk.Label(right_frame, text="📸 ẢNH XE VÀO", font=info_font, bg="#f0f2f5").pack(pady=(10, 0))
        self.snapshot_vao_label = tk.Label(right_frame, image=self.default_snapshot, bg="black", relief=tk.SOLID, borderwidth=1)
        self.snapshot_vao_label.pack(pady=2)
        tk.Label(right_frame, text="📸 ẢNH XE RA", font=info_font, bg="#f0f2f5").pack(pady=(10, 0))
        self.snapshot_ra_label = tk.Label(right_frame, image=self.default_snapshot, bg="black", relief=tk.SOLID, borderwidth=1)
        self.snapshot_ra_label.pack(pady=2)
        tk.Button(right_frame, text="THOÁT", bg="#FF3333", fg="black", font=info_font, width=26, command=self.on_closing).pack(pady=15)

        self.running = True
        self.latest_frame_vao = None
        self.latest_frame_ra = None
        self.vehicle_count = 0
        
        self.ui_queue = queue.Queue()
        self.ui_lock = threading.Lock()
        self.ui_frame_vao = None
        self.ui_frame_ra = None
        self.frame_locks = {"VAO": threading.Lock(), "RA": threading.Lock()}
        self.last_cam_update = {"VAO": 0, "RA": 0}

        print("Đang tải AI EasyOCR...")
        self.reader = easyocr.Reader(['en'])
        
        print("Đang kết nối Camera...")
        self.cap_vao = cv2.VideoCapture(URL_CAM_VAO)
        self.cap_ra = cv2.VideoCapture(URL_CAM_RA)
        self.cap_vao.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap_ra.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        print(">>> HỆ THỐNG ĐÃ SẴN SÀNG! <<<")
        self.root.after(30, self.refresh_ui)

        threading.Thread(target=self.update_clock, daemon=True).start()
        threading.Thread(target=self.video_loop, args=(self.cap_vao, "VAO", URL_CAM_VAO), daemon=True).start()
        threading.Thread(target=self.video_loop, args=(self.cap_ra, "RA", URL_CAM_RA), daemon=True).start()
        threading.Thread(target=self.single_ai_worker, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def set_mode(self, is_auto):
        self.auto_mode = is_auto
        if is_auto:
            self.btn_tu_dong.config(bg="#1E90FF", fg="white")
            self.btn_thu_cong.config(bg="#f0f0f0", fg="black")
        else:
            self.btn_tu_dong.config(bg="#f0f0f0", fg="black")
            self.btn_thu_cong.config(bg="#FFA500", fg="white")

    def update_clock(self):
        while self.running:
            now = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
            self.ui_queue.put({"type": "clock", "time": now})
            time.sleep(1)

    def refresh_ui(self):
        if not self.running: return

        with self.ui_lock:
            if self.ui_frame_vao:
                tk_vao = ImageTk.PhotoImage(image=self.ui_frame_vao)
                self.video_vao_label.config(image=tk_vao)
                self.video_vao_label.image = tk_vao
                self.ui_frame_vao = None

            if self.ui_frame_ra:
                tk_ra = ImageTk.PhotoImage(image=self.ui_frame_ra)
                self.video_ra_label.config(image=tk_ra)
                self.video_ra_label.image = tk_ra
                self.ui_frame_ra = None

        while not self.ui_queue.empty():
            try:
                msg = self.ui_queue.get_nowait()
                if msg["type"] == "clock":
                    self.time_label.config(text=msg["time"])
                elif msg["type"] == "scan_success":
                    if msg["cam_type"] == "VAO":
                        self.plate_vao_big.config(text=msg["big_text"])
                        self.plate_vao_small.config(text=msg["small_text"])
                    else:
                        self.plate_ra_big.config(text=msg["big_text"])
                        self.plate_ra_small.config(text=msg["small_text"])
                    
                    self.info_vars["SỐ XE"].set(str(msg["vehicle_count"]))
                    if msg["snapshot"] is not None:
                        tk_snap = ImageTk.PhotoImage(image=msg["snapshot"])
                        if msg["cam_type"] == "VAO":
                            self.snapshot_vao_label.config(image=tk_snap)
                            self.snapshot_vao_label.image = tk_snap
                        else:
                            self.snapshot_ra_label.config(image=tk_snap)
                            self.snapshot_ra_label.image = tk_snap
                elif msg["type"] == "update_bill":
                    self.info_vars["VÀO"].set(msg["time_in"])
                    self.info_vars["RA"].set(msg["time_out"])
                    self.info_vars["TIỀN"].set(msg["amount"])
                elif msg["type"] == "scan_error":
                    if msg["cam_type"] == "VAO": self.plate_vao_big.config(text="LỖI\nĐỌC")
                    else: self.plate_ra_big.config(text="LỖI\nĐỌC")
            except queue.Empty:
                break
        self.root.after(30, self.refresh_ui)

    def video_loop(self, cap, cam_id, url):
        while self.running:
            ret, frame = cap.read()
            if ret:
                frame_resized = cv2.resize(frame, (400, 300))
                with self.frame_locks[cam_id]:
                    if cam_id == "VAO": self.latest_frame_vao = frame_resized.copy()
                    else: self.latest_frame_ra = frame_resized.copy()
                
                current_time = time.time()
                if current_time - self.last_cam_update[cam_id] > 0.05:
                    self.last_cam_update[cam_id] = current_time
                    cv2.rectangle(frame_resized, (5, 5), (395, 295), (0, 255, 0), 2)
                    cv2.putText(frame_resized, "VUNG QUET AI", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

                    cv_img = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
                    pil_img = Image.fromarray(cv_img)
                    with self.ui_lock:
                        if cam_id == "VAO": self.ui_frame_vao = pil_img
                        else: self.ui_frame_ra = pil_img
            else:
                time.sleep(0.5)
                if self.running:
                    cap.release()
                    cap.open(url)

    def single_ai_worker(self):
        allowlist = '0123456789ABCDEFGHKLMNPRSTUVXYZ.-'
        last_plate_vao, streak_vao, cooldown_vao = "", 0, 0
        last_plate_ra, streak_ra, cooldown_ra = "", 0, 0

        while self.running:
            if not self.auto_mode:
                time.sleep(0.5)
                continue

            # --- CAMERA VÀO ---
            with self.frame_locks["VAO"]: frame_vao = self.latest_frame_vao.copy() if self.latest_frame_vao is not None else None
            if time.time() > cooldown_vao and frame_vao is not None:
                plate_vao = self.scan_roi(frame_vao, allowlist)
                if plate_vao:
                    if plate_vao == last_plate_vao: streak_vao += 1
                    else:
                        last_plate_vao = plate_vao
                        streak_vao = 1
                    if streak_vao >= 5: 
                        self.known_plates.add(plate_vao)
                        self.update_success_ui(plate_vao, "VAO", frame_vao)
                        cooldown_vao = time.time() + 15  # Tăng lên 15s
                        streak_vao = 0
                        last_plate_vao = ""
                else:
                    streak_vao = 0
                    last_plate_vao = ""

            # --- CAMERA RA ---
            with self.frame_locks["RA"]: frame_ra = self.latest_frame_ra.copy() if self.latest_frame_ra is not None else None
            if time.time() > cooldown_ra and frame_ra is not None:
                plate_ra = self.scan_roi(frame_ra, allowlist)
                if plate_ra:
                    if plate_ra == last_plate_ra: streak_ra += 1
                    else:
                        last_plate_ra = plate_ra
                        streak_ra = 1
                    if streak_ra >= 5: 
                        self.known_plates.add(plate_ra)
                        self.update_success_ui(plate_ra, "RA", frame_ra)
                        cooldown_ra = time.time() + 15  # Tăng lên 15s
                        streak_ra = 0
                        last_plate_ra = ""
                else:
                    streak_ra = 0
                    last_plate_ra = ""
            time.sleep(0.1) 

    def get_closest_known_plate(self, current_plate):
        for known in self.known_plates:
            if len(known) == len(current_plate):
                diff_count = sum(1 for a, b in zip(known, current_plate) if a != b)
                if diff_count <= 1: return known 
        return current_plate

    def force_number(self, char):
        mapping = {'A': '4', 'B': '8', 'G': '6', 'T': '1', 'I': '1', 'L': '4', 'S': '5', 'Z': '2', 'O': '0', 'D': '0'}
        return mapping.get(char, char)

    def force_letter(self, char):
        mapping = {'4': 'A', '8': 'B', '0': 'D', '6': 'G', '1': 'T', '5': 'S', '2': 'Z'}
        return mapping.get(char, char)

    # --- HÀM ÉP LỖI BỔ SUNG ---
    def fix_specific_errors(self, text):
        if text.startswith("78G1"):
            text = "18G1" + text[4:]
        return text

    def scan_roi(self, roi_frame, allowlist):
        gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)
        bfilter = cv2.bilateralFilter(gray, 11, 17, 17)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(bfilter)
        
        results = self.reader.readtext(enhanced, allowlist=allowlist, width_ths=0.7, slope_ths=0.3)
        current_plate = ""
        for (bbox, text, prob) in results:
            if prob > 0.3: current_plate += text
                
        clean_plate = "".join(char for char in current_plate if char.isalnum()).upper()
        if len(clean_plate) >= 8: 
            chars = list(clean_plate)
            chars[0] = self.force_number(chars[0])
            chars[1] = self.force_number(chars[1])
            chars[2] = self.force_letter(chars[2])
            for i in range(len(chars) - 5, len(chars)):
                chars[i] = self.force_number(chars[i])
            clean_plate = "".join(chars)

        # Sử dụng hàm ép lỗi
        clean_plate = self.fix_specific_errors(clean_plate)

        if len(clean_plate) >= 4:
            clean_plate = self.get_closest_known_plate(clean_plate)
            return clean_plate
        return None

    def format_plate(self, clean_text):
        if len(clean_text) == 9:
            top, bottom = f"{clean_text[:2]}-{clean_text[2:4]}", f"{clean_text[4:]}"
            return f"{top}\n{bottom}", f"{top}*{bottom}"
        elif len(clean_text) == 8:
            top, bottom = f"{clean_text[:3]}", f"{clean_text[3:]}"
            return f"{top}\n{bottom}", f"{top}*{bottom}"
        elif len(clean_text) >= 4:
            mid = len(clean_text) // 2
            return f"{clean_text[:mid]}\n{clean_text[mid:]}", f"{clean_text[:mid]}*{clean_text[mid:]}"
        return "LỖI\nĐỌC", "LỖI ĐỌC"

    def trigger_scan(self, cam_type):
        if cam_type == "VAO":
            self.plate_vao_big.config(text="ĐANG\nQUÉT")
            with self.frame_locks["VAO"]: frame_to_process = self.latest_frame_vao.copy() if self.latest_frame_vao is not None else None
        else:
            self.plate_ra_big.config(text="ĐANG\nQUÉT")
            with self.frame_locks["RA"]: frame_to_process = self.latest_frame_ra.copy() if self.latest_frame_ra is not None else None

        if frame_to_process is not None:
            threading.Thread(target=self.run_manual_ocr, args=(frame_to_process, cam_type), daemon=True).start()

    def run_manual_ocr(self, roi_frame, cam_type):
        plate = self.scan_roi(roi_frame, '0123456789ABCDEFGHKLMNPRSTUVXYZ.-')
        if plate: self.update_success_ui(plate, cam_type, roi_frame)
        else: self.ui_queue.put({"type": "scan_error", "cam_type": cam_type})

    def update_success_ui(self, plate_text, cam_type, roi_frame):
        big_text, small_text = self.format_plate(plate_text)
        pil_img = None
        if roi_frame is not None:
            cv_img = cv2.cvtColor(cv2.resize(roi_frame, (250, 100)), cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(cv_img)

        if cam_type == "VAO": self.vehicle_count += 1
        elif self.vehicle_count > 0: self.vehicle_count -= 1

        self.ui_queue.put({
            "type": "scan_success", "cam_type": cam_type,
            "big_text": big_text, "small_text": small_text,
            "snapshot": pil_img, "vehicle_count": self.vehicle_count
        })
        threading.Thread(target=self.send_to_server, args=(plate_text, cam_type), daemon=True).start()

    def on_closing(self):
        self.running = False
        time.sleep(0.5) 
        self.cap_vao.release()
        self.cap_ra.release()
        self.root.destroy()

if __name__ == "__main__":
    # Luôn bật Flask Server ngầm
    threading.Thread(target=run_flask_server, daemon=True).start()
    
    root = tk.Tk()
    app = SmartParkingSystem(root)
    root.mainloop()
