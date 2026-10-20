# -*- coding: utf-8 -*-
"""
BTVN A4 Bot - Tự động nhận lời dặn của cô giáo từ Telegram -> Gọi Gemini API sắp xếp khoa học
-> Ghép Thời khóa biểu -> Xuất PDF A4 tương phản siêu cao (cho máy in mờ) phủ kín 100% trang
-> Gửi trả file PDF vào Telegram.
"""

import os
import re
import sys
import json
import time
import shutil
import asyncio
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

try:
    import google.generativeai as genai
except ImportError:
    genai = None

try:
    from telegram import Bot
except ImportError:
    Bot = None

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW_VN = datetime.now(tz=TZ_VN)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_CHAT_ID_2 = os.getenv("TELEGRAM_CHAT_ID_2", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Inputs truyền từ GitHub Actions workflow_dispatch hoặc repository_dispatch
INPUT_MESSAGE_TEXT = os.getenv("INPUT_MESSAGE_TEXT", "").strip()
INPUT_CHAT_ID = os.getenv("INPUT_CHAT_ID", "").strip()
INPUT_PHOTO_FILE_ID = os.getenv("INPUT_PHOTO_FILE_ID", "").strip()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TKB_FILE = os.path.join(SCRIPT_DIR, "tkb_data.json")
STATE_FILE = os.path.join(SCRIPT_DIR, "btvn_state.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "Report_BTVN")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg: str):
    ts = datetime.now(tz=TZ_VN).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_tkb() -> dict:
    if os.path.exists(TKB_FILE):
        try:
            with open(TKB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"Lỗi đọc tkb_data.json: {e}")
    return {"student_name": "Lê Minh Huy", "class_name": "3", "schedule": {}}


def get_default_target_day(dt: datetime) -> tuple[str, str]:
    """
    Tính thứ và ngày học tiếp theo mặc định dựa trên thời điểm gửi tin nhắn:
    - Nếu gửi sáng sớm (< 07:30) từ Thứ 2 -> Thứ 6: Tính cho chính ngày hôm đó.
    - Nếu gửi chiều/tối Thứ 2 -> Thứ 5: Tính cho ngày hôm sau (Thứ 3 -> Thứ 6).
    - Nếu gửi chiều/tối Thứ 6, hoặc Thứ 7, Chủ Nhật: Tính cho Thứ 2 tuần kế tiếp.
    """
    wd = dt.weekday()  # 0=Mon .. 6=Sun
    if wd < 5 and dt.hour < 7:
        target_dt = dt
    elif wd in (0, 1, 2, 3):
        target_dt = dt + timedelta(days=1)
    else:
        days_until_mon = (7 - wd) % 7
        if days_until_mon == 0:
            days_until_mon = 1
        target_dt = dt + timedelta(days=days_until_mon)

    vn_days = {
        0: "Thứ 2",
        1: "Thứ 3",
        2: "Thứ 4",
        3: "Thứ 5",
        4: "Thứ 6",
        5: "Thứ 7",
        6: "Chủ Nhật",
    }
    day_name = vn_days.get(target_dt.weekday(), "Thứ 2")
    date_str = f"{day_name}, ngày {target_dt.strftime('%d/%m/%Y')}"
    return day_name, date_str


def fetch_latest_telegram_update() -> tuple[str, str, str]:
    """
    Nếu không truyền sẵn INPUT_MESSAGE_TEXT qua webhook, tự động gọi getUpdates của Telegram Bot
    để lấy tin nhắn mới nhất chưa xử lý.
    Trả về: (message_text, chat_id, photo_file_id)
    """
    if not TELEGRAM_BOT_TOKEN:
        return "", "", ""

    last_update_id = 0
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
                last_update_id = int(st.get("last_update_id", 0))
        except Exception:
            pass

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 5, "allowed_updates": ["message"]}
    if last_update_id > 0:
        params["offset"] = last_update_id + 1

    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if not data.get("ok") or not data.get("result"):
            return "", "", ""

        updates = data["result"]
        latest = updates[-1]
        new_update_id = latest["update_id"]
        msg = latest.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = (msg.get("text") or msg.get("caption") or "").strip()
        photo_file_id = ""
        if msg.get("photo"):
            photo_file_id = msg["photo"][-1].get("file_id", "")

        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "last_update_id": new_update_id,
                    "updated_at": NOW_VN.strftime("%Y-%m-%d %H:%M:%S"),
                },
                f,
                indent=2,
            )

        return text, chat_id, photo_file_id
    except Exception as e:
        log(f"Lỗi khi gọi getUpdates: {e}")
        return "", "", ""


