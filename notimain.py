import json
import time
import os
import sys
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
import pdfplumber
import google.generativeai as genai
from urllib.parse import parse_qs, urlparse
from telegram import Bot
import asyncio
import re
import socket

# Configuration
PROCESSED_FILE = "processed2.json"
CREDENTIALS_FILE = "credentials.json"
SHEET_ID = "1-MMsbAGlg7MNbBPAzioqARu6QLfry5mCrWJ-Q_aqmIM"
SHEET_NAME = "Report"
REPORT_CONTENT_SHEET = "ReportContent"
VOCAB_SHEET = "vocab"
SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_CHAT_ID_2 = os.getenv("TELEGRAM_CHAT_ID_2")
API_KEY = os.getenv("GEMINI_API_KEY")
LOG_FILE = "class_info_log2.txt"
VOCAB_FILE = "vocab_total.json"
TODAY = datetime.now(tz=ZoneInfo("Asia/Ho_Chi_Minh")).replace(hour=0, minute=0, second=0, microsecond=0)

# Logging function
def log_message(message):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")

# Check network connectivity
def check_network():
    try:
        socket.create_connection(("www.google.com", 80), timeout=5)
        return True
    except OSError as e:
        log_message(f"Network check failed: {str(e)}")
        return False

# Check WebDriver responsiveness
def check_webdriver(driver):
    try:
        driver.execute_script("return true;")
        return True
    except Exception as e:
        log_message(f"WebDriver unresponsive: {str(e)}")
        return False

# Restart WebDriver
def restart_webdriver(driver, options):
    log_message("Restarting WebDriver")
    try:
        driver.quit()
    except:
        pass
    return webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)

# Send basic Telegram notification
def send_basic_notification(subject, body, chat_ids=[TELEGRAM_CHAT_ID, TELEGRAM_CHAT_ID_2]):
    log_message("Preparing to send basic Telegram notification")
    if not TELEGRAM_BOT_TOKEN:
        log_message("Missing TELEGRAM_BOT_TOKEN, skipping notification. Please set TELEGRAM_BOT_TOKEN in environment variables.")
        return
    for chat_id in chat_ids:
        if not chat_id:
            log_message(f"Missing chat_id, skipping notification for one recipient")
            continue
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": f"<b>{subject}</b>\n\n{body}",
            "parse_mode": "HTML"
        }
        try:
            log_message(f"Sending Telegram message to chat_id {chat_id}: {body}")
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                log_message(f"Telegram notification sent successfully to chat_id {chat_id}")
            else:
                log_message(f"Telegram send failed for chat_id {chat_id}: {response.text}")
        except Exception as e:
            log_message(f"Error sending Telegram notification to chat_id {chat_id}: {str(e)}")

# Login to the website
def login(driver, max_retries=3):
    log_message("Navigating to login page: https://apps.cec.com.vn/login")
    driver.get("https://apps.cec.com.vn/login")
    current_id = os.getenv("NEW_CEC_USER")
    password = os.getenv("NEW_CEC_PASS")
    if not current_id or not password:
        log_message(f"Credentials missing: Username={current_id[:3] if current_id else 'None'}***, Password={'*' * len(password) if password else 'None'}")
        raise Exception("Missing credentials")

    for attempt in range(max_retries):
        try:
            log_message(f"Login attempt {attempt + 1}/{max_retries}")
            username_field = WebDriverWait(driver, 30).until(
                EC.visibility_of_element_located((By.ID, "input-14"))
            )
            driver.execute_script("arguments[0].value = '';", username_field)
            username_field.send_keys(current_id)

            log_message("Entering password")
            password_field = WebDriverWait(driver, 30).until(
                EC.visibility_of_element_located((By.ID, "input-18"))
            )
            driver.execute_script("arguments[0].value = '';", password_field)
            password_field.send_keys(password)

            log_message("Clicking login button")
            login_button = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
            )
            login_button.click()

            WebDriverWait(driver, 10).until_not(
                EC.url_contains("login")
            )
            log_message("Login successful")
            return True

        except Exception as e:
            log_message(f"Login attempt {attempt + 1}/{max_retries} failed: {str(e)}")
            try:
                error_message = driver.find_element(By.XPATH, "//div[contains(@class, 'error') or contains(text(), 'error') or contains(text(), 'sai')]")
                log_message(f"Login error message found: {error_message.text}")
                if "captcha" in error_message.text.lower():
                    log_message("CAPTCHA detected, cannot proceed automatically")
                    raise Exception("CAPTCHA required")
                if attempt == max_retries - 1:
                    log_message("Max login retries reached")
                    raise Exception(f"Login failed after {max_retries} attempts: {str(e)}")
            except:
                log_message("No specific error message found on login page")
                if attempt == max_retries - 1:
                    log_message("Max login retries reached")
                    raise Exception(f"Login failed after {max_retries} attempts: {str(e)}")
            time.sleep(3)
    return False

# Update Google Sheet (Report sheet)
def update_google_sheet(date, class_name, report_url, timestamp):
    log_message(f"Updating Google Sheet '{SHEET_NAME}' with Date {date}, Class {class_name}, URL {report_url}")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
            client = gspread.authorize(creds)
            sheet = client.open_by_key(SHEET_ID)
            worksheet = sheet.worksheet(SHEET_NAME)
            row_data = [date, class_name, report_url, timestamp]
            worksheet.append_row(row_data)
            log_message(f"Updated Google Sheet '{SHEET_NAME}' successfully: {date}, {class_name}, {report_url} at {timestamp}")
            return True
        except Exception as e:
            log_message(f"Attempt {attempt+1}/{max_retries} failed to update Google Sheet '{SHEET_NAME}': {str(e)}")
            if attempt == max_retries - 1:
                log_message(f"Error updating Google Sheet '{SHEET_NAME}': {str(e)}")
                return False
            time.sleep(3)
    return False

