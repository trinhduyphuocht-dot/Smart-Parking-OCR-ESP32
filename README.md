# Smart-Parking-OCR-ESP3
# 🚗 Automated Smart Parking System (Smart-Parking-OCR-ESP32)

## 📖 Giới thiệu dự án
Dự án Bãi đỗ xe thông minh tự động ứng dụng công nghệ Thị giác máy tính (Computer Vision) để nhận diện biển số xe (ALPR - Automated License Plate Recognition). Hệ thống sử dụng vi điều khiển ESP32-CAM để thu thập hình ảnh và truyền dữ liệu về server Python xử lý thông qua mạng WiFi, kết hợp với giao diện Web Dashboard quản lý trực quan.

Dự án tập trung hoàn toàn vào giải pháp nhận diện hình ảnh (OCR), tối ưu hóa thuật toán để đạt độ chính xác cao mà không phụ thuộc vào các thẻ vật lý.

## 🛠 Công nghệ sử dụng
**Hardware:**
* Vi điều khiển: **ESP32-CAM** (Thu thập hình ảnh và truyền phát qua WiFi).

**Software & AI/ML:**
* **Ngôn ngữ lập trình:** C/C++ (Firmware cho ESP32), Python (Xử lý Backend & AI).
* **Computer Vision:** * **YOLOv8:** Phát hiện vị trí (Detection) và cắt vùng chứa biển số xe.
  * **EasyOCR:** Nhận diện và trích xuất ký tự quang học (OCR) từ vùng biển số.
* **Web Framework:** **Flask** (Xây dựng Dashboard quản lý, giao tiếp API với thiết bị phần cứng).

## ⚙️ Kiến trúc hệ thống & Logic hoạt động

### 1. Thu thập hình ảnh tối ưu
Để cải thiện tối đa độ chính xác của mô hình nhận diện (đặc biệt với form biển số ngang), module **ESP32-CAM được thiết lập góc đặt camera nằm ngang (horizontal orientation)**. Điều này giúp khung hình thu được bao quát trọn vẹn biển số, giảm thiểu tình trạng cắt xén ký tự trước khi đẩy qua model YOLOv8.

### 2. Luồng xử lý (Workflow)
1. ESP32-CAM chụp ảnh phương tiện tại cổng và gửi HTTP POST request chứa dữ liệu ảnh về Flask Server.
2. Server tiếp nhận, đưa ảnh qua model YOLOv8 để detect box chứa biển số.
3. EasyOCR đọc ký tự từ box và trả về text (chuỗi ký tự biển số).
4. Hệ thống đối chiếu dữ liệu, lưu thời gian vào/ra và tính toán chi phí trên Dashboard.

### 3. Logic luồng thanh toán (Payment State Flow)
Hệ thống được thiết kế với máy trạng thái (State Machine) quản lý luồng xe ra vào cực kỳ chặt chẽ. Điểm nhấn trong logic hệ thống là quy trình xử lý sau thanh toán:
* Hệ thống sẽ kiểm tra trạng thái thanh toán của phương tiện (State Level 3).
* Ngay sau khi xác nhận thanh toán thành công, **chương trình được lập trình để reset trạng thái trực tiếp từ Level 3 quay trở về Level 1** (Trạng thái chờ đón lượt phương tiện mới), đảm bảo luồng hoạt động diễn ra liên tục, không bị treo trạng thái ở barrier đầu ra.

## 🚀 Hướng dẫn cài đặt (Installation)
*(Các bước cài đặt môi trường - Bạn có thể bổ sung thêm các lệnh `pip install` cụ thể của bạn)*

1. Clone repository này về máy:
   ```bash
   git clone [https://github.com/trinhduyphuoc/Smart-Parking-OCR-ESP32.git](https://github.com/trinhduyphuoc/Smart-Parking-OCR-ESP32.git)
