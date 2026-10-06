# CEC English Report Tracker & Vocabulary Generator

Hệ thống tự động theo dõi báo cáo học tập định kỳ của học sinh tại CEC (Trung tâm Anh ngữ CEC), trích xuất từ vựng bằng Gemini AI, tự động tạo Phiếu tập chép 5 cột khổ A4 chuẩn in ấn, xuất thẻ từ vựng Quizlet và gửi thông báo qua Telegram.

---

## 📌 Cấu trúc thư mục (Repository Structure)

Repo được tổ chức gọn gàng, tách biệt các thành phần đang chạy thực tế và kho lưu trữ lịch sử:

```
eng/
├── .github/
│   └── workflows/
│       └── sele.yml                  # [ACTIVE] GitHub Actions chạy hàng giờ kiểm tra báo cáo mới
├── Report/                           # [ACTIVE] Thư mục lưu báo cáo JSON, PDF phiếu tập chép & Quizlet
│   ├── YYYY-MM-DD_Lesson_XX.json
│   ├── YYYY-MM-DD_Lesson_XX_Phieu_Tap_Chep.pdf
│   └── YYYY-MM-DD_Lesson_XX_Quizlet_Import.txt
├── legacy/                           # [ARCHIVE] Lưu trữ toàn bộ mã nguồn, cấu hình và dữ liệu cũ
│   ├── data/                         # Cấu hình cũ, link cũ, dữ liệu lớp cũ (10539, Class/...)
│   ├── logs/                         # File log cũ phình to (class_info_log.txt ~17.5 MB)
│   ├── scripts/                      # Các script thử nghiệm giai đoạn đầu (main.py, extract_lessons.py...)
│   └── workflows/                    # Các GitHub Actions cũ đã ngừng chạy (selenium.yml, extract.yml)
├── .gitignore                        # Cấu hình loại bỏ file rác, credential, cache
├── class_info_log2.txt               # [ACTIVE] Nhật ký chạy của notimain.py (tự động xoay vòng < 5000 dòng)
├── notimain.py                       # [ACTIVE] Script chính: Cào CEC, Gemini AI, sinh PDF, gửi Telegram
├── processed2.json                   # [ACTIVE] Trạng thái lịch sử các ngày học đã xử lý
├── requirements.txt                  # [ACTIVE] Danh sách thư viện Python cần thiết
├── vocab_total.json                  # [ACTIVE] Từ điển tích lũy tổng hợp các buổi học
└── README.md                         # Tài liệu hướng dẫn này
```

---

## ⚙️ Luồng hoạt động tự động (Automation Workflow)

1. **GitHub Action (`sele.yml`)**:
   - Chạy định kỳ vào phút thứ 5 mỗi giờ (hoặc kích hoạt thủ công qua `workflow_dispatch`).
   - Cài đặt môi trường Python 3.10 và Google Chrome headless.
2. **Script chính (`notimain.py`)**:
   - Đăng nhập vào cổng học viên CEC (`apps.cec.com.vn`) bằng tài khoản phụ huynh.
   - Tìm kiếm các buổi học mới nhất tính đến thời điểm hiện tại.
   - Nếu phát hiện nút **"Báo cáo bài học"** đã được giáo viên đăng tải và chưa từng xử lý:
     - Tải và phân tích nội dung báo cáo PDF.
     - Sử dụng **Gemini AI** trích xuất từ vựng mới, từ vựng ôn tập, ngữ pháp, phonics, bài tập về nhà.
     - Tích lũy từ vựng với các buổi trước (tối đa 40 từ) và lưu vào `vocab_total.json`.
     - Tự động sinh **Phiếu tập chép 5 cột A4** (`*_Phieu_Tap_Chep.pdf`) và file import **Quizlet** (`*_Quizlet_Import.txt`).
     - Gửi tin nhắn tóm tắt kèm các file đính kèm trực tiếp đến phụ huynh qua **Telegram Bot**.
     - Cập nhật dữ liệu vào Google Sheets.
     - Tự động commit và đẩy các file kết quả mới (`Report/*`, `vocab_total.json`, `processed2.json`) lên repo GitHub.

---

## 🔑 Cấu hình Secrets (GitHub Repository Secrets)

Để hệ thống hoạt động trơn tru trên GitHub Actions, các Secrets sau được cấu hình:

| Secret Name | Mô tả |
| :--- | :--- |
| `NEW_CEC_USER` | Tên đăng nhập cổng CEC |
| `NEW_CEC_PASS` | Mật khẩu cổng CEC |
| `GEMINI_API_KEY` | Google Gemini API Key để phân tích báo cáo và từ vựng |
| `TELEGRAM_BOT_TOKEN` | Token của Telegram Bot thông báo |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID chính nhận thông báo |
| `TELEGRAM_CHAT_ID_2` | Telegram Chat ID phụ (nếu có) |
| `GOOGLE_CREDENTIALS` | Nội dung JSON Service Account để ghi Google Sheets |