def download_telegram_photo(file_id: str) -> str | None:
    if not file_id or not TELEGRAM_BOT_TOKEN:
        return None
    try:
        r1 = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=15,
        ).json()
        file_path = r1.get("result", {}).get("file_path")
        if not file_path:
            return None
        img_bytes = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}",
            timeout=20,
        ).content
        local_img = os.path.join(OUTPUT_DIR, "teacher_note_photo.jpg")
        with open(local_img, "wb") as f:
            f.write(img_bytes)
        return local_img
    except Exception as e:
        log(f"Không tải được ảnh Telegram: {e}")
        return None


def get_available_gemini_model() -> str:
    preferred = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-flash-latest",
        "gemini-2.0-flash",
    ]
    try:
        models = [
            m.name
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
        ]
        for p in preferred:
            for m in models:
                if p in m:
                    return m
        for m in models:
            if "flash" in m.lower():
                return m
        return models[0] if models else "models/gemini-2.5-flash"
    except Exception:
        return "models/gemini-2.5-flash"


def structure_teacher_note_with_gemini(
    raw_text: str, image_path: str | None, tkb_data: dict
) -> dict:
    default_day, default_date_str = get_default_target_day(NOW_VN)

    system_prompt = f"""Bạn là trợ lý giáo dục tiểu học chuyên sắp xếp lời dặn của cô giáo chủ nhiệm (Cô Lê Thị Vân) thành Phiếu Dặn Dò & Bài Tập Về Nhà khổ A4 cho học sinh Lê Minh Huy.

THỜI ĐIỂM HIỆN TẠI: {NOW_VN.strftime('%Y-%m-%d %H:%M')} (Mặc định chuẩn bị bài cho: {default_day} - {default_date_str}).
DỮ LIỆU THỜI KHÓA BIỂU CỐ ĐỊNH CỦA LỚP:
{json.dumps(tkb_data.get("schedule", {}), ensure_ascii=False, indent=2)}

NGUYÊN TẮC BẮT BUỘC TUYỆT ĐỐI (ZERO HALLUCINATION):
1. KHÔNG ĐƯỢC BỊA ĐẶT: Chỉ trích xuất và sắp xếp lại đúng 100% những thông tin cô giáo dặn trong tin nhắn/hình ảnh. Tuyệt đối không tự ý bịa thêm bài tập, số trang hay yêu cầu không có trong lời dặn của cô.
2. PHÂN NHÓM KHOA HỌC:
   - Gom các bài tập/yêu cầu học bài vào mảng "subjects" theo từng môn học rõ ràng (VD: "TOÁN", "TIẾNG VIỆT", "TIẾNG ANH", "TỰ NHIÊN & XÃ HỘI", "CÔNG NGHỆ", "MĨ THUẬT"...).
   - Gom các lời dặn chung (đồng phục, nộp giấy tờ, lịch kiểm tra, mang đồ dùng đặc biệt, giờ giấc...) vào mảng "general_notes".
3. NHẤN MẠNH TỪ KHÓA QUAN TRỌNG:
   - Sử dụng thẻ HTML `<b>...</b>` để làm nổi bật số trang sách, bài số mấy, tên sách/vở, hạn nộp hoặc yêu cầu trọng tâm (Ví dụ: "Hoàn thành bài tập <b>trang 42, 43</b> trong <b>Vở bài tập Toán</b>").
4. XÁC ĐỊNH NGÀY HỌC TIẾP THEO ("target_day"):
   - Nếu lời dặn nói rõ chuẩn bị cho thứ mấy (ví dụ "Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6") thì điền đúng giá trị đó. Nếu không nói rõ, dùng mặc định "{default_day}".

ĐỊNH DẠNG ĐẦU RA:
Chỉ trả về DUY NHẤT một chuỗi JSON hợp lệ (không bọc trong markdown ```json), theo cấu trúc:
{{
  "title": "PHIẾU DẶN DÒ & BÀI TẬP VỀ NHÀ",
  "target_day": "{default_day}",
  "date_label": "{default_date_str}",
  "subjects": [
    {{
      "name": "TÊN MÔN HỌC",
      "tasks": [
        "Nội dung công việc 1 (có dùng thẻ <b>...</b> cho từ khóa chính)",
        "Nội dung công việc 2"
      ]
    }}
  ],
  "general_notes": [
    "Lưu ý chung 1 (nếu có)"
  ]
}}
"""

    if not GEMINI_API_KEY or not genai:
        log("Không có GEMINI_API_KEY, dùng bộ phân tách dòng trực tiếp (Fallback).")
        return fallback_structure(raw_text, default_day, default_date_str)

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model_name = get_available_gemini_model()
        log(f"Đang gọi Gemini model: {model_name}")
        model = genai.GenerativeModel(model_name, system_instruction=system_prompt)

        contents = []
        if image_path and os.path.exists(image_path):
            from PIL import Image
            img = Image.open(image_path)
            contents.append(img)
        if raw_text:
            contents.append(f"Nội dung cô giáo dặn:\n{raw_text}")
        else:
            contents.append("Hãy đọc nội dung cô giáo dặn trong ảnh và sắp xếp theo đúng cấu trúc JSON yêu cầu.")

        resp = model.generate_content(contents)
        text_out = resp.text.strip()
        if text_out.startswith("```"):
            text_out = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=re.DOTALL).strip()

        parsed = json.loads(text_out)
        if not parsed.get("target_day"):
            parsed["target_day"] = default_day
        if not parsed.get("date_label"):
            parsed["date_label"] = default_date_str
        return parsed
    except Exception as e:
        log(f"Lỗi khi gọi Gemini API ({e}), chuyển sang bộ phân tách dự phòng.")
        return fallback_structure(raw_text, default_day, default_date_str)