# Update ReportContent sheet
def update_report_content_sheet(extracted_data, class_name, date_str, lesson_title):
    log_message(f"Updating Google Sheet '{REPORT_CONTENT_SHEET}' with extracted data")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
            client = gspread.authorize(creds)
            sheet = client.open_by_key(SHEET_ID)
            worksheet = sheet.worksheet(REPORT_CONTENT_SHEET)
            check_time = datetime.now(tz=ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d %H:%M:%S")
            vocab_list = [(k, v) for k, v in extracted_data['new_vocabulary'].items()]
            sentence_list = [(k, v if isinstance(v, str) else '; '.join(v)) for k, v in extracted_data['sentence_structures'].items() if v]
            link_list = extracted_data['links']
            comments = extracted_data['student_comments_minh_huy'] or 'Không có nhận xét'
            rows = [
                ["----------", "", "", "", ""],
                [f"Class: {class_name}", "", f"Date: {date_str}", "", f"Lesson: {lesson_title}"]
            ]
            if link_list:
                rows.append([f"Links: {'; '.join(link_list)}", "", "", "", ""])
            rows.append([f"Check Time: {check_time}", "", "", "", ""])
            rows.append([f"Comments about Minh Huy: {comments}", "", "", "", ""])
            num_rows = max(len(vocab_list), len(sentence_list), 1)
            for i in range(num_rows):
                row = [
                    vocab_list[i][0] if i < len(vocab_list) else "",
                    vocab_list[i][1] if i < len(vocab_list) else "",
                    f"{sentence_list[i][0]}:{sentence_list[i][1]}" if i < len(sentence_list) else "",
                    "",
                    ""
                ]
                rows.append(row)
            worksheet.insert_rows(rows, row=2)
            log_message(f"Inserted {len(rows)} rows to '{REPORT_CONTENT_SHEET}' after header")
            return True
        except Exception as e:
            log_message(f"Attempt {attempt+1}/{max_retries} failed to update Google Sheet '{REPORT_CONTENT_SHEET}': {str(e)}")
            if attempt == max_retries - 1:
                log_message(f"Error updating Google Sheet '{REPORT_CONTENT_SHEET}': {str(e)}")
                return False
            time.sleep(3)
    return False

# Update vocab sheet
def update_vocab_sheet(total_vocab):
    log_message(f"Updating Google Sheet '{VOCAB_SHEET}' with total vocabulary")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
            client = gspread.authorize(creds)
            sheet = client.open_by_key(SHEET_ID)
            worksheet = sheet.worksheet(VOCAB_SHEET)
            worksheet.clear()
            worksheet.append_row(["Word", "Meaning"])
            vocab_rows = [[item['word'], item['meaning']] for item in total_vocab if isinstance(item, dict)]
            if vocab_rows:
                worksheet.append_rows(vocab_rows)
            log_message(f"Updated Google Sheet '{VOCAB_SHEET}' successfully with {len(vocab_rows)} vocabulary entries")
            return True
        except Exception as e:
            log_message(f"Attempt {attempt+1}/{max_retries} failed to update Google Sheet '{VOCAB_SHEET}': {str(e)}")
            if attempt == max_retries - 1:
                log_message(f"Error updating Google Sheet '{VOCAB_SHEET}': {str(e)}")
                return False
            time.sleep(3)
    return False

# Save processed data
def save_processed(date, class_name, report_url):
    log_message(f"Saving processed data to {PROCESSED_FILE}")
    processed = {"date": date, "class_name": class_name, "report_url": report_url}
    try:
        with open(PROCESSED_FILE, 'w', encoding='utf-8') as f:
            json.dump(processed, f, indent=2)
        log_message(f"Saved {PROCESSED_FILE} successfully")
    except Exception as e:
        log_message(f"Error saving {PROCESSED_FILE}: {str(e)}")

# Check if in a git repository
def is_git_repository():
    try:
        subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], check=True, capture_output=True, text=True)
        log_message("Git repository detected")
        return True
    except Exception as e:
        log_message(f"Not a git repository: {str(e)}")
        return False

# Escape MarkdownV2 characters
def escape_markdown_v2(text):
    special_chars = r'([_*[\](){}~`>#+=|.!-])'
    return re.sub(special_chars, r'\\\g<1>', text)

# Send detailed Telegram message

# ────────────────────────────────────────────────
#    CÔNG CỤ TẠO PHIẾU CHÉP A4 & QUIZLET TỰ ĐỘNG
# ────────────────────────────────────────────────

def get_merged_vocabulary(current_vocab, report_dir='Report', current_date=None, target_max=40, **kwargs):
    """
    Quy tắc gộp từ vựng ôn tập:
    - Nếu bài học >= target_max (40 từ): Giữ nguyên toàn bộ (không bị giới hạn 40).
    - Nếu bài học < target_max (40 từ): Lấy từ vựng các bài trước (không trùng lặp),
      lần lượt qua các bài trước cho đến khi đạt đủ target_max (40 từ).
    """
    merged = dict(current_vocab)
    
    # Nếu bài hiện tại đã đủ từ (>= target_max) thì giữ nguyên toàn bộ (kể cả > 40 từ)
    if len(merged) >= target_max:
        return merged, [("Bài hiện tại", len(merged))]

    if not os.path.exists(report_dir):
        return merged, [("Bài hiện tại", len(merged))]

    report_files = sorted(
        [f for f in os.listdir(report_dir) if f.endswith('.json')],
        reverse=True
    )

    # Chỉ lấy các báo cáo diễn ra TRƯỚC ngày hiện tại (file_date < current_date)
    filtered_reports = []
    for f in report_files:
        if current_date:
            file_date = f[:10]
            if file_date >= current_date:
                continue
        filtered_reports.append(os.path.join(report_dir, f))

    sources = [("Bài hiện tại", len(merged))]
    existing_keys = {k.strip().lower() for k in merged.keys()}

    for rf in filtered_reports:
        if len(merged) >= target_max:
            break
        try:
            with open(rf, 'r', encoding='utf-8') as fp:
                data = json.load(fp)
            prev_vocab = data.get('new_vocabulary', {})
            added_from_this = 0

            if isinstance(prev_vocab, dict):
                for k, v in prev_vocab.items():
                    k_clean = k.strip()
                    if k_clean and v and k_clean.lower() not in existing_keys:
                        merged[k_clean] = v.strip()
                        existing_keys.add(k_clean.lower())
                        added_from_this += 1
                        if len(merged) >= target_max:
                            break
            elif isinstance(prev_vocab, list):
                for item in prev_vocab:
                    if isinstance(item, dict):
                        w = item.get('word', '').strip()
                        m = item.get('meaning', '').strip()
                        if w and m and w.lower() not in existing_keys:
                            merged[w] = m
                            existing_keys.add(w.lower())
                            added_from_this += 1
                            if len(merged) >= target_max:
                                break

            if added_from_this > 0:
                report_name = os.path.basename(rf).replace('.json', '')
                sources.append((report_name, added_from_this))
        except Exception:
            continue

    return merged, sources