def fallback_structure(raw_text: str, default_day: str, default_date_str: str) -> dict:
    lines = [
        re.sub(r"^[\-\*\•\+]\s*", "", ln.strip())
        for ln in (raw_text or "").splitlines()
        if ln.strip()
    ]
    if not lines:
        lines = ["Ôn tập bài cũ và chuẩn bị đầy đủ sách vở theo Thời khóa biểu."]
    return {
        "title": "PHIẾU DẶN DÒ & BÀI TẬP VỀ NHÀ",
        "target_day": default_day,
        "date_label": default_date_str,
        "subjects": [
            {
                "name": "NỘI DUNG CÔ GIÁO DẶN",
                "tasks": lines,
            }
        ],
        "general_notes": [],
    }


def render_btvn_html(data: dict, tkb_data: dict) -> str:
    title = data.get("title", "PHIẾU DẶN DÒ & BÀI TẬP VỀ NHÀ")
    target_day = data.get("target_day", "Thứ 2")
    date_label = data.get("date_label", "")
    student_name = tkb_data.get("student_name", "Lê Minh Huy")
    teacher_name = tkb_data.get("teacher_name", "Cô Lê Thị Vân")
    subjects = data.get("subjects", [])
    general_notes = data.get("general_notes", [])

    schedule_all = tkb_data.get("schedule", {})
    day_schedule = schedule_all.get(target_day, {})
    morning_list = day_schedule.get("morning", [])
    afternoon_list = day_schedule.get("afternoon", [])
    schedule_note = day_schedule.get("notes", "")

    subjects_html = ""
    for subj in subjects:
        tasks = subj.get("tasks", [])
        if not tasks:
            continue
        tasks_li = "".join(
            f'<li class="task-item"><span class="checkbox-box"></span><div class="task-text">{t}</div></li>'
            for t in tasks
        )
        subjects_html += f"""
        <div class="section-box subject-card">
          <div class="section-header">
            <span class="section-title">■ {subj.get("name", "").upper()}</span>
          </div>
          <ul class="task-list">
            {tasks_li}
          </ul>
        </div>
        """

    notes_html = ""
    if general_notes:
        notes_li = "".join(
            f'<li class="task-item"><span class="checkbox-box"></span><div class="task-text">{n}</div></li>'
            for n in general_notes
        )
        notes_html = f"""
        <div class="section-box notes-card">
          <div class="section-header inverted">
            <span class="section-title">★ DẶN DÒ CHUNG &amp; LƯU Ý QUAN TRỌNG</span>
          </div>
          <ul class="task-list">
            {notes_li}
          </ul>
        </div>
        """

    tkb_html = ""
    if morning_list or afternoon_list:
        m_items = "".join(
            f'<div class="tkb-subj-item"><span class="checkbox-box sm"></span><span>{m}</span></div>'
            for m in morning_list
        )
        a_items = "".join(
            f'<div class="tkb-subj-item"><span class="checkbox-box sm"></span><span>{a}</span></div>'
            for a in afternoon_list
        )
        note_row = (
            f'<div class="tkb-note-bar"><b>Lưu ý TKB ({target_day}):</b> {schedule_note}</div>'
            if schedule_note
            else ""
        )
        tkb_html = f"""
        <div class="section-box tkb-card">
          <div class="section-header">
            <span class="section-title">🎒 SOẠN SÁCH VỞ THEO THỜI KHÓA BIỂU ({target_day.upper()})</span>
          </div>
          <div class="tkb-grid">
            <div class="tkb-col">
              <div class="tkb-col-title">BUỔI SÁNG (7h30 có mặt)</div>
              <div class="tkb-col-body">{m_items}</div>
            </div>
            <div class="tkb-col">
              <div class="tkb-col-title">BUỔI CHIỀU (13h30 có mặt)</div>
              <div class="tkb-col-body">{a_items}</div>
            </div>
          </div>
          {note_row}
        </div>
        """

    # Nếu tổng số môn học >= 4, có thể bật chế độ lưới 2 cột khi cần
    many_subjects_class = "grid-two-cols" if len(subjects) >= 4 else ""

    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <title>{title} - {date_label}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@700;800;900&display=swap" rel="stylesheet">
  <style>
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
    }}

    @page {{
      size: A4 portrait;
      margin: 6mm 6mm 6mm 6mm;
    }}

    :root {{
      --base-font: 19px;
      --title-font: 27px;
      --header-font: 20px;
      --line-height: 1.42;
      --card-pad: 14px;
      --item-gap: 11px;
      --box-size: 23px;
    }}

    body {{
      font-family: "Be Vietnam Pro", "Segoe UI", Tahoma, Verdana, Arial, sans-serif;
      background-color: #ffffff !important;
      color: #000000 !important;
      font-weight: 800;
      line-height: var(--line-height);
      -webkit-font-smoothing: antialiased;
    }}

    .a4-page {{
      width: 198mm;
      height: 285mm;
      margin: 0 auto;
      background: #ffffff !important;
      border: 4px solid #000000;
      padding: 4.5mm;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 3.5mm;
      overflow: hidden;
    }}

    .page-header {{
      border: 3.5px solid #000000;
      padding: 9px 14px;
      text-align: center;
      background: #ffffff;
      flex-shrink: 0;
    }}

    .page-title {{
      font-size: var(--title-font);
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: #000000;
      line-height: 1.15;
    }}

    .page-meta {{
      margin-top: 6px;
      padding-top: 6px;
      border-top: 2.5px solid #000000;
      font-size: calc(var(--base-font) * 0.88);
      font-weight: 900;
      color: #000000;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}

    .page-body {{
      flex: 1 1 auto;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 3.5mm;
      min-height: 0;
    }}

    .subjects-container {{
      flex: 1 1 auto;
      display: flex;
      flex-direction: column;
      justify-content: stretch;
      gap: 3.5mm;
    }}

    .subjects-container.grid-two-cols.compact-mode {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      align-items: stretch;
    }}

    .section-box {{
      border: 3px solid #000000;
      background: #ffffff;
      display: flex;
      flex-direction: column;
      flex: 1 1 auto;
    }}

    .section-header {{
      border-bottom: 3px solid #000000;
      padding: 6px 14px;
      background: #ffffff;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}

    .section-header.inverted {{
      background: #000000 !important;
      color: #ffffff !important;
    }}

    .section-header.inverted .section-title {{
      color: #ffffff !important;
    }}

    .section-title {{
      font-size: var(--header-font);
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 0.4px;
      color: #000000;
    }}

    .task-list {{
      list-style: none;
      padding: var(--card-pad);
      display: flex;
      flex-direction: column;
      justify-content: space-evenly;
      gap: var(--item-gap);
      flex: 1 1 auto;
    }}

    .task-item {{
      display: flex;
      align-items: flex-start;
      gap: 12px;
      font-size: var(--base-font);
      font-weight: 800;
      color: #000000;
      line-height: var(--line-height);
      padding-bottom: 6px;
      border-bottom: 2px dashed #000000;
    }}

    .task-item:last-child {{
      border-bottom: none;
      padding-bottom: 0;
    }}

    .checkbox-box {{
      width: var(--box-size);
      height: var(--box-size);
      min-width: var(--box-size);
      border: 3px solid #000000;
      background: #ffffff;
      display: inline-block;
      margin-top: 3px;
    }}

    .checkbox-box.sm {{
      width: calc(var(--box-size) * 0.84);
      height: calc(var(--box-size) * 0.84);
      min-width: calc(var(--box-size) * 0.84);
      border: 2.5px solid #000000;
      margin-top: 2px;
    }}

    .task-text {{
      flex: 1;
    }}

    .task-text b, .task-text strong {{
      font-weight: 900;
      text-decoration: underline;
      text-underline-offset: 3px;
    }}

    /* THỜI KHÓA BIỂU */
    .tkb-card {{
      flex: 0 1 auto;
    }}

    .tkb-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
    }}

    .tkb-col {{
      display: flex;
      flex-direction: column;
    }}

    .tkb-col:first-child {{
      border-right: 3px solid #000000;
    }}

    .tkb-col-title {{
      font-size: calc(var(--base-font) * 0.92);
      font-weight: 900;
      text-align: center;
      padding: 6px;
      border-bottom: 2.5px solid #000000;
      text-transform: uppercase;
    }}

    .tkb-col-body {{
      padding: 9px 12px;
      display: flex;
      flex-direction: column;
      gap: 7px;
      flex: 1;
      justify-content: space-evenly;
    }}

    .tkb-subj-item {{
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: calc(var(--base-font) * 0.92);
      font-weight: 800;
    }}

    .tkb-note-bar {{
      border-top: 2.5px solid #000000;
      padding: 7px 12px;
      font-size: calc(var(--base-font) * 0.88);
      font-weight: 800;
    }}

    .page-footer {{
      border: 3px solid #000000;
      padding: 8px 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: calc(var(--base-font) * 0.85);
      font-weight: 900;
      flex-shrink: 0;
    }}

    .footer-check {{
      display: flex;
      align-items: center;
      gap: 9px;
    }}
  </style>
</head>
<body>
  <div class="a4-page" id="a4Page">
    <div class="page-header">
      <div class="page-title">{title}</div>
      <div class="page-meta">
        <span>HỌC SINH: <u>{student_name.upper()}</u></span>
        <span>GVCN: <u>{teacher_name.upper()}</u></span>
        <span>CHUẨN BỊ CHO: <u>{date_label.upper()}</u></span>
      </div>
    </div>

    <div class="page-body" id="pageBody">
      <div class="subjects-container {many_subjects_class}" id="subjectsContainer">
        {subjects_html}
      </div>
      {notes_html}
      {tkb_html}
    </div>

    <div class="page-footer">
      <div class="footer-check">
        <span class="checkbox-box sm"></span>
        <span>ĐÃ HOÀN THÀNH ĐẦY ĐỦ BÀI TẬP</span>
      </div>
      <div class="footer-check">
        <span class="checkbox-box sm"></span>
        <span>ĐÃ SOẠN ĐỦ SÁCH VỞ, BƠM MỰC BÚT VÀO CẶP</span>
      </div>
    </div>
  </div>

  <script>
    (function autoFitA4() {{
      const page = document.getElementById('a4Page');
      const subjContainer = document.getElementById('subjectsContainer');
      const root = document.documentElement;
      if (!page) return;

      let baseFont = 19;
      const minFont = 12.5;
      const maxFont = 27;

      function applyScale(size) {{
        root.style.setProperty('--base-font', size + 'px');
        root.style.setProperty('--title-font', Math.round(size * 1.42) + 'px');
        root.style.setProperty('--header-font', Math.round(size * 1.08) + 'px');
        root.style.setProperty('--card-pad', Math.max(7, Math.round(size * 0.72)) + 'px');
        root.style.setProperty('--item-gap', Math.max(5, Math.round(size * 0.58)) + 'px');
        root.style.setProperty('--box-size', Math.max(17, Math.round(size * 1.2)) + 'px');
      }}

      applyScale(baseFont);

      // Nếu nội dung ít, phóng to chữ và khoảng cách để phủ kín trang A4
      while (baseFont < maxFont && page.scrollHeight <= page.clientHeight) {{
        baseFont += 0.5;
        applyScale(baseFont);
        if (page.scrollHeight > page.clientHeight) {{
          baseFont -= 0.5;
          applyScale(baseFont);
          break;
        }}
      }}

      // Nếu nội dung dài bị tràn, thu nhỏ cỡ chữ dần
      while (baseFont > minFont && page.scrollHeight > page.clientHeight) {{
        baseFont -= 0.5;
        applyScale(baseFont);
        if (baseFont <= 15 && subjContainer && subjContainer.classList.contains('grid-two-cols')) {{
          subjContainer.classList.add('compact-mode');
        }}
      }}
    }})();
  </script>
</body>
</html>
"""
    return html


def export_pdf(html_content: str, pdf_path: str) -> str | None:
    html_path = pdf_path.replace(".pdf", ".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    chrome_bin = None
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        chrome_bin = next((c for c in candidates if os.path.exists(c)), None)
    else:
        chrome_bin = (
            shutil.which("google-chrome-stable")
            or shutil.which("google-chrome")
            or shutil.which("chromium")
        )

    if not chrome_bin:
        log("Không tìm thấy trình duyệt Chrome/Edge để xuất PDF.")
        return None

    abs_html = os.path.abspath(html_path).replace("\\", "/")
    abs_pdf = os.path.abspath(pdf_path)

    cmd = [
        chrome_bin,
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--no-pdf-header-footer",
        "--virtual-time-budget=3000",
        f"--print-to-pdf={abs_pdf}",
        f"file:///{abs_html}",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log(f"Đã xuất thành công PDF: {abs_pdf}")
    return abs_pdf


async def send_pdf_to_telegram(pdf_path: str, structured: dict, target_chat_ids: list[str]):
    if not TELEGRAM_BOT_TOKEN or not Bot:
        log("Thiếu TELEGRAM_BOT_TOKEN hoặc thư viện python-telegram-bot.")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    target_day = structured.get("target_day", "")
    date_label = structured.get("date_label", "")

    summary_lines = [f"📌 *PHIẾU DẶN DÒ & BTVN ({date_label})*"]
    for s in structured.get("subjects", []):
        summary_lines.append(f"\n🔹 *{s.get('name', '').upper()}*:")
        for t in s.get("tasks", []):
            clean_t = re.sub(r"<[^>]+>", "", t)
            summary_lines.append(f"  ☐ {clean_t}")
    if structured.get("general_notes"):
        summary_lines.append("\n⚠️ *LƯU Ý QUAN TRỌNG*:")
        for n in structured.get("general_notes", []):
            clean_n = re.sub(r"<[^>]+>", "", n)
            summary_lines.append(f"  ☐ {clean_n}")

    caption = "\n".join(summary_lines)
    if len(caption) > 950:
        caption = caption[:950] + "..."

    unique_chats = []
    for cid in target_chat_ids:
        if cid and cid not in unique_chats:
            unique_chats.append(cid)

    for cid in unique_chats:
        try:
            with open(pdf_path, "rb") as f_doc:
                await bot.send_document(
                    chat_id=cid,
                    document=f_doc,
                    filename=os.path.basename(pdf_path),
                    caption=caption,
                )
            log(f"Đã gửi PDF tới Telegram chat_id={cid}")
        except Exception as e:
            log(f"Lỗi gửi PDF tới chat_id={cid}: {e}")


def main():
    msg_text = INPUT_MESSAGE_TEXT
    chat_id = INPUT_CHAT_ID
    photo_file_id = INPUT_PHOTO_FILE_ID

    if not msg_text and not photo_file_id:
        log("Không có input trực tiếp từ workflow_dispatch, đang kiểm tra getUpdates từ Telegram...")
        msg_text, chat_id, photo_file_id = fetch_latest_telegram_update()

    if not msg_text and not photo_file_id:
        log("Không có tin nhắn mới nào cần xử lý. Kết thúc.")
        return

    log(f"Nhận yêu cầu tạo Phiếu Dặn Dò A4 (chat_id={chat_id}): {msg_text[:120]}...")
    image_path = download_telegram_photo(photo_file_id) if photo_file_id else None

    tkb_data = load_tkb()
    structured = structure_teacher_note_with_gemini(msg_text, image_path, tkb_data)

    date_slug = NOW_VN.strftime("%Y-%m-%d_%H%M")
    pdf_filename = f"Phieu_Dan_Do_A4_{date_slug}.pdf"
    pdf_path = os.path.join(OUTPUT_DIR, pdf_filename)

    html_content = render_btvn_html(structured, tkb_data)
    out_pdf = export_pdf(html_content, pdf_path)

    if out_pdf and os.path.exists(out_pdf):
        recipients = [chat_id, TELEGRAM_CHAT_ID, TELEGRAM_CHAT_ID_2]
        asyncio.run(send_pdf_to_telegram(out_pdf, structured, recipients))


if __name__ == "__main__":
    main()