def generate_a4_worksheet(date_str, lesson_title, vocab_dict, output_pdf_path):
    import shutil
    vocab_items = [{"en": k, "vi": v} for k, v in vocab_dict.items() if k and v]
    total_words = len(vocab_items)
    if total_words == 0:
        return None

    if total_words <= 15:
        pages = [vocab_items]
        row_height = "15.0mm"
    elif total_words <= 20:
        pages = [vocab_items]
        row_height = "11.5mm"
    elif total_words <= 40:
        mid = (total_words + 1) // 2
        pages = [vocab_items[:mid], vocab_items[mid:]]
        max_page_items = max(len(pages[0]), len(pages[1]))
        if max_page_items <= 16:
            row_height = "14.2mm"
        elif max_page_items <= 18:
            row_height = "12.8mm"
        else:
            row_height = "11.5mm"
    else:
        per_page = 24
        pages = [vocab_items[i:i + per_page] for i in range(0, total_words, per_page)]
        row_height = "9.5mm"

    total_pages = len(pages)

    def render_page(items, page_num):
        rows = []
        for item in items:
            rows.append(f"""          <tr>
            <td class="col-en">{item['en']}</td>
            <td class="col-vi">{item['vi']}</td>
            <td class="col-write"><div class="handwriting-box"></div></td>
            <td class="col-write"><div class="handwriting-box"></div></td>
            <td class="col-write"><div class="handwriting-box"></div></td>
          </tr>""")
        rows_html = "\n".join(rows)

        return f"""
    <div class="a4-page">
      <div class="page-head">
        <div class="title-main">PHIẾU TẬP CHÉP TỪ VỰNG TIẾNG ANH</div>
        <div class="info-bar">
          <div>Họ và tên: <strong>LÊ MINH HUY</strong></div>
          <div>Lớp: <strong>VQ2-C3-2602</strong></div>
          <div>Bài học: <strong>{lesson_title}</strong></div>
          <div style="text-align: right;">Ngày: {date_str}</div>
        </div>
      </div>

      <table class="vocab-grid">
        <thead>
          <tr>
            <th style="width: 16%;">Từ Vựng</th>
            <th style="width: 16%;">Nghĩa</th>
            <th style="width: 22.66%;">Lần 1 (Tập chép)</th>
            <th style="width: 22.67%;">Lần 2 (Tập chép)</th>
            <th style="width: 22.67%;">Lần 3 (Tập chép)</th>
          </tr>
        </thead>
        <tbody>
{rows_html}
        </tbody>
      </table>

      <div class="page-foot">
        <div>Học sinh: Lê Minh Huy &bull; Lớp VQ2-C3-2602 &bull; Trung tâm CEC</div>
        <div>Trang {page_num} / {total_pages} (Tổng {total_words} từ)</div>
      </div>
    </div>"""

    pages_html = "\n".join(render_page(page_items, idx + 1) for idx, page_items in enumerate(pages))

    html_content = f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <title>Phiếu Tập Chép Từ Vựng - {lesson_title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Tahoma, Geneva, Verdana, sans-serif;
      background-color: #ffffff;
      color: #000000;
      line-height: 1.2;
      -webkit-font-smoothing: antialiased;
    }}
    .a4-page {{
      background: #ffffff;
      width: 210mm;
      min-height: 286mm;
      max-height: 286mm;
      padding: 6mm 5mm 5mm 5mm;
      margin: 0 auto 30px auto;
      box-sizing: border-box;
      display: flex;
      flex-direction: column;
      justify-content: flex-start;
      overflow: hidden;
      page-break-after: always;
      break-after: page;
    }}
    .a4-page:last-child {{
      page-break-after: auto;
      break-after: auto;
    }}
    .page-head {{
      border-bottom: 2px solid #000000;
      padding-bottom: 4px;
      margin-bottom: 5px;
    }}
    .title-main {{
      font-size: 18px;
      font-weight: 900;
      text-align: center;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #000000;
      margin-bottom: 3px;
    }}
    .info-bar {{
      display: grid;
      grid-template-columns: 2.2fr 1.3fr 1.3fr 1fr;
      font-size: 12px;
      font-weight: 800;
      color: #000000;
      padding: 2px 0;
    }}
    .vocab-grid {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      flex-grow: 1;
    }}
    .vocab-grid th {{
      background: #ffffff;
      color: #000000;
      font-size: 12.5px;
      font-weight: 900;
      text-transform: uppercase;
      border: 2px solid #000000;
      padding: 4px 2px;
      text-align: center;
    }}
    .vocab-grid td {{
      border: 1.5px solid #000000;
      padding: 0 4px;
      vertical-align: middle;
      height: {row_height};
    }}
    .col-en {{
      width: 16%;
      font-weight: 900;
      font-size: 13.5px;
      color: #000000;
      word-break: break-word;
      line-height: 1.25;
      padding: 0 4px !important;
    }}
    .col-vi {{
      width: 16%;
      font-size: 12px;
      font-weight: 700;
      color: #000000;
      word-break: break-word;
      line-height: 1.25;
      padding: 0 4px !important;
    }}
    .col-write {{
      width: 22.66%;
      padding: 0 !important;
      position: relative;
    }}
    .handwriting-box {{
      width: 100%;
      height: 100%;
      min-height: {row_height};
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .handwriting-box::after {{
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      top: 50%;
      border-top: 1.2px dashed #000000;
      pointer-events: none;
    }}
    .page-foot {{
      border-top: 1.5px solid #000000;
      margin-top: 4px;
      padding-top: 2px;
      display: flex;
      justify-content: space-between;
      font-size: 10px;
      font-weight: 700;
      color: #000000;
    }}
    @page {{
      size: A4 portrait;
      margin: 6mm 5mm 5mm 5mm;
    }}
    @media print {{
      body {{ background: #ffffff !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .a4-page {{
        width: 100% !important;
        min-height: 286mm !important;
        max-height: 286mm !important;
        margin: 0 !important;
        padding: 6mm 5mm 5mm 5mm !important;
        box-shadow: none !important;
        border: none !important;
        page-break-after: always !important;
        break-after: page !important;
      }}
      .a4-page:last-child {{
        page-break-after: auto !important;
        break-after: auto !important;
      }}
    }}
  </style>
</head>
<body>
{pages_html}
</body>
</html>
"""
    temp_html_path = output_pdf_path.replace('.pdf', '.html')
    with open(temp_html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    chrome_binary = None
    if sys.platform == 'win32':
        win_chrome = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
        if os.path.exists(win_chrome):
            chrome_binary = win_chrome
    else:
        chrome_binary = shutil.which('google-chrome') or shutil.which('google-chrome-stable') or shutil.which('chromium')

    if not chrome_binary:
        log_message('Warning: Chrome binary not found, skipping PDF generation')
        return None

    try:
        cmd = [
            chrome_binary,
            '--headless',
            '--disable-gpu',
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--no-pdf-header-footer',
            f'--print-to-pdf={output_pdf_path}',
            temp_html_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        log_message(f'Successfully generated PDF worksheet: {output_pdf_path}')
        return output_pdf_path
    except Exception as e:
        log_message(f'Failed to generate PDF worksheet: {str(e)}')
        return None

def generate_quizlet_file(vocab_dict, output_txt_path):
    try:
        with open(output_txt_path, 'w', encoding='utf-8') as f:
            for k, v in vocab_dict.items():
                if k and v:
                    f.write(f"{k.strip()}\t{v.strip()}\n")
        log_message(f'Successfully generated Quizlet file: {output_txt_path}')
        return output_txt_path
    except Exception as e:
        log_message(f'Failed to generate Quizlet file: {str(e)}')
        return None

async def send_detailed_telegram_message(bot, chat_id, result_data, worksheet_pdf=None, quizlet_txt=None, vocab_sources=None):
    try:
        general_info = (
            f"*BÁO CÁO BÀI HỌC - {result_data['report_date']}*\n"
            f"📅 *Ngày*: {result_data['report_date']}\n"
            f"📚 *Tiêu đề*: {result_data['lesson_title']}\n"
            f"🏫 *Lớp*: {result_data['class_name']}"
        )
        log_message(f"Sending general info message to chat_id {chat_id}: {general_info[:100]}...")
        await bot.send_message(chat_id=chat_id, text=escape_markdown_v2(general_info), parse_mode='MarkdownV2')
        log_message(f"Sent general info message to chat_id {chat_id}")
        await asyncio.sleep(0.5)

        vocab_text = f"*TỪ VỰNG MỚI - {result_data['report_date']}*\n" + "\n".join(
            f"• `{k}`: {v}" for k, v in result_data['new_vocabulary'].items()
        )
        if result_data['new_vocabulary']:
            log_message(f"Sending vocabulary message to chat_id {chat_id}: {vocab_text[:100]}...")
            await bot.send_message(chat_id=chat_id, text=escape_markdown_v2(vocab_text), parse_mode='MarkdownV2')
            log_message(f"Sent vocabulary message to chat_id {chat_id}")
            await asyncio.sleep(0.5)

        sentence_text = f"*CẤU TRÚC CÂU - {result_data['report_date']}*\n" + "\n".join(
            f"• *{k}*: {v if isinstance(v, str) else ', '.join(v)}"
            for k, v in result_data['sentence_structures'].items()
            if v is not None
        )
        if result_data['sentence_structures']:
            log_message(f"Sending sentence structures message to chat_id {chat_id}: {sentence_text[:100]}...")
            await bot.send_message(chat_id=chat_id, text=escape_markdown_v2(sentence_text), parse_mode='MarkdownV2')
            log_message(f"Sent sentence structures message to chat_id {chat_id}")
            await asyncio.sleep(0.5)

        homework_text = f"*BÀI TẬP VỀ NHÀ - {result_data['report_date']}*\n{result_data['homework']}"
        if result_data['homework'] and result_data['homework'] != "cannot find info":
            log_message(f"Sending homework message to chat_id {chat_id}: {homework_text[:100]}...")
            await bot.send_message(chat_id=chat_id, text=escape_markdown_v2(homework_text), parse_mode='MarkdownV2')
            log_message(f"Sent homework message to chat_id {chat_id}")
            await asyncio.sleep(0.5)

        comments_text = f"*NHẬN XÉT VỀ MINH HUY - {result_data['report_date']}*\n{result_data['student_comments_minh_huy'] or 'Không có nhận xét'}"
        if result_data['student_comments_minh_huy'] and result_data['student_comments_minh_huy'] != "cannot find info":
            log_message(f"Sending comments message to chat_id {chat_id}: {comments_text[:100]}...")
            await bot.send_message(chat_id=chat_id, text=escape_markdown_v2(comments_text), parse_mode='MarkdownV2')
            log_message(f"Sent comments message to chat_id {chat_id}")
            await asyncio.sleep(0.5)

        is_padded = vocab_sources and len(vocab_sources) > 1
        total_practice_words = sum(s[1] for s in vocab_sources) if vocab_sources else len(result_data.get('new_vocabulary', {}))

        # Chỉ gửi thêm duy nhất file PDF tập chép A4 chuẩn in (không gửi file text quizlet)
        if worksheet_pdf and os.path.exists(worksheet_pdf):
            try:
                log_message(f"Sending A4 worksheet PDF to chat_id {chat_id}")
                if is_padded:
                    new_count = vocab_sources[0][1]
                    review_count = total_practice_words - new_count
                    pdf_caption = (
                        f"📄 Phiếu tập chép 5 cột A4 - {result_data.get('lesson_title', '')} "
                        f"(Đã ghép đủ {total_practice_words} từ: {new_count} từ mới + {review_count} từ ôn tập)"
                    )
                else:
                    pdf_caption = f"📄 Phiếu tập chép 5 cột A4 - {result_data.get('lesson_title', '')} ({result_data['report_date']})"
                with open(worksheet_pdf, "rb") as f_doc:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=f_doc,
                        filename=os.path.basename(worksheet_pdf),
                        caption=pdf_caption
                    )
                log_message(f"Sent A4 worksheet PDF to chat_id {chat_id}")
                await asyncio.sleep(0.5)
            except Exception as e:
                log_message(f"Error sending worksheet PDF: {str(e)}")

    except Exception as e:
        log_message(f"Failed to send detailed Telegram messages to chat_id {chat_id}: {str(e)}")
        raise

# Fallback parser trực tiếp từ nội dung văn bản PDF khi Gemini gặp lỗi hạn ngạch (429)
def parse_report_from_text(pdf_text, date_str, pdf_links=None):
    log_message("Executing fallback regex parser on PDF text")
    lesson_title = "Lesson"
    m_lesson = re.search(r'(?:Lesson|Bài học)[\s:：]*([^\n\r]+)', pdf_text, re.IGNORECASE)
    if m_lesson:
        lesson_title = m_lesson.group(1).strip()
    else:
        m_unit = re.search(r'(Unit\s+\d+[^\n\r]*)', pdf_text, re.IGNORECASE)
        if m_unit:
            lesson_title = m_unit.group(1).strip()

    comments = ""
    m_comment = re.search(r'Minh Huy\s*[:：]\s*([^\n\r]+(?:\n[^\n\r]+)*?)(?=\n\s*\.\s*[A-Z]|\n\s*PHẦN|\n\s*Cambridge|\Z)', pdf_text, re.IGNORECASE)
    if m_comment:
        comments = re.sub(r'\s+', ' ', m_comment.group(1)).strip()

    homework = ""
    m_hw = re.search(r'(?:Homework|Bài tập về nhà)[^\n\r]*:\s*([\s\S]*?)(?=\n\s*Minh Huy|\n\s*Nhận xét|\Z)', pdf_text, re.IGNORECASE)
    if m_hw:
        homework = re.sub(r'\s+', ' ', m_hw.group(1)).strip()

    vocab = {}
    lines = pdf_text.splitlines()
    for line in lines:
        line = line.strip()
        if not line or len(line) > 80:
            continue
        pairs = re.findall(r'([a-zA-Z\s\-]{2,25})\s*[:：]\s*([a-zA-ZÀ-ỹ0-9\s,\(\)\/\.]{2,40})', line)
        for w, m in pairs:
            w_clean = w.strip().lower()
            m_clean = m.strip()
            if any(skip in w_clean for skip in ['class', 'lesson', 'date', 'skills', 'link', 'audio', 'phonic']):
                continue
            if w_clean and m_clean:
                vocab[w_clean] = m_clean

    log_message(f"Fallback extracted: title='{lesson_title}', vocab_count={len(vocab)}")
    return {
        "new_vocabulary": vocab,
        "sentence_structures": {},
        "report_date": date_str,
        "lesson_title": lesson_title if lesson_title != "cannot find info" else f"Lesson ({date_str})",
        "homework": homework or "Ôn tập nội dung bài học và làm bài tập theo hướng dẫn của giáo viên.",
        "links": pdf_links or [],
        "student_comments_minh_huy": comments or "Không có nhận xét riêng trong báo cáo."
    }

# Get available Gemini model
def get_available_model(attempt=0):
    candidate_order = [
        'gemini-2.5-flash',
        'gemini-2.5-flash-lite',
        'gemini-flash-latest',
        'gemini-3.5-flash',
        'gemini-3.5-flash-lite'
    ]
    try:
        models = [model.name for model in genai.list_models() if 'generateContent' in model.supported_generation_methods]
        log_message(f"Available Gemini models count: {len(models)}")
        if attempt < len(candidate_order):
            target = candidate_order[attempt]
            for m in models:
                if target in m:
                    return m
        for m in models:
            if 'flash' in m.lower():
                return m
        return models[0] if models else 'models/gemini-2.5-flash'
    except Exception as e:
        log_message(f"Failed to list models: {str(e)}")
        return 'models/gemini-2.5-flash'

# Fix invalid report date
def fix_report_date(date_str, fallback_date):
    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        return parsed_date.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        parsed_fallback = datetime.strptime(fallback_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        return parsed_fallback.strftime("%Y-%m-%d")

# Fix invalid JSON
def fix_invalid_json(text):
    text = text.strip()
    text = re.sub(r'^\]\s*|\]\s*$', '', text)
    if not text.startswith('{'):
        text = '{' + text
    if not text.endswith('}'):
        text = text + '}'
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        return json.dumps({
            "new_vocabulary": {"pot": "cái nồi"},
            "sentence_structures": {},
            "report_date": "cannot find info",
            "lesson_title": "cannot find info",
            "homework": "cannot find info",
            "links": [],
            "student_comments_minh_huy": "cannot find info"
        })

# Clean response text from Gemini API
def clean_response_text(text):
    text = text.strip()
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
    elif text.startswith('```') and text.endswith('```'):
        text = text[3:-3].strip()
    return text

# Main processing function
def process_report():
    log_message("Starting report check for calendar overview")
    if not check_network():
        log_message("Network unavailable, aborting process")
        return

    processed = {}
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r', encoding='utf-8') as f:
                processed = json.load(f)
            log_message(f"Loaded {PROCESSED_FILE}: {processed}")
        except Exception as e:
            log_message(f"Error reading {PROCESSED_FILE}: {str(e)}")

    if 'GOOGLE_CREDENTIALS' in os.environ:
        try:
            log_message("Writing Google credentials to credentials.json")
            creds_content = os.environ['GOOGLE_CREDENTIALS'].strip().encode('utf-8').decode('utf-8-sig')
            with open(CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
                json.dump(json.loads(creds_content), f, indent=2, ensure_ascii=False)
            log_message("Credentials written successfully")
        except Exception as e:
            log_message(f"Error writing credentials.json: {str(e)}")
            return

    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")
    log_message("Initializing Chrome WebDriver")
    driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(60)
    log_message(f"WebDriver session ID: {driver.session_id}")

    try:
        if not check_webdriver(driver):
            driver = restart_webdriver(driver, options)
        if not login(driver):
            log_message("Login failed, aborting process")
            return

        log_message("Navigating to calendar overview page: https://apps.cec.com.vn/student-calendar/overview")
        driver.get("https://apps.cec.com.vn/student-calendar/overview")
        time.sleep(7)
        if not check_webdriver(driver):
            driver = restart_webdriver(driver, options)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        log_message("Scrolled to bottom of page")

        class_events = WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'v-event') and @data-date]"))
        )
        log_message(f"Found {len(class_events)} class events")

        latest_date = None
        latest_event = None
        for event in class_events:
            date_str = event.get_attribute("data-date")
            try:
                event_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
                if event_date < TODAY and (latest_date is None or event_date > latest_date):
                    latest_date = event_date
                    latest_event = event
            except Exception as e:
                log_message(f"Error parsing date {date_str}: {str(e)}")
                continue

        if not latest_date:
            log_message("No classes found before today")
            return

        date_str = latest_date.strftime("%Y-%m-%d")
        log_message(f"Latest class date before today: {date_str}")

        driver.execute_script("arguments[0].click();", latest_event)
        time.sleep(3)

        popup = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.XPATH, "//div[contains(@class, 'v-menu__content') and contains(@class, 'menuable__content__active')]"))
        )
        title_element = popup.find_element(By.CLASS_NAME, "v-toolbar__title")
        title_text = title_element.text.strip()
        class_name = title_text.split(" : ")[-1] if " : " in title_text else "Unknown"
        log_message(f"Class name from popup: {class_name}")

        if (processed.get("date") == date_str and
                processed.get("class_name") == class_name and
                processed.get("report_url")):
            log_message(f"Class {class_name} on {date_str} already processed with report URL: {processed['report_url']}")
            return

        report_button = popup.find_element(By.XPATH, "//button[.//p[text()='Báo cáo bài học']]")
        is_enabled = "v-btn--disabled" not in report_button.get_attribute("class") and report_button.is_enabled()

        if is_enabled:
            log_message("Report button is enabled, clicking to get report URL")
            original_window = driver.current_window_handle
            max_window_retries = 3
            report_url = None
        
            for attempt in range(max_window_retries):
                try:
                    if not check_webdriver(driver):
                        log_message("WebDriver unresponsive before clicking report button, restarting")
                        driver = restart_webdriver(driver, options)
                        # Re-navigate to calendar page and re-open popup
                        driver.get("https://apps.cec.com.vn/student-calendar/overview")
                        time.sleep(7)
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        driver.execute_script("arguments[0].click();", latest_event)
                        time.sleep(3)
                        popup = WebDriverWait(driver, 10).until(
                            EC.visibility_of_element_located((By.XPATH, "//div[contains(@class, 'v-menu__content') and contains(@class, 'menuable__content__active')]"))
                        )
                        report_button = popup.find_element(By.XPATH, "//button[.//p[text()='Báo cáo bài học']]")
        
                    log_message(f"Attempt {attempt + 1}/{max_window_retries} to click report button")
                    report_button.click()
        
                    # Wait for new window to open
                    WebDriverWait(driver, 15).until(
                        lambda d: len(d.window_handles) > len([original_window])
                    )
                    for window_handle in driver.window_handles:
                        if window_handle != original_window:
                            driver.switch_to.window(window_handle)
                            break
        
                    # Wait for the new window to load
                    WebDriverWait(driver, 60).until(
                        EC.url_contains("docs.google.com")
                    )
                    report_url = driver.current_url
                    log_message(f"Report URL: {report_url}")
                    break
                except Exception as e:
                    log_message(f"Window switch attempt {attempt + 1}/{max_window_retries} failed: {str(e)}")
                    if attempt == max_window_retries - 1:
                        log_message("Max retries reached for window switch")
                        raise Exception(f"Failed to retrieve report URL after {max_window_retries} attempts: {str(e)}")
                    # Restart WebDriver if unresponsive
                    if "connection refused" in str(e).lower() or "timeout" in str(e).lower():
                        driver = restart_webdriver(driver, options)
                        # Re-navigate to calendar page
                        driver.get("https://apps.cec.com.vn/student-calendar/overview")
                        time.sleep(7)
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        driver.execute_script("arguments[0].click();", latest_event)
                        time.sleep(3)
                        popup = WebDriverWait(driver, 10).until(
                            EC.visibility_of_element_located((By.XPATH, "//div[contains(@class, 'v-menu__content') and contains(@class, 'menuable__content__active')]"))
                        )
                        report_button = popup.find_element(By.XPATH, "//button[.//p[text()='Báo cáo bài học']]")
                    time.sleep(5)  # Wait longer before retrying
        
            if report_url:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                body = f"Báo cáo bài học mới cho lớp {class_name} ngày {date_str}\nLink: {report_url}"
                log_message(f"Found new report: {body}")
                update_google_sheet(date_str, class_name, report_url, timestamp)
                save_processed(date_str, class_name, report_url)



                driver.close()
                driver.switch_to.window(original_window)

                # Segment B: Process the report PDF
                log_message("Starting PDF processing for report analysis")
                if not API_KEY:
                    log_message("Missing GEMINI_API_KEY, skipping PDF processing. Please set GEMINI_API_KEY in environment variables.")
                    return

                genai.configure(api_key=API_KEY)
                log_message(f"Extracting direct PDF URL from {report_url}")
                parsed_url = urlparse(report_url)
                query_params = parse_qs(parsed_url.query)
                direct_pdf_url = query_params.get('url', [None])[0]

                if not direct_pdf_url:
                    log_message("Could not extract direct PDF URL from Google Docs viewer")
                    return

                pdf_path = 'temp_report.pdf'
                log_message(f"Downloading PDF from {direct_pdf_url}")
                try:
                    response = requests.get(direct_pdf_url, timeout=10)
                    response.raise_for_status()
                    content_type = response.headers.get('content-type', '')
                    if 'application/pdf' not in content_type:
                        log_message(f"Downloaded file is not a PDF (Content-Type: {content_type})")
                        return
                    with open(pdf_path, 'wb') as f:
                        f.write(response.content)
                    log_message(f"Successfully downloaded PDF to {pdf_path}")
                except requests.RequestException as e:
                    log_message(f"Failed to download PDF: {str(e)}")
                    return

                pdf_text = ''
                pdf_links = []
                log_message("Extracting text and links from PDF")
                try:
                    with pdfplumber.open(pdf_path) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text()
                            pdf_text += text or ''
                            if page.annots:
                                for annot in page.annots:
                                    if 'uri' in annot:
                                        pdf_links.append(annot['uri'])
                    log_message(f"Extracted {len(pdf_text)} characters and {len(pdf_links)} links from PDF")
                except Exception as e:
                    log_message(f"Failed to extract text or links from PDF: {str(e)}")
                    os.remove(pdf_path)
                    return
                finally:
                    if os.path.exists(pdf_path):
                        os.remove(pdf_path)
                        log_message(f"Deleted temporary PDF file: {pdf_path}")

                if not pdf_text:
                    log_message("No text extracted from PDF")
                    return

                system_prompt = """
                You are an AI extractor that **must** output in strict JSON format with no extra text, comments, or markdown. The output must be a valid JSON object. Do not wrap the JSON in code blocks or add any explanation. If you cannot extract information, return "cannot find info" for strings or {} or [] for objects/arrays.
                Extract from the given text:
                {
                  "new_vocabulary": {},  // Dictionary of new English words/phrases (key: word/phrase in lowercase, value: meaning in Vietnamese, must not be empty)
                  "sentence_structures": {},  // Dictionary of question-answer pairs (key: question, value: answer or list of answers if multiple, no null values)
                  "report_date": "",  // Report date in YYYY-MM-DD (if not found, use date from input JSON)
                  "lesson_title": "",  // Lesson title (if not found, "cannot find info")
                  "homework": "",  // Homework description with any associated links (if not found, "cannot find info")
                  "links": [],  // List of all URLs found in the content (e.g., homework links, YouTube videos)
                  "student_comments_minh_huy": ""  // Comments about student Minh Huy (if not found, "cannot find info")
                }
                For new_vocabulary, provide meanings in Vietnamese (e.g., {"pen": "cái bút"}). Every word must have a non-empty meaning. For missing meanings, use a default dictionary (e.g., "pot": "cái nồi").
                For sentence_structures, map questions to answers (e.g., {"What is this?": "It's a pen."} or {"What are they?": ["They are scissors.", "They are books."]}). If no sentence structures found, return {}.
                Include all URLs (e.g., YouTube, Google Drive, Quizlet) in the links field, especially those related to homework.
                Use date from input JSON if report_date is not found in text.
                Ensure the output is a valid JSON object with all required fields.
                """

                max_attempts = 3
                extracted_data = None
                best_response = None
                for attempt in range(max_attempts):
                    log_message(f"Gemini API attempt {attempt + 1}/{max_attempts}")
                    model_name = get_available_model(attempt)
                    if not model_name:
                        log_message("No suitable model found. Using default response.")
                        break
                    log_message(f"Using model: {model_name}")
                    try:
                        model = genai.GenerativeModel(model_name, system_instruction=system_prompt)
                        response = model.generate_content(pdf_text)
                        cleaned_text = clean_response_text(response.text)
                        cleaned_text = fix_invalid_json(cleaned_text)
                        log_message(f"Received API response (attempt {attempt + 1}): {cleaned_text[:100]}...")
                        extracted_data = json.loads(cleaned_text)
                        extracted_data['links'] = list(set(extracted_data.get('links', []) + pdf_links))
                        extracted_data['report_date'] = fix_report_date(extracted_data.get('report_date', date_str), date_str)
                        for word in extracted_data['new_vocabulary']:
                            if not extracted_data['new_vocabulary'][word]:
                                extracted_data['new_vocabulary'][word] = {
                                    "pot": "cái nồi"
                                }.get(word, "nghĩa không xác định")
                        extracted_data['sentence_structures'] = {
                            k: v for k, v in extracted_data['sentence_structures'].items()
                            if v is not None and (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v)))
                        }
                        if best_response is None or len(extracted_data.get('new_vocabulary', {})) > len(best_response.get('new_vocabulary', {})):
                            best_response = extracted_data
                        log_message(f"API attempt {attempt + 1} successful")
                        break
                    except Exception as e:
                        log_message(f"API attempt {attempt + 1}/{max_attempts} failed: {str(e)}")
                        time.sleep(3)
                        if attempt == max_attempts - 1:
                            log_message("All API attempts failed. Attempting recovery from cache or PDF text.")
                            recovered = None
                            if os.path.exists('Report'):
                                for rf in os.listdir('Report'):
                                    if rf.startswith(date_str) and rf.endswith('.json') and 'cannot_find_info' not in rf:
                                        try:
                                            with open(os.path.join('Report', rf), 'r', encoding='utf-8') as cf:
                                                cdata = json.load(cf)
                                                if cdata.get('new_vocabulary') and cdata.get('lesson_title') != 'cannot find info':
                                                    recovered = cdata
                                                    log_message(f"Successfully recovered report data from existing file: {rf}")
                                                    break
                                        except Exception:
                                            pass

                            if not recovered:
                                recovered = parse_report_from_text(pdf_text, date_str, pdf_links)

                            extracted_data = best_response or recovered

                update_report_content_sheet(extracted_data, class_name, date_str, extracted_data['lesson_title'])

                log_message("Processing total vocabulary")
                if os.path.exists(VOCAB_FILE):
                    try:
                        with open(VOCAB_FILE, 'r', encoding='utf-8') as f:
                            vocab_data = json.load(f)
                            if isinstance(vocab_data, dict) and 'vocabulary' in vocab_data:
                                total_vocab = vocab_data['vocabulary']
                            elif isinstance(vocab_data, list) and all(isinstance(item, str) for item in vocab_data):
                                total_vocab = [{"word": word, "meaning": ""} for word in vocab_data]
                            else:
                                total_vocab = []
                        log_message(f"Loaded existing vocabulary from {VOCAB_FILE}: {len(total_vocab)} entries")
                    except Exception as e:
                        log_message(f"Error reading {VOCAB_FILE}: {str(e)}. Starting with empty vocab.")
                        total_vocab = []
                else:
                    log_message(f"{VOCAB_FILE} does not exist. Starting with empty vocab.")
                    total_vocab = []

                new_vocab = extracted_data['new_vocabulary']
                new_vocab_lower = {k.lower(): v for k, v in new_vocab.items()}
                total_vocab_lower = {item['word'].lower(): item['meaning'] for item in total_vocab if isinstance(item, dict)}
                added_vocab = [
                    {"word": k, "meaning": v}
                    for k, v in new_vocab.items()
                    if k.lower() not in total_vocab_lower or total_vocab_lower.get(k.lower(), '') == ''
                ]
                total_vocab.extend(added_vocab)
                log_message(f"Added {len(added_vocab)} new vocabulary entries. Total vocabulary: {len(total_vocab)}")

                log_message(f"Saving updated vocabulary to {VOCAB_FILE}")
                with open(VOCAB_FILE, 'w', encoding='utf-8') as f:
                    json.dump({'vocabulary': total_vocab}, f, ensure_ascii=False, indent=4)
                log_message(f"Successfully saved {VOCAB_FILE}")

                update_vocab_sheet(total_vocab)

                log_message("Creating Report directory if not exists")
                os.makedirs('Report', exist_ok=True)
                # Làm sạch tiêu đề bài học tránh ký tự cấm trên Windows NTFS
                title_raw = extracted_data['lesson_title'] if extracted_data.get('lesson_title') else 'unknown'
                title = re.sub(r'[:"*?<>|\\/]', '_', title_raw).replace(' ', '_')
                result_filename = f"Report/{date_str}_{title}.json"

                # Tự động gộp từ vựng ôn tập nếu ít hơn 40 từ (lấy từ các bài trước đủ đúng 40 từ)
                practice_vocab, vocab_sources = get_merged_vocabulary(
                    extracted_data.get('new_vocabulary', {}),
                    report_dir='Report',
                    current_date=date_str,
                    target_max=40
                )
                log_message(f"Practice vocabulary count: {len(practice_vocab)} (Original lesson words: {len(extracted_data.get('new_vocabulary', {}))})")
                for src_name, cnt in vocab_sources:
                    log_message(f"  + {src_name}: {cnt} words")

                # Tự động tạo Phiếu tập chép A4 và File Quizlet 1-Click Import
                worksheet_pdf = f"Report/{date_str}_{title}_Phieu_Tap_Chep.pdf"
                quizlet_txt = f"Report/{date_str}_{title}_Quizlet_Import.txt"
                generate_a4_worksheet(date_str, extracted_data.get('lesson_title', title_raw), practice_vocab, worksheet_pdf)
                generate_quizlet_file(practice_vocab, quizlet_txt)

                result_data = {
                    **extracted_data,
                    'class_name': class_name,
                    'report_url': report_url,
                    'total_vocabulary': total_vocab
                }

                log_message(f"Saving result to {result_filename}")
                with open(result_filename, 'w', encoding='utf-8') as f:
                    json.dump(result_data, f, ensure_ascii=False, indent=4)
                log_message(f"Successfully saved: {result_filename}")

                async def send_report_to_telegram():
                    log_message("Initializing Telegram Bot for detailed messages")
                    bot = Bot(token=TELEGRAM_BOT_TOKEN)
                    chat_ids = list(dict.fromkeys(cid for cid in [TELEGRAM_CHAT_ID, TELEGRAM_CHAT_ID_2] if cid))
                    for chat_id in chat_ids:
                        log_message(f"Sending detailed Telegram messages to chat_id {chat_id}")
                        await send_detailed_telegram_message(bot, chat_id, result_data, worksheet_pdf, quizlet_txt, vocab_sources)
                        log_message(f"Completed sending detailed messages to chat_id {chat_id}")

                log_message("Starting detailed Telegram notifications")
                asyncio.run(send_report_to_telegram())
                log_message("Completed detailed Telegram notifications")

                if is_git_repository():
                    log_message("Committing and pushing Report and vocab files to GitHub")
                    try:
                        subprocess.run(["git", "config", "--global", "user.name", "GitHub Action"], check=True)
                        subprocess.run(["git", "config", "--global", "user.email", "action@github.com"], check=True)
                        subprocess.run(["git", "add", PROCESSED_FILE, LOG_FILE, VOCAB_FILE, "Report/*"], check=True)
                        subprocess.run(["git", "commit", "-m", f"Update report and vocab for {date_str}"], check=True)
                        subprocess.run(["git", "pull", "--rebase"], check=True)
                        subprocess.run(["git", "push"], check=True)
                        log_message(f"Pushed {PROCESSED_FILE}, {LOG_FILE}, {VOCAB_FILE}, and Report/* successfully")
                    except Exception as e:
                        log_message(f"Error committing/pushing Report and vocab files: {str(e)}")

        else:
            log_message("Report button is disabled")
    except Exception as e:
        log_message(f"Error checking reports: {str(e)}")
    finally:
        log_message("Closing WebDriver")
        driver.quit()

if __name__ == "__main__":
    log_message("Starting script")
    process_report()
    log_message("Script completed")
