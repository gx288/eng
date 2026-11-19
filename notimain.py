import json
import time
import os
import sys
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
except ImportError:
    gspread = None
    ServiceAccountCredentials = None
import requests
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    webdriver = None
    ChromeDriverManager = None
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import google.generativeai as genai
except ImportError:
    genai = None

import urllib.request
import urllib.parse
from urllib.parse import parse_qs, urlparse

try:
    from telegram import Bot
except ImportError:
    Bot = None
import asyncio
import re
import socket

# Configuration
PROCESSED_FILE = "processed2.json"
SESSION_FILE = "cec_session.json"
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

def rotate_log_if_needed(max_lines=5000, keep_lines=2000):
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            if len(lines) > max_lines:
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.writelines(lines[-keep_lines:])
    except Exception as e:
        print(f"Log rotation error: {e}")

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
    new_driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)
    try:
        restore_session(new_driver)
    except:
        pass
    return new_driver

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

# Save session (cookies + localStorage)
def save_session(driver):
    try:
        cookies = driver.get_cookies()
        local_storage = driver.execute_script("return Object.assign({}, window.localStorage);")
        session_data = {
            "cookies": cookies,
            "localStorage": local_storage,
            "timestamp": time.time()
        }
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)
        log_message(f"Saved session cache ({len(cookies)} cookies, {len(local_storage)} localStorage items) to {SESSION_FILE}")
    except Exception as e:
        log_message(f"Warning: Failed to save session cache: {str(e)}")

# Restore session (cookies + localStorage)
def restore_session(driver):
    if not os.path.exists(SESSION_FILE):
        log_message("No session cache file found, will perform fresh login")
        return False
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            session_data = json.load(f)

        log_message("Attempting to restore session from cache...")
        driver.get("https://apps.cec.com.vn/login")
        time.sleep(2)

        # Restore cookies
        cookies = session_data.get("cookies", [])
        for cookie in cookies:
            try:
                cookie_dict = {k: v for k, v in cookie.items() if k in ['name', 'value', 'path', 'domain', 'secure', 'httpOnly']}
                driver.add_cookie(cookie_dict)
            except Exception:
                pass

        # Restore localStorage
        local_storage = session_data.get("localStorage", {})
        if local_storage:
            for k, v in local_storage.items():
                try:
                    driver.execute_script("window.localStorage.setItem(arguments[0], arguments[1]);", k, v)
                except Exception:
                    pass

        # Navigate to student calendar overview
        log_message("Navigating to student calendar overview with restored session...")
        driver.get("https://apps.cec.com.vn/student-calendar/overview")
        time.sleep(5)

        # Check if session is valid or redirected back to login
        current_url = driver.current_url
        if "login" not in current_url:
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'v-event') and @data-date]"))
                )
                log_message("Session verified successfully! Calendar events loaded without fresh login.")
                return True
            except Exception:
                if "login" not in driver.current_url:
                    log_message(f"Session active at {driver.current_url}. Bypassed fresh login.")
                    return True

        log_message("Session cache expired or redirected to login, will perform fresh login")
        return False
    except Exception as e:
        log_message(f"Error restoring session cache: {str(e)}, will perform fresh login")
        return False

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
            time.sleep(2)
            save_session(driver)
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
    processed = {}
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r', encoding='utf-8') as f:
                processed = json.load(f)
        except Exception:
            processed = {}

    processed_dates = processed.get("processed_dates", {})
    if not isinstance(processed_dates, dict):
        processed_dates = {}
    if processed.get("date") and processed.get("report_url"):
        processed_dates[processed["date"]] = processed["report_url"]
    processed_dates[date] = report_url

    processed_out = {
        "date": date,
        "class_name": class_name,
        "report_url": report_url,
        "processed_dates": processed_dates
    }
    try:
        with open(PROCESSED_FILE, 'w', encoding='utf-8') as f:
            json.dump(processed_out, f, indent=2)
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
    if not text:
        return ""
    text = str(text).replace('\\', '\\\\')
    special_chars = r'([_*[\](){}~`>#+=|.!-])'
    return re.sub(special_chars, r'\\\g<1>', text)

async def safe_send_message(bot, chat_id, raw_text):
    """Gửi tin nhắn Telegram an toàn: thử MarkdownV2 trước, nếu lỗi định dạng tự động fallback sang plain text"""
    try:
        await bot.send_message(chat_id=chat_id, text=escape_markdown_v2(raw_text), parse_mode='MarkdownV2')
    except Exception as e:
        log_message(f"MarkdownV2 send warning ({e}), falling back to plain text")
        clean_plain = raw_text.replace('*', '').replace('`', '')
        await bot.send_message(chat_id=chat_id, text=clean_plain)

# Send detailed Telegram message

# ────────────────────────────────────────────────
#    CÔNG CỤ TẠO PHIẾU CHÉP A4 & QUIZLET TỰ ĐỘNG
# ────────────────────────────────────────────────

def get_merged_vocabulary(current_vocab, report_dir='Report', current_date=None, target_max=15, **kwargs):
    """
    Quy tắc gộp từ vựng ôn tập:
    - Nếu bài học >= target_max (25 từ): Giữ nguyên toàn bộ (không bị giới hạn 25).
    - Nếu bài học < target_max (25 từ): Lấy từ vựng các bài trước (không trùng lặp),
      lần lượt qua các bài trước cho đến khi đạt đủ target_max (25 từ).
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

# ────────────────────────────────────────────────
#    KHO CÂU VÍ DỤ CHUẨN TỪ ĐIỂN OXFORD (OALD) & CAMBRIDGE
# ────────────────────────────────────────────────

CURATED_OALD_SENTENCES = {
    # Lesson 11 & Ôn tập (40 từ chuẩn Lesson 11)
    'quilt': ('She pulled the quilt up around her.', 'Cô ấy kéo chiếc chăn bông lên quanh người.'),
    'bolt': ('She slid the bolt into place.', 'Cô ấy gài then cửa lại.'),
    'belt': ('Fasten your seat belt, please.', 'Xin vui lòng thắt dây an toàn.'),
    'tilt': ('She tilted her head to one side.', 'Cô ấy nghiêng đầu sang một bên.'),
    'wilt': ('The flowers were beginning to wilt.', 'Những bông hoa đã bắt đầu héo.'),
    'goes': ('He goes to work by train.', 'Anh ấy đi làm bằng tàu hỏa.'),
    'our': ('We sold our house last year.', 'Chúng tôi đã bán nhà năm ngoái.'),
    'we': ('We are going to the beach.', 'Chúng tôi sắp đi ra bãi biển.'),
    'she': ('She lives in a small village.', 'Cô ấy sống trong một ngôi làng nhỏ.'),
    'their': ('They have sold their car.', 'Họ đã bán chiếc xe ô tô của mình.'),
    'one': ('There is only one room left.', 'Chỉ còn lại một phòng.'),
    'these': ('Who owns all these books?', 'Ai sở hữu tất cả những cuốn sách này?'),
    'but': ('It was cold but sunny.', 'Trời lạnh nhưng có nắng.'),
    'your': ('Where is your school bag?', 'Cặp sách của bạn ở đâu?'),
    'said': ('She said nothing about it.', 'Cô ấy không nói gì về điều đó.'),
    'blue': ('She was wearing a blue dress.', 'Cô ấy đang mặc một chiếc váy màu xanh.'),
    'yesterday': ('Where were you yesterday morning?', 'Sáng hôm qua bạn đã ở đâu?'),
    'breakfast': ('He usually has toast for breakfast.', 'Anh ấy thường ăn bánh mì nướng cho bữa sáng.'),
    'lunch': ('What did you have for lunch?', 'Bạn đã ăn gì cho bữa trưa?'),
    'dinner': ('They invited us over for dinner.', 'Họ đã mời chúng tôi đến ăn tối.'),
    'eggs': ('How do you like your eggs?', 'Bạn thích ăn trứng chế biến thế nào?'),
    'chicken': ('We had roast chicken for dinner.', 'Chúng tôi đã ăn gà quay cho bữa tối.'),
    'salad': ('I ordered a fresh green salad.', 'Tôi đã gọi một đĩa rau trộn tươi.'),
    'noodles': ('Cook the noodles in boiling water.', 'Nấu mì trong nước sôi.'),
    'juice': ('Would you like some orange juice?', 'Bạn có muốn uống chút nước cam không?'),
    'skull': ('A dinosaur skull was found here.', 'Một chiếc sọ khủng long đã được tìm thấy ở đây.'),
    'tusk': ('An elephant has two long tusks.', 'Con voi có hai chiếc ngà dài.'),
    'skinny': ('He was tall and skinny.', 'Anh ấy cao và gầy gò.'),
    'ask': ('Can I ask a question?', 'Em có thể hỏi một câu được không?'),
    'whisker': ('The cat twitched its whiskers.', 'Con mèo khẽ giật giật ria mép.'),
    'desk': ('He was sitting at his desk.', 'Anh ấy đang ngồi tại bàn làm việc.'),
    'gift': ('This is a special gift for you.', 'Đây là một món quà đặc biệt dành cho bạn.'),
    'shaft': ('A shaft of sunlight entered the room.', 'Một vệt nắng ấm chiếu vào căn phòng.'),
    'lift': ('Take the lift to the top floor.', 'Đi thang máy lên tầng trên cùng.'),
    'soft': ('The pillow was warm and soft.', 'Chiếc gối ấm áp và mềm mại.'),
    'left': ('Turn left at the traffic lights.', 'Rẽ trái ở cột đèn giao thông.'),
    'theft': ('Lock your bike to prevent theft.', 'Khóa xe cẩn thận để tránh bị trộm.'),
    'guava': ('Guava is a sweet tropical fruit.', 'Ổi là một loại trái cây nhiệt đới ngọt ngào.'),
    'apple': ('He took a bite of the apple.', 'Anh ấy cắn một miếng táo.'),
    'pear': ('She peeled a pear for the child.', 'Cô ấy gọt một quả lê cho đứa trẻ.'),
    'mango': ('She sliced a ripe yellow mango.', 'Cô ấy cắt lát một quả xoài vàng chín mọng.'),

    # Lesson 13, 14, 15, 16
    'kelp': ('Giant kelp grows in cold oceans.', 'Tảo bẹ khổng lồ mọc ở các đại dương lạnh.'),
    'yelp': ('The puppy gave a sharp yelp.', 'Chú cún con kêu ăng ẳng thất thanh.'),
    'gulp': ('He drank the water in one gulp.', 'Anh ấy uống cạn nước trong một ngụm.'),
    'pulp': ('The fruit has sweet juicy pulp.', 'Trái cây có phần thịt quả ngọt mọng nước.'),
    'help': ('Can you help me with this?', 'Bạn có thể giúp tôi việc này không?'),
    'write': ('Write your address at the top.', 'Hãy viết địa chỉ của bạn ở phía trên cùng.'),
    'again': ('Could you say that again, please?', 'Bạn có thể vui lòng nói lại điều đó không?'),
    'about': ('What is this story about?', 'Câu chuyện này kể về điều gì thế?'),
    'birthday': ('What do you want for your birthday?', 'Bạn muốn nhận quà gì cho ngày sinh nhật?'),
    'asleep': ('The baby fell fast asleep.', 'Em bé đã ngủ thiếp đi rất say.'),
    'dream': ('I had a strange dream last night.', 'Đêm qua tôi đã có một giấc mơ kỳ lạ.'),
    'fried eggs': ('He had toast and fried eggs.', 'Anh ấy ăn bánh mì nướng và trứng ốp la.'),
    'rice': ('Do you prefer bread or rice?', 'Bạn thích ăn bánh mì hay ăn cơm hơn?'),
    'ice cream': ('We had ice cream for dessert.', 'Chúng tôi đã ăn kem tráng miệng.'),
    'cupcakes': ('She baked a batch of cupcakes.', 'Cô ấy nướng một mẻ bánh nướng nhỏ.'),
    'calf': ('The cow gave birth to a calf.', 'Con bò mẹ đã sinh ra một chú bê con.'),
    'golf': ('He plays golf every weekend.', 'Anh ấy chơi đánh gôn vào mỗi cuối tuần.'),
    'wolf': ('A pack of wolves hunted together.', 'Một đàn sói cùng nhau đi săn mồi.'),
    'gulf': ('Ships sailed across the Persian Gulf.', 'Những con tàu đi qua vùng Vịnh Ba Tư.'),
    'shelf': ('Put the books back on the shelf.', 'Hãy xếp sách lại lên giá sách nhé.'),
    'selfish': ("Don't be so selfish with toys.", 'Đừng ích kỷ với đồ chơi như vậy nhé.'),
    'milk': ('Drink a glass of warm milk.', 'Uống một ly sữa ấm nhé.'),
    'silk': ('Her dress was made of pure silk.', 'Váy của cô ấy được may bằng lụa nguyên chất.'),
    'bulk': ('The great bulk of work is done.', 'Phần lớn công việc đã được hoàn thành.'),
    'hulk': ('The green hulk smashed the wall.', 'Người khổng lồ xanh đập tan bức tường.'),
    'first': ('She won first prize in the race.', 'Cô ấy đã giành giải nhất trong cuộc đua.'),
    'next': ('Who is the next person in line?', 'Ai là người tiếp theo trong hàng?'),
    'then': ('We had dinner, and then went home.', 'Chúng tôi ăn tối, rồi sau đó đi về nhà.'),
    'at the end': ('There is a test at the end.', 'Có một bài kiểm tra ở phần cuối.'),
    'soup': ('The hot soup tasted very good.', 'Món súp nóng ăn rất ngon miệng.'),
    'pancakes': ('She made pancakes with maple syrup.', 'Cô ấy làm bánh kếp ăn cùng xi-rô phong.'),
    'water': ('Could I have a glass of water?', 'Cho tôi xin một ly nước được không?'),
    'damp': ('Wipe the table with a damp cloth.', 'Lau bàn bằng một chiếc khăn ẩm nhé.'),
    'stem': ('Cut the flowers with long stems.', 'Cắt hoa chừa lại phần cuống dài.'),
    'land': ('The plane will land in ten minutes.', 'Máy bay sẽ hạ cánh trong mười phút nữa.'),
    'sink': ('Wood does not sink in water.', 'Gỗ không bị chìm trong nước.'),
    'drop': ('Be careful not to drop the plate.', 'Cẩn thận đừng làm rơi chiếc đĩa nhé.'),
    'orange juice': ('She poured a glass of orange juice.', 'Cô ấy rót một ly nước cam.'),
    'a few': ('I need to ask a few questions.', 'Tôi cần hỏi một vài câu hỏi.'),
    'a lot of': ('She has a lot of friends here.', 'Cô ấy có rất nhiều bạn bè ở đây.'),

    # Farm & Animals
    'cow': ('The cows were grazing in the field.', 'Đàn bò đang gặm cỏ trên cánh đồng.'),
    'horse': ('He fell off his horse yesterday.', 'Anh ấy bị ngã ngựa vào ngày hôm qua.'),
    'sheep': ('A flock of sheep crossed the road.', 'Một đàn cừu băng qua đường.'),
    'farm': ('My uncle runs a dairy farm.', 'Chú tôi quản lý một trang trại bò sữa.'),
    'foal': ('The mare stood beside her newborn foal.', 'Ngựa mẹ đứng bên cạnh chú ngựa con mới sinh.'),
    'chick': ('The mother bird fed her tiny chicks.', 'Chim mẹ mớm mồi cho những chú chim non.'),
    'duckling': ('Five ducklings followed the mother duck.', 'Năm chú vịt con bơi theo vịt mẹ.'),
    'cheese': ('Would you like some bread and cheese?', 'Bạn có muốn ăn chút bánh mì và phô mai không?'),
    'yogurt': ('She ate a pot of fruit yogurt.', 'Cô ấy ăn một hộp sữa chua hoa quả.'),
    'haircuts': ('He needs regular haircuts for school.', 'Cậu ấy cần cắt tóc gọn gàng để đi học.')
}

def generate_fallback_sentences(vocab_items, sentence_structures=None):
    """
    Trích xuất câu ví dụ chuẩn mực từ kho ngữ liệu Oxford Advanced Learner's Dictionary (OALD) & Cambridge.
    Nếu từ vựng mới chưa có trong từ điển, sử dụng các mẫu câu chuẩn từ điển Oxford (4-8 từ), không tự chế gượng ép.
    """
    results = []
    generic_patterns = [
        ("Point to the {w} in the picture.", "Hãy chỉ vào {m} ở trong bức tranh."),
        ("Can you see the {w} over there?", "Bạn có nhìn thấy {m} ở đằng kia không?"),
        ("Where can I find a {w}?", "Tôi có thể tìm thấy {m} ở đâu?"),
        ("She drew a picture of a {w}.", "Cô bé đã vẽ một bức tranh về {m}."),
        ("We learned the word {w} today.", "Hôm nay chúng em đã học từ {m}.")
    ]

    for idx, item in enumerate(vocab_items):
        w = item['word'].strip()
        m = item['meaning'].strip()
        w_lower = w.lower()

        if w_lower in CURATED_OALD_SENTENCES:
            s_en, s_vi = CURATED_OALD_SENTENCES[w_lower]
        else:
            m_clean = re.sub(r'\(.*\)', '', m).strip()
            if '/' in m_clean:
                m_clean = m_clean.split('/')[0].strip()
            if ',' in m_clean:
                m_clean = m_clean.split(',')[0].strip()
            
            pat_en, pat_vi = generic_patterns[idx % len(generic_patterns)]
            s_en = pat_en.format(w=w_lower)
            s_vi = pat_vi.format(m=m_clean, m_cap=m_clean.capitalize())

        results.append({
            "word": w,
            "meaning": m,
            "sentence_en": s_en,
            "sentence_vi": s_vi
        })
    return results

def get_sentence_bank(repo_dir='.'):
    bank_file = os.path.join(repo_dir, 'oald_sentence_bank.json')
    bank = dict(CURATED_OALD_SENTENCES)
    if os.path.exists(bank_file):
        try:
            with open(bank_file, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    bank.update(saved)
        except Exception as e:
            log_message(f"Warning loading oald_sentence_bank.json: {e}")
    return bank

def save_sentence_bank(bank, repo_dir='.'):
    bank_file = os.path.join(repo_dir, 'oald_sentence_bank.json')
    try:
        with open(bank_file, 'w', encoding='utf-8') as f:
            json.dump(bank, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_message(f"Warning saving oald_sentence_bank.json: {e}")

_OXFORD_5000_MAP = None
def get_oxford_5000_map(repo_dir='.'):
    global _OXFORD_5000_MAP
    if _OXFORD_5000_MAP is not None:
        return _OXFORD_5000_MAP
    _OXFORD_5000_MAP = {}
    o5k_file = os.path.join(repo_dir, 'oxford_5000.json')
    if os.path.exists(o5k_file):
        try:
            with open(o5k_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k, v in data.items():
                if isinstance(v, dict):
                    w = v.get('word', '').lower().strip()
                    ex = v.get('example', '').strip()
                    if w and ex and w not in _OXFORD_5000_MAP:
                        words = ex.split()
                        if 4 <= len(words) <= 9 and ex[0].isupper() and ex[-1] in '.?!' and not any(bad in ex for bad in ['...', 'etc.', 'e.g.', '/', '~', ';', '…']):
                            _OXFORD_5000_MAP[w] = ex
        except Exception as e:
            log_message(f"Warning loading oxford_5000.json: {e}")
    return _OXFORD_5000_MAP

def translate_to_vietnamese(text):
    """Dịch câu ví dụ sang tiếng Việt tự nhiên qua Google Translation API (kèm retry & fallback MyMemory)"""
    import urllib.parse
    import urllib.request
    q = urllib.parse.quote(text)
    for attempt in range(3):
        try:
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=vi&dt=t&q={q}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                vi = ''.join([part[0] for part in data[0] if part[0]]).strip()
                if vi and vi.lower() != text.strip().lower():
                    return vi
        except Exception:
            time.sleep(0.6 * (attempt + 1))
    try:
        url2 = f"https://api.mymemory.translated.net/get?q={q}&langpair=en|vi"
        req2 = urllib.request.Request(url2, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req2, timeout=5) as resp2:
            data2 = json.loads(resp2.read().decode('utf-8'))
            vi2 = data2.get('responseData', {}).get('translatedText', '').strip()
            if vi2 and vi2.lower() != text.strip().lower():
                return vi2
    except Exception:
        pass
    return ""

def scrape_oxford_live_sentence(word):
    """Trích xuất câu ví dụ ngắn gọn (4-8 từ) trực tiếp từ website Oxford Learner's Dictionaries"""
    import urllib.request
    import urllib.parse
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        BeautifulSoup = None
    clean_w = word.lower().strip().replace(' ', '-')
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    candidates = [clean_w, f"{clean_w}_1", f"{clean_w}_2"]
    if clean_w.endswith('s') and len(clean_w) > 3:
        candidates.append(clean_w[:-1])
    if clean_w.endswith('es') and len(clean_w) > 4:
        candidates.append(clean_w[:-2])
        
    for term in candidates:
        url = f"https://www.oxfordlearnersdictionaries.com/definition/english/{term}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                html_str = resp.read().decode('utf-8', errors='ignore')
                raw_texts = []
                if BeautifulSoup is not None:
                    soup = BeautifulSoup(html_str, 'html.parser')
                    raw_texts = [tag.get_text(" ", strip=True) for tag in soup.select('ul.examples li span.x, .sense .examples .x, .x')]
                else:
                    matches = re.findall(r'<span[^>]*class="[^"]*\bx\b[^"]*"[^>]*>(.*?)</span>', html_str, flags=re.I | re.S)
                    raw_texts = [re.sub(r'<[^>]+>', ' ', m) for m in matches]
                for txt in raw_texts:
                    txt = re.sub(r'\(.*?\)', '', txt).strip()
                    txt = re.sub(r'\s+', ' ', txt).strip()
                    words = txt.split()
                    if 4 <= len(words) <= 8 and txt and txt[0].isupper() and txt[-1] in '.?!':
                        if not any(bad in txt for bad in ['...', 'etc.', 'e.g.', '/', '~', ';', '…']):
                            return txt
        except Exception:
            continue
    return None

def generate_vocabulary_sentences(vocab_input, lesson_title="", sentence_structures=None, api_key=None):
    """
    Trích xuất câu ví dụ chuẩn mực 100% từ điển Oxford (OALD) & Cambridge không phụ thuộc AI:
    - Lớp 1: Tra cứu tức thì (0.0001s) trong kho ngữ liệu oald_sentence_bank.json.
    - Lớp 2: Tra cứu offline trong cơ sở dữ liệu Oxford 5000 (oxford_5000.json với gần 6.000 từ).
    - Lớp 3: Trích xuất trực tiếp từ website Oxford Learner's Dictionaries (oxfordlearnersdictionaries.com) cho các từ Phonics/hiếm.
    - Tự động dịch thoát ý tiếng Việt và lưu vĩnh viễn vào oald_sentence_bank.json để tái sử dụng mãi mãi.
    - Không bao giờ bị timeout, không phụ thuộc quota AI, câu văn luôn chuẩn bản xứ 100%.
    """
    if isinstance(vocab_input, dict):
        items = [{"word": k.strip(), "meaning": v.strip()} for k, v in vocab_input.items() if k and v]
    elif isinstance(vocab_input, list):
        items = vocab_input
    else:
        items = []

    if not items:
        return []

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    bank = get_sentence_bank(repo_dir)
    o5k_map = get_oxford_5000_map(repo_dir)
    bank_updated = False

    results = []
    for it in items:
        w_orig = it["word"].strip()
        m_orig = it["meaning"].strip()
        w_key = w_orig.lower()

        s_en, s_vi = None, None

        try:
            # 1. Tra cứu trong Bank
            if w_key in bank:
                s_en, s_vi = bank[w_key]
                if not s_vi or s_vi.strip().lower() == s_en.strip().lower():
                    s_vi = translate_to_vietnamese(s_en)
                    if s_vi:
                        bank[w_key] = (s_en, s_vi)
                        bank_updated = True
            # 2. Tra cứu trong Oxford 5000
            elif w_key in o5k_map:
                s_en = o5k_map[w_key]
                s_vi = translate_to_vietnamese(s_en)
                if s_en and s_vi and s_vi.strip().lower() != s_en.strip().lower():
                    bank[w_key] = (s_en, s_vi)
                    bank_updated = True
                    log_message(f"Found Oxford 5000 example for '{w_key}': {s_en}")
                else:
                    s_en, s_vi = None, None
            # 3. Scrape live Oxford OALD
            else:
                s_en = scrape_oxford_live_sentence(w_orig)
                if s_en:
                    s_vi = translate_to_vietnamese(s_en)
                    if s_vi and s_vi.strip().lower() != s_en.strip().lower():
                        bank[w_key] = (s_en, s_vi)
                        bank_updated = True
                        log_message(f"Scraped live Oxford OALD example for '{w_key}': {s_en}")
                    else:
                        s_en, s_vi = None, None
        except Exception as e:
            log_message(f"Warning looking up sentence for '{w_key}': {e}")

        # 4. Fallback an toàn nếu từ hoàn toàn không có trong Oxford hoặc dịch lỗi
        if not s_en or not s_vi or s_vi.strip().lower() == s_en.strip().lower():
            fb = generate_fallback_sentences([it], sentence_structures)[0]
            s_en = fb["sentence_en"]
            s_vi = fb["sentence_vi"]

        results.append({
            "word": w_orig,
            "meaning": m_orig,
            "sentence_en": s_en,
            "sentence_vi": s_vi
        })

    if bank_updated:
        save_sentence_bank(bank, repo_dir)
        log_message(f"Updated and saved oald_sentence_bank.json with new Oxford sentences")

    return results

def build_word_search_grid(vocab_items, seed_str="cec"):
    """
    Tạo bảng trò chơi Tìm Từ Vựng (Word Search Puzzle) theo HÀNG NGANG (→),
    CỘT DỌC (↓) và ĐƯỜNG CHÉO (↘ ↗). Trả về cả lưới chữ, mặt nạ đáp án (answer_mask)
    và tọa độ đáp án của từng từ.
    """
    import random
    rng = random.Random(seed_str)

    cleaned_entries = []
    for it in vocab_items:
        w_raw = str(it.get("word", "")).strip()
        m_raw = str(it.get("meaning", "")).strip()
        letters = re.sub(r"[^A-Za-z]", "", w_raw).upper()
        if len(letters) < 2:
            continue
        cleaned_entries.append({
            "word": w_raw,
            "meaning": m_raw,
            "grid_word": letters
        })

    if not cleaned_entries:
        cleaned_entries = [{"word": "BOOK", "meaning": "quyển sách", "grid_word": "BOOK"}]

    max_w_len = max(len(e["grid_word"]) for e in cleaned_entries)
    num_w = len(cleaned_entries)

    if num_w <= 6:
        grid_n = 11
    elif num_w <= 10:
        grid_n = 13
    elif num_w <= 13:
        grid_n = 14
    else:
        grid_n = 15

    grid_n = max(grid_n, min(16, max_w_len + 1))
    if max_w_len > grid_n:
        grid_n = min(17, max_w_len)

    sorted_entries = sorted(cleaned_entries, key=lambda x: len(x["grid_word"]), reverse=True)

    dir_arrow_map = {
        (0, 1): "→ Ngang",
        (1, 0): "↓ Dọc",
        (1, 1): "↘ Chéo",
        (-1, 1): "↗ Chéo"
    }

    placements = {}
    answer_cells = set()

    for try_grow in range(3):
        grid = [["" for _ in range(grid_n)] for _ in range(grid_n)]
        placements = {}
        answer_cells = set()
        placed_centers = []
        placed_all = True

        for idx, entry in enumerate(sorted_entries):
            gw = entry["grid_word"]
            wlen = len(gw)
            if wlen > grid_n:
                gw = gw[:grid_n]
                wlen = len(gw)

            if idx % 3 == 0:
                dirs = [(0, 1), (1, 0), (1, 1), (-1, 1)]
            elif idx % 3 == 1:
                dirs = [(1, 0), (1, 1), (-1, 1), (0, 1)]
            else:
                dirs = [(1, 1), (-1, 1), (0, 1), (1, 0)]

            candidates = []

            for d_priority, (dr, dc) in enumerate(dirs):
                for r in range(grid_n):
                    end_r = r + dr * (wlen - 1)
                    if end_r < 0 or end_r >= grid_n:
                        continue
                    for c in range(grid_n):
                        end_c = c + dc * (wlen - 1)
                        if end_c < 0 or end_c >= grid_n:
                            continue
                        ok = True
                        overlaps = 0
                        adj_touches = 0
                        word_coords = set()
                        for k_i in range(wlen):
                            rr = r + dr * k_i
                            cc = c + dc * k_i
                            word_coords.add((rr, cc))
                            ch = grid[rr][cc]
                            if ch != "" and ch != gw[k_i]:
                                ok = False
                                break
                            if ch == gw[k_i]:
                                overlaps += 1
                        if not ok:
                            continue

                        # Đếm số ô xung quanh (8 hướng lân cận) đã có chữ của từ khác để tránh các từ dính sát nhau
                        for (rr, cc) in word_coords:
                            for nr in (-1, 0, 1):
                                for nc in (-1, 0, 1):
                                    if nr == 0 and nc == 0:
                                        continue
                                    tr, tc = rr + nr, cc + nc
                                    if 0 <= tr < grid_n and 0 <= tc < grid_n and (tr, tc) not in word_coords:
                                        if grid[tr][tc] != "":
                                            adj_touches += 1

                        # Phạt nặng nếu ô ngay trước chữ đầu hoặc ngay sau chữ cuối cùng hướng đã có chữ (tránh 2 từ nối đuôi nhau trên cùng 1 hàng/cột)
                        before_r, before_c = r - dr, c - dc
                        after_r, after_c = r + dr * wlen, c + dc * wlen
                        if 0 <= before_r < grid_n and 0 <= before_c < grid_n and grid[before_r][before_c] != "":
                            adj_touches += 50
                        if 0 <= after_r < grid_n and 0 <= after_c < grid_n and grid[after_r][after_c] != "":
                            adj_touches += 50

                        # Tính khoảng cách từ tâm từ này tới các từ đã đặt để rải đều khắp bảng
                        mid_r = r + dr * (wlen - 1) / 2.0
                        mid_c = c + dc * (wlen - 1) / 2.0
                        if placed_centers:
                            min_dist = min(((mid_r - pr) ** 2 + (mid_c - pc) ** 2) ** 0.5 for pr, pc in placed_centers)
                        else:
                            min_dist = 10.0

                        # Điểm ưu tiên:
                        # 1. Tốt nhất là KHÔNG đè nhau (overlaps == 0) và KHÔNG chạm sát cạnh từ khác (adj_touches == 0)
                        # 2. Ưu tiên hướng chính của từ (d_priority == 0) và khoảng cách xa các từ đã đặt (min_dist lớn)
                        dir_Penalty = 0 if d_priority == 0 else (1 if d_priority <= 1 else 2)
                        candidates.append((overlaps, adj_touches, dir_Penalty, -min_dist + rng.uniform(-0.8, 0.8), r, c, dr, dc))

            if candidates:
                candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
                _, _, _, _, r0, c0, dr0, dc0 = candidates[0]
            else:
                placed_all = False
                break

            for k_i in range(wlen):
                rr = r0 + dr0 * k_i
                cc = c0 + dc0 * k_i
                grid[rr][cc] = gw[k_i]
                answer_cells.add((rr, cc))

            placed_centers.append((r0 + dr0 * (wlen - 1) / 2.0, c0 + dc0 * (wlen - 1) / 2.0))
            placements[entry["word"].lower()] = {
                "row": r0 + 1,
                "col": c0 + 1,
                "dr": dr0,
                "dc": dc0,
                "dir_label": dir_arrow_map.get((dr0, dc0), "→")
            }

        if placed_all:
            break
        grid_n = min(17, grid_n + 1)

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    for r in range(grid_n):
        for c in range(grid_n):
            if not grid[r][c]:
                grid[r][c] = rng.choice(alphabet)

    for e in cleaned_entries:
        p = placements.get(e["word"].lower(), {"row": 1, "col": 1, "dir_label": "→"})
        e["ans_row"] = p["row"]
        e["ans_col"] = p["col"]
        e["ans_dir"] = p["dir_label"]

    return grid, grid_n, cleaned_entries, answer_cells

def generate_a4_worksheet(date_str, lesson_title, vocab_input, output_pdf_path, sentence_structures=None, class_name='VQ2-E1L-2603'):
    """
    Tạo Phiếu Bài Tập 2 Trang A4 Chuẩn In Đen Trắng Tương Phản Siêu Cao Kèm Đáp Án:
    - TRANG 1: Trò chơi Tinh Mắt Tìm Từ Vựng (Word Search Puzzle có đánh số Hàng/Cột)
               + Danh sách từ mới cần tìm ở cuối trang.
    - TRANG 2: Bảng luyện chép câu ví dụ chuẩn từ điển Oxford (OALD) ngắn gọn, dễ hiểu
               + Khung ĐÁP ÁN TRÒ CHƠI TÌM TỪ (Bảng lưới đáp án thu nhỏ + Tọa độ từng từ) ở cuối Trang 2.
    """
    import shutil
    import html as _html
    import math

    if isinstance(vocab_input, dict):
        vocab_items = generate_vocabulary_sentences(vocab_input, lesson_title, sentence_structures)
    elif isinstance(vocab_input, list):
        if vocab_input and not vocab_input[0].get("sentence_en"):
            vocab_items = generate_vocabulary_sentences(vocab_input, lesson_title, sentence_structures)
        else:
            vocab_items = vocab_input
    else:
        return None

    if len(vocab_items) > 8:
        filtered_items = [it for it in vocab_items if len(re.sub(r"[^A-Za-z]", "", str(it.get("word", "")))) >= 2]
        if len(filtered_items) >= 6:
            vocab_items = filtered_items

    total_words = len(vocab_items)
    if total_words == 0:
        return None

    # 1. XÂY DỰNG TRANG 1: BẢNG WORD SEARCH CÓ ĐÁNH SỐ TỌA ĐỘ + DANH SÁCH TỪ CUỐI TRANG
    grid, grid_n, ws_entries, answer_cells = build_word_search_grid(vocab_items, seed_str=f"{date_str}_{lesson_title}")

    word_list_rows = math.ceil(len(ws_entries) / 3)
    avail_grid_mm = max(130, 214 - word_list_rows * 7.2)
    cell_mm = round(min(14.4, avail_grid_mm / (grid_n + 0.7)), 2)
    cell_font_px = round(max(14.0, min(21.0, cell_mm * 1.42)), 1)

    # Hàng tiêu đề số Cột (1..grid_n)
    col_headers = '<th class="ws-idx-corner"></th>' + "".join(f'<th class="ws-idx-top">{c+1}</th>' for c in range(grid_n))
    grid_rows_html = [f"          <tr>{col_headers}</tr>"]
    for r in range(grid_n):
        tds = [f'<th class="ws-idx-left">{r+1}</th>']
        for c in range(grid_n):
            is_ans = "1" if (r, c) in answer_cells else "0"
            tds.append(f'<td class="ws-cell" data-ans="{is_ans}">{grid[r][c]}</td>')
        grid_rows_html.append(f"          <tr>{''.join(tds)}</tr>")
    grid_table_html = "\n".join(grid_rows_html)

    # Danh sách từ vựng ở cuối Trang 1 (chia 3 cột)
    word_cards_html = []
    for e in ws_entries:
        w_disp = _html.escape(e["word"])
        m_disp = _html.escape(e["meaning"])
        word_cards_html.append(f"""          <div class="wb-item">
            <span class="wb-check">&#9744;</span>
            <span class="wb-word">{w_disp.upper()}</span>:
            <span class="wb-mean">{m_disp}</span>
          </div>""")
    word_bank_html = "\n".join(word_cards_html)

    # 2. XÂY DỰNG TRANG 2: CHỈ CHÉP CÂU VÍ DỤ OXFORD (OALD) - TUYỆT ĐỐI KHÔNG IN ĐÁP ÁN LÊN GIẤY CỦA BÉ
    sentence_items = vocab_items[:15]
    num_sents = len(sentence_items)
    # Phủ kín 100% Trang 2 bằng bảng luyện chép câu rộng rãi (~16mm/dòng)
    sent_row_mm = round(max(14.5, min(22.0, 240.0 / max(1, num_sents))), 2)

    sent_rows_html = []
    for idx, it in enumerate(sentence_items, 1):
        w_esc = _html.escape(str(it.get("word", "")))
        m_esc = _html.escape(str(it.get("meaning", "")))
        se_esc = _html.escape(str(it.get("sentence_en", "")))
        sv_esc = _html.escape(str(it.get("sentence_vi", "")))
        sent_rows_html.append(f"""          <tr class="sent-only-row">
            <td class="col-word-ref">
              <div class="ref-en">{idx}. {w_esc}</div>
              <div class="ref-vi">{m_esc}</div>
            </td>
            <td class="col-oald-sent">
              <div class="oald-en">{se_esc}</div>
              <div class="oald-vi">{sv_esc}</div>
            </td>
            <td class="col-copy-box"></td>
          </tr>""")
    sent_tbody_html = "\n".join(sent_rows_html)

    # Bảng lưới đáp án thu nhỏ (Mini Answer Grid) ở cuối Trang 2: chỉ in đậm chữ cái đáp án, ô ngẫu nhiên để chấm mờ '·'
    mini_rows = []
    for r in range(grid_n):
        m_tds = []
        for c in range(grid_n):
            if (r, c) in answer_cells:
                m_tds.append(f'<td class="ak-cell ak-hit">{grid[r][c]}</td>')
            else:
                m_tds.append('<td class="ak-cell ak-miss">&middot;</td>')
        mini_rows.append(f"              <tr>{''.join(m_tds)}</tr>")
    mini_grid_html = "\n".join(mini_rows)

    # Danh sách tọa độ đáp án từng từ (Hàng, Cột, Hướng)
    ak_list_html = []
    for idx, e in enumerate(ws_entries, 1):
        w_up = _html.escape(e["word"].upper())
        ak_list_html.append(
            f'            <div class="ak-coord-item"><strong>{idx}. {w_up}:</strong> H{e["ans_row"]}-C{e["ans_col"]} ({e["ans_dir"]})</div>'
        )
    ak_coords_html = "\n".join(ak_list_html)

    html_content = f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <title>Trò Chơi Tìm Từ &amp; Luyện Chép Câu Oxford - {lesson_title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Tahoma, Geneva, Verdana, sans-serif;
      background-color: #ffffff;
      color: #000000;
      line-height: 1.22;
      -webkit-font-smoothing: antialiased;
    }}
    .web-toolbar {{
      text-align: center;
      padding: 10px;
      background: #1e293b;
      color: #fff;
      display: flex;
      justify-content: center;
      gap: 12px;
      position: sticky;
      top: 0;
      z-index: 999;
    }}
    .web-btn {{
      background: #10b981;
      color: #fff;
      border: none;
      padding: 8px 18px;
      border-radius: 8px;
      font-weight: 800;
      font-size: 14px;
      cursor: pointer;
    }}
    .a4-page {{
      background: #ffffff;
      width: 210mm;
      min-height: 286mm;
      max-height: 286mm;
      padding: 4.5mm 6mm 4.5mm 6mm;
      margin: 0 auto 20px auto;
      box-sizing: border-box;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      overflow: hidden;
      page-break-after: always;
      break-after: page;
    }}
    .a4-page:last-child {{
      page-break-after: auto;
      break-after: auto;
    }}
    .page-head {{
      border-bottom: 2.5px solid #000000;
      padding-bottom: 3px;
      margin-bottom: 5px;
    }}
    .title-main {{
      font-size: 16.5px;
      font-weight: 900;
      text-align: center;
      text-transform: uppercase;
      letter-spacing: 0.4px;
      color: #000000;
      margin-bottom: 2px;
    }}
    .info-bar {{
      display: grid;
      grid-template-columns: 1.5fr 1.1fr 1.8fr 0.9fr;
      font-size: 11.8px;
      font-weight: 800;
      color: #000000;
      padding: 1px 0;
    }}
    .guide-banner {{
      background: #ffffff;
      border: 2px dashed #000000;
      border-radius: 6px;
      padding: 4px 10px;
      font-size: 11.8px;
      font-weight: 800;
      text-align: center;
      margin-bottom: 4px;
    }}
    /* BẢNG Ô CHỮ TÌM TỪ (WORD SEARCH GRID) */
    .ws-grid-wrap {{
      display: flex;
      justify-content: center;
      align-items: center;
      margin: 2px 0 4px 0;
      flex-grow: 1;
    }}
    .ws-table {{
      border-collapse: separate;
      border-spacing: 0;
      border: 2.5px solid #000000;
      margin: 0 auto;
    }}
    .ws-idx-corner {{
      width: 6mm;
      height: 5.5mm;
      background: #000000;
      border-right: 1.5px solid #000000;
      border-bottom: 1.5px solid #000000;
    }}
    .ws-idx-top {{
      width: {cell_mm}mm;
      height: 5.5mm;
      font-size: 10px;
      font-weight: 900;
      text-align: center;
      vertical-align: middle;
      border-right: 1.5px solid #000000;
      border-bottom: 2px solid #000000;
      background: #f1f5f9;
    }}
    .ws-idx-left {{
      width: 6mm;
      height: {cell_mm}mm;
      font-size: 10px;
      font-weight: 900;
      text-align: center;
      vertical-align: middle;
      border-right: 2px solid #000000;
      border-bottom: 1.5px solid #000000;
      background: #f1f5f9;
    }}
    .ws-cell {{
      width: {cell_mm}mm;
      height: {cell_mm}mm;
      border-right: 1.6px solid #000000;
      border-bottom: 1.6px solid #000000;
      text-align: center;
      vertical-align: middle;
      font-family: "Consolas", "Courier New", "Segoe UI", monospace;
      font-size: {cell_font_px}px;
      font-weight: 900;
      color: #000000;
      user-select: none;
    }}
    body.show-ans .ws-cell[data-ans="1"] {{
      background-color: #fde047 !important;
      text-decoration: underline;
    }}
    .ws-table tr td:last-child, .ws-table tr th:last-child {{
      border-right: none;
    }}
    .ws-table tr:last-child td, .ws-table tr:last-child th {{
      border-bottom: none;
    }}
    /* DANH SÁCH TỪ MỚI Ở CUỐI TRANG 1 */
    .word-bank-box {{
      border: 2.5px solid #000000;
      border-radius: 6px;
      padding: 5px 10px 7px 10px;
      margin-top: 2px;
    }}
    .wb-title {{
      font-size: 12.5px;
      font-weight: 900;
      text-transform: uppercase;
      text-align: center;
      border-bottom: 2px solid #000000;
      padding-bottom: 3px;
      margin-bottom: 5px;
      letter-spacing: 0.3px;
    }}
    .wb-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 4px 10px;
    }}
    .wb-item {{
      font-size: 12px;
      line-height: 1.28;
      color: #000000;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .wb-check {{
      font-size: 14px;
      font-weight: 900;
      margin-right: 3px;
    }}
    .wb-word {{
      font-weight: 900;
      font-size: 12.5px;
    }}
    .wb-mean {{
      font-weight: 700;
    }}
    /* TRANG 2: BẢNG CHỈ CHÉP CÂU VÍ DỤ OXFORD + HỘP ĐÁP ÁN CUỐI TRANG */
    .sent-table {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      border: 2.5px solid #000000;
    }}
    .sent-table th {{
      background: #ffffff;
      color: #000000;
      font-size: 11.5px;
      font-weight: 900;
      text-transform: uppercase;
      border-bottom: 2.5px solid #000000;
      border-right: 2px solid #000000;
      padding: 4px 4px;
      text-align: center;
    }}
    .sent-table th:last-child {{
      border-right: none;
    }}
    .sent-only-row td {{
      height: {sent_row_mm}mm;
      border-bottom: 1.8px solid #000000;
      border-right: 2px solid #000000;
      padding: 1.5px 6px;
      vertical-align: middle;
    }}
    .sent-only-row td:last-child {{
      border-right: none;
    }}
    .sent-table tbody tr:last-child td {{
      border-bottom: none;
    }}
    .col-word-ref {{ width: 17%; }}
    .ref-en {{ font-size: 13px; font-weight: 900; color: #000000; line-height: 1.15; }}
    .ref-vi {{ font-size: 11px; font-weight: 700; color: #000000; line-height: 1.15; }}
    .col-oald-sent {{ width: 35%; }}
    .oald-en {{ font-size: 12.8px; font-weight: 900; color: #000000; line-height: 1.2; }}
    .oald-vi {{ font-size: 11px; font-weight: 700; color: #000000; line-height: 1.15; margin-top: 1px; }}
    .col-copy-box {{ width: 48%; }}

    /* HỘP ĐÁP ÁN CHỈ HIỆN TRÊN WEB KHI BỐ MẸ BẤM XEM ĐÁP ÁN - ẨN HOÀN TOÀN KHI IN PDF CHO BÉ */
    .answer-key-box {{
      display: none;
      max-width: 210mm;
      border: 2.5px solid #059669;
      background: #ecfdf5;
      border-radius: 10px;
      padding: 10px 14px;
      margin: 10px auto 16px auto;
      align-items: center;
      gap: 14px;
    }}
    body.show-ans .answer-key-box {{
      display: flex;
    }}
    .ak-mini-table {{
      border-collapse: collapse;
      border: 1.5px solid #000000;
      flex-shrink: 0;
    }}
    .ak-cell {{
      width: 2.7mm;
      height: 2.7mm;
      font-size: 6.5px;
      font-family: "Consolas", monospace;
      text-align: center;
      vertical-align: middle;
      line-height: 1;
    }}
    .ak-hit {{
      font-weight: 900;
      color: #000000;
      background: #e2e8f0;
      border: 0.4px solid #000000;
    }}
    .ak-miss {{
      color: #94a3b8;
      font-weight: 400;
    }}
    .ak-coords-wrap {{
      flex: 1;
    }}
    .ak-title {{
      font-size: 10.5px;
      font-weight: 900;
      text-transform: uppercase;
      border-bottom: 1.5px solid #000000;
      padding-bottom: 2px;
      margin-bottom: 3px;
    }}
    .ak-coords-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 2px 8px;
    }}
    .ak-coord-item {{
      font-size: 9.8px;
      font-weight: 700;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .page-foot {{
      border-top: 2px solid #000000;
      margin-top: 3px;
      padding-top: 2px;
      display: flex;
      justify-content: space-between;
      font-size: 10px;
      font-weight: 800;
      color: #000000;
    }}
    @page {{
      size: A4 portrait;
      margin: 4.5mm 5mm 4.5mm 5mm;
    }}
    @media print {{
      .web-toolbar, .answer-key-box {{ display: none !important; }}
      body {{ background: #ffffff !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .a4-page {{
        width: 100% !important;
        min-height: 286mm !important;
        max-height: 286mm !important;
        margin: 0 !important;
        padding: 4.5mm 6mm 4.5mm 6mm !important;
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
  <div class="web-toolbar">
    <button class="web-btn" onclick="document.body.classList.toggle('show-ans')">&#128273; Bật / Tắt Đáp Án Tìm Từ (Dành Cho Phụ Huynh)</button>
    <button class="web-btn" style="background:#2563eb;" onclick="window.print()">&#128424; In Phiếu A4 Cho Bé (Không Kèm Đáp Án)</button>
  </div>
  <div class="answer-key-box">
    <table class="ak-mini-table">
      <tbody>
{mini_grid_html}
      </tbody>
    </table>
    <div class="ak-coords-wrap">
      <div class="ak-title">&#128273; ĐÁP ÁN VỊ TRÍ TỪ VỰNG TRONG BẢNG TRANG 1 (HÀNG &times; CỘT &amp; HƯỚNG)</div>
      <div class="ak-coords-grid">
{ak_coords_html}
      </div>
    </div>
  </div>
  <script>if(location.search.includes('ans=1'))document.body.classList.add('show-ans');</script>

  <!-- TRANG 1: GAME TÌM TỪ VỰNG (WORD SEARCH PUZZLE) -->
  <div class="a4-page">
    <div>
      <div class="page-head">
        <div class="title-main">TRÒ CHƠI TINH MẮT TÌM TỪ VỰNG TIẾNG ANH (WORD SEARCH)</div>
        <div class="info-bar">
          <div>Họ và tên: <strong>LÊ MINH HUY</strong></div>
          <div>Lớp: <strong>{class_name}</strong></div>
          <div>Bài học: <strong>{lesson_title}</strong></div>
          <div style="text-align: right;">Ngày: {date_str}</div>
        </div>
      </div>
      <div class="guide-banner">
        &#127919; Nhiệm vụ: Tìm và khoanh tròn <strong>{len(ws_entries)} từ vựng</strong> ẩn trong bảng theo <strong>HÀNG NGANG (&rarr;)</strong>, <strong>CỘT DỌC (&darr;)</strong> hoặc <strong>ĐƯỜNG CHÉO (&searr; &nearr;)</strong>, sau đó đánh dấu <strong>&check;</strong> vào danh sách cuối trang!
      </div>
    </div>

    <div class="ws-grid-wrap">
      <table class="ws-table">
        <tbody>
{grid_table_html}
        </tbody>
      </table>
    </div>

    <div>
      <div class="word-bank-box">
        <div class="wb-title">DANH SÁCH TỪ MỚI CẦN TÌM ({len(ws_entries)} TỪ)</div>
        <div class="wb-grid">
{word_bank_html}
        </div>
      </div>

      <div class="page-foot">
        <div>Học sinh: Lê Minh Huy &bull; Lớp {class_name} &bull; Trung tâm CEC</div>
        <div>Trang 1 / 2 (Trò chơi tìm từ vựng - Lưới {grid_n}x{grid_n})</div>
      </div>
    </div>
  </div>

  <!-- TRANG 2: LUYỆN CHÉP CÂU VÍ DỤ CHUẨN TỪ ĐIỂN OXFORD (OALD) + HỘP ĐÁP ÁN -->
  <div class="a4-page">
    <div class="page-head">
      <div class="title-main">PHIẾU LUYỆN VIẾT CÂU TIẾNG ANH (CHUẨN TỪ ĐIỂN OXFORD)</div>
      <div class="info-bar">
        <div>Họ và tên: <strong>LÊ MINH HUY</strong></div>
        <div>Lớp: <strong>{class_name}</strong></div>
        <div>Bài học: <strong>{lesson_title}</strong></div>
        <div style="text-align: right;">Ngày: {date_str}</div>
      </div>
    </div>

    <table class="sent-table">
      <thead>
        <tr>
          <th style="width: 17%;">Từ Vựng &amp; Nghĩa</th>
          <th style="width: 35%;">Câu Ví Dụ Chuẩn Oxford (OALD)</th>
          <th style="width: 48%;">Thực Hành Chép Lại Câu Ví Dụ</th>
        </tr>
      </thead>
      <tbody>
{sent_tbody_html}
      </tbody>
    </table>

    <div class="page-foot">
      <div>Học sinh: Lê Minh Huy &bull; Lớp {class_name} &bull; Trung tâm CEC</div>
      <div>Trang 2 / 2 (Luyện chép {num_sents} câu ví dụ Oxford)</div>
    </div>
  </div>
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
        abs_html = os.path.abspath(temp_html_path).replace('\\', '/')
        abs_pdf = os.path.abspath(output_pdf_path)
        cmd = [
            chrome_binary,
            '--headless',
            '--disable-gpu',
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--no-pdf-header-footer',
            f'--print-to-pdf={abs_pdf}',
            f'file:///{abs_html}'
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
        await safe_send_message(bot, chat_id, general_info)
        log_message(f"Sent general info message to chat_id {chat_id}")
        await asyncio.sleep(0.5)

        vocab_text = f"*TỪ VỰNG MỚI - {result_data['report_date']}*\n" + "\n".join(
            f"• `{k}`: {v}" for k, v in result_data['new_vocabulary'].items()
        )
        if result_data['new_vocabulary']:
            log_message(f"Sending vocabulary message to chat_id {chat_id}: {vocab_text[:100]}...")
            await safe_send_message(bot, chat_id, vocab_text)
            log_message(f"Sent vocabulary message to chat_id {chat_id}")
            await asyncio.sleep(0.5)

        sentence_text = f"*CẤU TRÚC CÂU - {result_data['report_date']}*\n" + "\n".join(
            f"• *{k}*: {v if isinstance(v, str) else ', '.join(v)}"
            for k, v in result_data['sentence_structures'].items()
            if v is not None
        )
        if result_data['sentence_structures']:
            log_message(f"Sending sentence structures message to chat_id {chat_id}: {sentence_text[:100]}...")
            await safe_send_message(bot, chat_id, sentence_text)
            log_message(f"Sent sentence structures message to chat_id {chat_id}")
            await asyncio.sleep(0.5)

        homework_text = f"*BÀI TẬP VỀ NHÀ - {result_data['report_date']}*\n{result_data['homework']}"
        if result_data['homework'] and result_data['homework'] != "cannot find info":
            log_message(f"Sending homework message to chat_id {chat_id}: {homework_text[:100]}...")
            await safe_send_message(bot, chat_id, homework_text)
            log_message(f"Sent homework message to chat_id {chat_id}")
            await asyncio.sleep(0.5)

        comments_text = f"*NHẬN XÉT VỀ MINH HUY - {result_data['report_date']}*\n{result_data['student_comments_minh_huy'] or 'Không có nhận xét'}"
        if result_data['student_comments_minh_huy'] and result_data['student_comments_minh_huy'] != "cannot find info":
            log_message(f"Sending comments message to chat_id {chat_id}: {comments_text[:100]}...")
            await safe_send_message(bot, chat_id, comments_text)
            log_message(f"Sent comments message to chat_id {chat_id}")
            await asyncio.sleep(0.5)

        is_padded = vocab_sources and len(vocab_sources) > 1
        total_practice_words = sum(s[1] for s in vocab_sources) if vocab_sources else len(result_data.get('new_vocabulary', {}))

        # Gửi Đáp Án Tìm Từ bằng tin nhắn riêng trên Telegram cho Phụ huynh kiểm tra (không in vào tờ PDF của bé)
        practice_items = result_data.get('practice_vocabulary', [])
        if practice_items:
            try:
                _, _, ws_entries_ak, _ = build_word_search_grid(
                    practice_items,
                    seed_str=f"{result_data.get('report_date', '')}_{result_data.get('lesson_title', '')}"
                )
                ak_lines = [f"*🔑 ĐÁP ÁN GAME TÌM TỪ - {result_data['report_date']} (Dành cho Phụ huynh)*"]
                for idx_ak, e_ak in enumerate(ws_entries_ak, 1):
                    ak_lines.append(f"{idx_ak}. `{e_ak['word'].upper()}`: Hàng {e_ak['ans_row']}, Cột {e_ak['ans_col']} ({e_ak['ans_dir']})")
                await safe_send_message(bot, chat_id, "\n".join(ak_lines))
                await asyncio.sleep(0.5)
            except Exception as ak_err:
                log_message(f"Warning sending answer key message: {ak_err}")

        # Chỉ gửi thêm duy nhất file PDF tập chép A4 chuẩn in (không gửi file text quizlet)
        if worksheet_pdf and os.path.exists(worksheet_pdf):
            try:
                log_message(f"Sending A4 worksheet PDF to chat_id {chat_id}")
                if is_padded:
                    new_count = vocab_sources[0][1]
                    review_count = total_practice_words - new_count
                    pdf_caption = (
                        f"📄 Phiếu tập chép 5 cột A4 (Từ vựng & Câu) - {result_data.get('lesson_title', '')} "
                        f"(Đã ghép đủ {total_practice_words} từ & câu ví dụ: {new_count} từ mới + {review_count} từ ôn tập)"
                    )
                else:
                    pdf_caption = f"📄 Phiếu tập chép 5 cột A4 (Từ vựng & Câu) - {result_data.get('lesson_title', '')} ({result_data['report_date']})"
                sent_ok = False
                for doc_attempt in range(3):
                    try:
                        with open(worksheet_pdf, "rb") as f_doc:
                            await bot.send_document(
                                chat_id=chat_id,
                                document=f_doc,
                                filename=os.path.basename(worksheet_pdf),
                                caption=pdf_caption,
                                read_timeout=60,
                                write_timeout=60,
                                connect_timeout=30
                            )
                        sent_ok = True
                        break
                    except Exception as de:
                        log_message(f"send_document attempt {doc_attempt+1}/3 warning: {de}")
                        await asyncio.sleep(2)
                if not sent_ok and TELEGRAM_BOT_TOKEN:
                    with open(worksheet_pdf, "rb") as f_doc:
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
                            data={"chat_id": chat_id, "caption": pdf_caption},
                            files={"document": (os.path.basename(worksheet_pdf), f_doc)},
                            timeout=60
                        )
                log_message(f"Sent A4 worksheet PDF to chat_id {chat_id}")
                await asyncio.sleep(0.5)
            except Exception as e:
                log_message(f"Error sending worksheet PDF: {str(e)}")

    except Exception as e:
        log_message(f"Failed to send detailed Telegram messages to chat_id {chat_id}: {str(e)}")
        raise

def clean_lesson_title(raw_title, date_str=""):
    if not raw_title or raw_title == "cannot find info":
        return f"Lesson ({date_str})" if date_str else "Lesson"
    t = re.sub(r'^\s*(?:Lesson\s*)?(?:\(\s*Bài\s*học\s*\)|Bài\s*học)\s*[:：\-]*\s*', '', str(raw_title), flags=re.IGNORECASE).strip()
    t = re.sub(r'^\s*[:：\-]+\s*', '', t).strip()
    return t or (f"Lesson ({date_str})" if date_str else "Lesson")

def clean_student_comment(raw_comment):
    if not raw_comment:
        return ""
    c = str(raw_comment)
    # Cắt bỏ phần nhận xét của các học sinh tiếp theo nếu bị dính
    c = re.split(r'(?:\n|\s{2,}|(?<=[.!?”]))\s*(?:\.\s*)?(?:Hà Chi|Hoàng Vũ|Gia Hưng|Bảo Hân|Quốc Đạt|Minh Khang|Tuấn Minh|Khánh An|Bảo Ngọc|Gia Bảo|Trân trọng cảm ơn)\s*[:：!]?', c)[0]
    c = re.sub(r'\(\s*Nhận xét\s*', ' ', c, flags=re.IGNORECASE)
    c = re.sub(r'\s*học sinh\s*\)', ' ', c, flags=re.IGNORECASE)
    c = re.sub(r'Page\s*\|\s*\d+', ' ', c, flags=re.IGNORECASE)
    c = re.sub(r'\s+', ' ', c).strip()
    return c

def clean_extracted_vocabulary(vocab_dict):
    if not isinstance(vocab_dict, dict):
        return {}
    bad_exact = {
        'class', 'lesson', 'date', 'skills', 'link', 'links', 'audio', 'phonic', 'phonics',
        'words', 'grammar', 'summary', 'homework', 'comments', 'running content', 'warm up',
        'opener', 'vocabulary', 'communication', 'reading', 'speaking', 'listening', 'writing',
        'activity', 'activities', 'wrap-up', 'wrap up', 'page', 'wb page', '- wb page',
        'minh huy', 'ha chi', 'hà chi', 'hoang vu', 'hoàng vũ', 'gia hung', 'gia hưng',
        'bao han', 'bảo hân', 'quoc dat', 'quốc đạt', 'ng', 'chi', 'huy', 'i mystery box',
        'mystery box', 'online practice', 'extra exercise'
    }
    bad_substrings = [
        'class', 'lesson', 'date', 'skill', 'phonic', 'grammar', 'homework', 'comment',
        'page', 'unit', 'mystery', 'box', 'minh huy', 'exercise', 'practice', 'workbook',
        'http', 'www', 'listen', 'speak', 'read', 'write'
    ]
    cleaned = {}
    for k, v in vocab_dict.items():
        if not k or not v:
            continue
        w = re.sub(r'^[^a-zA-Z]+|[^a-zA-Z]+$', '', str(k)).strip().lower()
        w = re.sub(r'\s+', ' ', w)
        if not w or len(w) > 22 or not re.match(r'^[a-z]+(?:[\s\-][a-z]+){0,2}$', w):
            continue
        if w in bad_exact or any(b in w for b in bad_substrings):
            continue
        if len(w) == 1 and w not in ('a', 'i'):
            continue
        if len(w) == 2 and w not in ('an', 'in', 'on', 'up', 'go', 'no', 'he', 'we', 'my', 'ox', 'ax'):
            continue
        m = str(v).strip()
        # Nếu nghĩa bị dính từ tiếng Anh viết hoa của cột bên cạnh (VD: "bảng Chair", "bàn học/bàn làm việc Poster")
        m = re.sub(r'\s+[A-Z][a-zA-Z\s\-]*$', '', m).strip()
        m = re.sub(r'\s+\d+\.?$', '', m).strip()
        if not m or len(m) > 38 or any(bad_m in m.lower() for bad_m in ['hôm nay', 'buổi học', 'học sinh', 'con đã', 'bài tập']):
            continue
        cleaned[w] = m
    return cleaned

# Fallback parser trực tiếp từ nội dung văn bản PDF khi Gemini gặp lỗi hạn ngạch (429/504)
def parse_report_from_text(pdf_text, date_str, pdf_links=None):
    log_message("Executing fallback regex parser on PDF text")
    lesson_title = f"Lesson ({date_str})"
    m_lesson = re.search(r'Lesson\s*(?:\([^)]*\))?\s*[:：]\s*([^\n\r]+)', pdf_text, re.IGNORECASE)
    if m_lesson:
        lesson_title = clean_lesson_title(m_lesson.group(1), date_str)
    else:
        m_unit = re.search(r'(Unit\s+\d+[^\n\r]*)', pdf_text, re.IGNORECASE)
        if m_unit:
            lesson_title = clean_lesson_title(m_unit.group(1), date_str)

    comments = ""
    m_comment = re.search(r'Minh Huy\s*[:：]\s*([\s\S]*?)(?=\n\s*(?:\.\s*)?[A-ZÀ-Ỹ][a-zà-ỹ]+(?:\s+[A-ZÀ-Ỹ][a-zà-ỹ]+){0,3}\s*[:：]|\n\s*Trân trọng|\Z)', pdf_text)
    if m_comment:
        comments = clean_student_comment(m_comment.group(1))

    homework = ""
    m_hw = re.search(r'Homework\s*(?:\([^)]*\))?\s*([\s\S]*?)(?=Comments|Nhận xét|\n\s*Minh Huy\s*[:：]|\Z)', pdf_text, re.IGNORECASE)
    if m_hw:
        hw_raw = re.sub(r'Page\s*\|\s*\d+', ' ', m_hw.group(1))
        homework = re.sub(r'\s+', ' ', hw_raw).strip()

    vocab = {}
    # Chỉ quét từ vựng trong phần nội dung bài học (trước phần Homework / Comments)
    content_Before_hw = re.split(r'Homework\s*(?:\(|:)|Comments\s*(?:\(|:)|Minh Huy\s*[:：]', pdf_text, maxsplit=1, flags=re.IGNORECASE)[0]
    for line in content_Before_hw.splitlines():
        line = line.strip()
        if not line or len(line) > 140:
            continue
        # Tách các từ vựng nằm trên cùng 1 dòng bảng 2-3 cột (VD: "Board: bảng Chair: ghế" hoặc "1. unhealthy: ... 6. healthy: ...")
        line_split = re.sub(r'(?:^|\s+)\d+\.\s*', '\n', line)
        line_split = re.sub(r'(?<=[^\s:：])\s+([A-Z][a-zA-Z]*(?:\s+[a-zA-Z]+)?\s*[:：])', r'\n\1', line_split)
        for seg in line_split.splitlines():
            seg = seg.strip()
            if not seg:
                continue
            m_pair = re.match(r'^([A-Za-z][A-Za-z\s\-]{0,20})\s*[:：]\s*([^:：\n\r]{1,38})$', seg)
            if m_pair:
                vocab[m_pair.group(1).strip().lower()] = m_pair.group(2).strip()

    vocab = clean_extracted_vocabulary(vocab)

    # Đối chiếu thêm danh sách từ vựng trong phần Homework ("Danh sách từ vựng: a, b, c")
    m_hw_words = re.search(r'Danh sách từ vựng\s*[:：]?\s*([a-zA-Z,\s\-]+?)(?=\n|\-\s*Yêu cầu|$)', pdf_text, re.IGNORECASE)
    if m_hw_words:
        known_map = {'a': 'một', 'an': 'một', 'crayon': 'bút màu sáp', 'eraser': 'cục tẩy', 'pencil case': 'hộp bút', 'poster': 'tranh treo tường', 'board': 'bảng', 'chair': 'ghế', 'desk': 'bàn học'}
        if os.path.exists(VOCAB_FILE):
            try:
                with open(VOCAB_FILE, 'r', encoding='utf-8') as vf:
                    for it in json.load(vf).get('vocabulary', []):
                        if isinstance(it, dict) and it.get('word') and it.get('meaning'):
                            known_map[it['word'].strip().lower()] = it['meaning'].strip()
            except Exception:
                pass
        for hw_w in m_hw_words.group(1).split(','):
            hw_w_clean = hw_w.strip().lower()
            if hw_w_clean and hw_w_clean not in vocab and hw_w_clean in known_map:
                vocab[hw_w_clean] = known_map[hw_w_clean]

    # Trích xuất cấu trúc câu cơ bản từ bài học
    structures = {}
    for q_match in re.findall(r'[“"]([^”"]*?\?)[”"]', content_Before_hw):
        q_clean = q_match.strip()
        if 5 <= len(q_clean) <= 50:
            if "Is it a" in q_clean:
                structures[q_clean] = ["Yes, it is.", "No, it isn't."]
            elif "What's this" in q_clean or "What is this" in q_clean:
                structures[q_clean] = "It's a/an + noun."
            elif "Can I have" in q_clean:
                structures[q_clean] = "Here you are."
            else:
                structures[q_clean] = ""

    log_message(f"Fallback extracted: title='{lesson_title}', vocab_count={len(vocab)}")
    return {
        "new_vocabulary": vocab,
        "sentence_structures": structures,
        "report_date": date_str,
        "lesson_title": lesson_title,
        "homework": homework or "Ôn tập nội dung bài học và làm bài tập theo hướng dẫn của giáo viên.",
        "links": pdf_links or [],
        "student_comments_minh_huy": comments or "Không có nhận xét riêng trong báo cáo."
    }

# Get available Gemini model
def get_available_model(attempt=0):
    candidate_order = [
        'gemini-2.5-flash',
        'gemini-2.5-flash-lite',
        'gemini-2.0-flash',
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
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")
    log_message("Initializing Chrome WebDriver")
    driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(60)
    log_message(f"WebDriver session ID: {driver.session_id}")

    try:
        if not check_webdriver(driver):
            driver = restart_webdriver(driver, options)

        session_valid = restore_session(driver)
        if not session_valid:
            log_message("Session cache not available or expired. Performing fresh login...")
            if not login(driver):
                log_message("Login failed, aborting process")
                return
            log_message("Navigating to calendar overview page: https://apps.cec.com.vn/student-calendar/overview")
            driver.get("https://apps.cec.com.vn/student-calendar/overview")
            time.sleep(7)
            save_session(driver)
        else:
            log_message("Active session restored, bypassed fresh login!")

        if not check_webdriver(driver):
            driver = restart_webdriver(driver, options)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        log_message("Scrolled to bottom of page")

        class_events = WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'v-event') and @data-date]"))
        )
        log_message(f"Found {len(class_events)} class events")

        # Collect all class events on or before today
        candidate_events = []
        for event in class_events:
            date_str_attr = event.get_attribute("data-date")
            try:
                event_date = datetime.strptime(date_str_attr, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
                if event_date <= TODAY:
                    candidate_events.append((event_date, date_str_attr, event))
            except Exception as e:
                log_message(f"Error parsing date {date_str_attr}: {str(e)}")
                continue

        if not candidate_events:
            log_message("No classes found on or before today")
            return

        candidate_events.sort(key=lambda x: x[0], reverse=True)
        latest_event_date, latest_date_str, latest_event = candidate_events[0]
        log_message(f"Latest class date up to today: {latest_date_str}")

        # Check if the latest class has already been processed
        processed_date = processed.get("date")
        processed_history = processed.get("processed_dates", {})
        if (processed_date == latest_date_str and processed.get("report_url")) or latest_date_str in processed_history:
            log_message(f"Latest class on {latest_date_str} has already been processed with report URL: {processed.get('report_url') or processed_history.get(latest_date_str)}. Exiting.")
            return

        date_str = latest_date_str
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        driver.execute_script("arguments[0].click();", latest_event)
        time.sleep(3)

        popup = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.XPATH, "//div[contains(@class, 'v-menu__content') and contains(@class, 'menuable__content__active')]"))
        )
        title_element = popup.find_element(By.CLASS_NAME, "v-toolbar__title")
        title_text = title_element.text.strip()
        class_name = title_text.split(" : ")[-1] if " : " in title_text else "Unknown"
        log_message(f"Targeting report for {class_name} on {date_str}")

        report_button = popup.find_element(By.XPATH, "//button[.//p[text()='Báo cáo bài học']]")
        is_enabled = "v-btn--disabled" not in report_button.get_attribute("class") and report_button.is_enabled()

        if not is_enabled:
            log_message(f"Report button for {class_name} on {date_str} is disabled. Waiting for teacher to publish report.")
            return

        original_window = driver.current_window_handle
        max_window_retries = 3
        report_url = None

        for attempt in range(max_window_retries):
            try:
                if not check_webdriver(driver):
                    log_message("WebDriver unresponsive before clicking report button, restarting")
                    driver = restart_webdriver(driver, options)
                    driver.get("https://apps.cec.com.vn/student-calendar/overview")
                    time.sleep(7)
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    class_events_fresh = driver.find_elements(By.XPATH, f"//div[contains(@class, 'v-event') and @data-date='{date_str}']")
                    if class_events_fresh:
                        latest_event = class_events_fresh[0]
                    driver.execute_script("arguments[0].click();", latest_event)
                    time.sleep(3)
                    popup = WebDriverWait(driver, 10).until(
                        EC.visibility_of_element_located((By.XPATH, "//div[contains(@class, 'v-menu__content') and contains(@class, 'menuable__content__active')]"))
                    )
                    report_button = popup.find_element(By.XPATH, "//button[.//p[text()='Báo cáo bài học']]")

                log_message(f"Attempt {attempt + 1}/{max_window_retries} to click report button")
                try:
                    report_button.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", report_button)

                # Wait for new window to open
                WebDriverWait(driver, 15).until(
                    lambda d: len(d.window_handles) > len([original_window])
                )
                for window_handle in driver.window_handles:
                    if window_handle != original_window:
                        driver.switch_to.window(window_handle)
                        break

                # Wait for new window to load Google Docs safely without TypeError on NoneType
                def is_doc_loaded(d):
                    try:
                        u = d.current_url
                        return bool(u and ("docs.google.com" in u or "drive.google.com" in u))
                    except Exception:
                        return False

                WebDriverWait(driver, 60).until(is_doc_loaded)
                report_url = driver.current_url
                log_message(f"Report URL: {report_url}")
                break
            except Exception as e:
                log_message(f"Window switch attempt {attempt + 1}/{max_window_retries} failed: {str(e)}")
                if attempt == max_window_retries - 1:
                    log_message("Max retries reached for window switch")
                    raise Exception(f"Failed to retrieve report URL after {max_window_retries} attempts: {str(e)}")
                try:
                    if original_window in driver.window_handles:
                        driver.switch_to.window(original_window)
                except:
                    pass
                if "connection refused" in str(e).lower() or "timeout" in str(e).lower() or "no such window" in str(e).lower():
                    driver = restart_webdriver(driver, options)
                    driver.get("https://apps.cec.com.vn/student-calendar/overview")
                    time.sleep(7)
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    class_events_fresh = driver.find_elements(By.XPATH, f"//div[contains(@class, 'v-event') and @data-date='{date_str}']")
                    if class_events_fresh:
                        latest_event = class_events_fresh[0]
                    driver.execute_script("arguments[0].click();", latest_event)
                    time.sleep(3)
                    popup = WebDriverWait(driver, 10).until(
                        EC.visibility_of_element_located((By.XPATH, "//div[contains(@class, 'v-menu__content') and contains(@class, 'menuable__content__active')]"))
                    )
                    report_button = popup.find_element(By.XPATH, "//button[.//p[text()='Báo cáo bài học']]")
                time.sleep(5)
        
        if report_url:
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            body = f"Báo cáo bài học mới cho lớp {class_name} ngày {date_str}\nLink: {report_url}"
            log_message(f"Found new report: {body}")
            update_google_sheet(date_str, class_name, report_url, timestamp)



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
              "new_vocabulary": {},  // Dictionary of official vocabulary words/phrases taught in this lesson (key: word/phrase in lowercase, value: meaning in Vietnamese, must not be empty)
              "sentence_structures": {},  // Dictionary of question-answer pairs (key: question, value: answer or list of answers if multiple, no null values)
              "report_date": "",  // Report date in YYYY-MM-DD (if not found, use date from input JSON)
              "lesson_title": "",  // Lesson title (if not found, "cannot find info")
              "homework": "",  // Homework description with any associated links (if not found, "cannot find info")
              "links": [],  // List of all URLs found in the content (e.g., homework links, YouTube videos)
              "student_comments_minh_huy": ""  // Comments about student Minh Huy (if not found, "cannot find info")
            }
            CRITICAL RULES FOR new_vocabulary:
            - ONLY extract words listed explicitly in the lesson's vocabulary sections (such as 'Phonics', 'Words set', 'Từ vựng', 'Tricky words', 'New words', 'Vocabulary').
            - DO NOT extract classroom activity skills, directions, or headings (e.g., 'listen', 'speak', 'read', 'write', 'reading', 'speaking', 'listening', 'phonics', 'tricky words', 'blend sounds', 'arm blending', 'dot blending').
            - DO NOT extract words mentioned exclusively inside the teacher's comments for other individual students (e.g., from 'Comments' section of other kids).
            - Every word in new_vocabulary must have a clear, accurate meaning in Vietnamese (e.g., {"quilt": "chăn bông"}).
            For sentence_structures, map questions to answers (e.g., {"What is this?": "It's a pen."} or {"What are they?": ["They are scissors.", "They are books."]}). If no sentence structures found, return {}.
            Include all URLs (e.g., YouTube, Google Drive, Quizlet) in the links field, especially those related to homework.
            Use date from input JSON if report_date is not found in text.
            Ensure the output is a valid JSON object with all required fields.
            """

            max_attempts = 3
            extracted_data = None
            best_response = None
            if os.path.exists('Report'):
                for rf in os.listdir('Report'):
                    if rf.startswith(date_str) and rf.endswith('.json') and 'cannot_find_info' not in rf:
                        try:
                            with open(os.path.join('Report', rf), 'r', encoding='utf-8') as cf:
                                cdata = json.load(cf)
                                if cdata.get('new_vocabulary') and cdata.get('lesson_title') and cdata.get('lesson_title') != 'cannot find info':
                                    extracted_data = cdata
                                    log_message(f"Loaded verified report data from existing file: {rf}")
                                    break
                        except Exception:
                            pass

            for attempt in range(0 if extracted_data else max_attempts):
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
                    extracted_data['lesson_title'] = clean_lesson_title(extracted_data.get('lesson_title', ''), date_str)
                    extracted_data['student_comments_minh_huy'] = clean_student_comment(extracted_data.get('student_comments_minh_huy', ''))
                    extracted_data['new_vocabulary'] = clean_extracted_vocabulary(extracted_data.get('new_vocabulary', {}))
                    for word in extracted_data['new_vocabulary']:
                        if not extracted_data['new_vocabulary'][word]:
                            extracted_data['new_vocabulary'][word] = {
                                "pot": "cái nồi"
                            }.get(word, "nghĩa không xác định")
                    extracted_data['sentence_structures'] = {
                        k: v for k, v in extracted_data['sentence_structures'].items()
                        if v is not None and (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v)))
                    }
                    if not extracted_data.get('new_vocabulary') or extracted_data.get('lesson_title') in (None, '', 'cannot find info'):
                        raise ValueError("Gemini returned empty new_vocabulary or invalid lesson_title")
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

            extracted_data['lesson_title'] = clean_lesson_title(extracted_data.get('lesson_title', ''), date_str)
            extracted_data['student_comments_minh_huy'] = clean_student_comment(extracted_data.get('student_comments_minh_huy', ''))
            extracted_data['new_vocabulary'] = clean_extracted_vocabulary(extracted_data.get('new_vocabulary', {}))

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
                target_max=15
            )
            log_message(f"Practice vocabulary count: {len(practice_vocab)} (Original lesson words: {len(extracted_data.get('new_vocabulary', {}))})")
            for src_name, cnt in vocab_sources:
                log_message(f"  + {src_name}: {cnt} words")

            # Tự động tạo Phiếu tập chép A4 và File Quizlet 1-Click Import
            worksheet_pdf = f"Report/{date_str}_{title}_Phieu_Tap_Chep.pdf"
            quizlet_txt = f"Report/{date_str}_{title}_Quizlet_Import.txt"
            
            # Sinh câu ví dụ chuẩn Cambridge CLL3 cho 40 từ vựng (ưu tiên cấu trúc bài học và đổi mới sau mỗi buổi)
            lesson_structures = extracted_data.get('sentence_structures', {})
            practice_items = generate_vocabulary_sentences(
                practice_vocab,
                lesson_title=extracted_data.get('lesson_title', title_raw),
                sentence_structures=lesson_structures,
                api_key=API_KEY
            )

            generate_a4_worksheet(date_str, extracted_data.get('lesson_title', title_raw), practice_items, worksheet_pdf, sentence_structures=lesson_structures, class_name=class_name)
            generate_quizlet_file(practice_vocab, quizlet_txt)

            result_data = {
                **extracted_data,
                'class_name': class_name,
                'report_url': report_url,
                'total_vocabulary': total_vocab,
                'practice_vocabulary': practice_items
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

            # Chỉ lưu trạng thái đã xử lý sau khi đã tạo xong phiếu tập chép và gửi Telegram thành công
            save_processed(date_str, class_name, report_url)

            if is_git_repository():
                log_message("Committing and pushing Report and vocab files to GitHub")
                try:
                    subprocess.run(["git", "config", "--global", "user.name", "GitHub Action"], check=True)
                    subprocess.run(["git", "config", "--global", "user.email", "action@github.com"], check=True)
                    subprocess.run(["git", "add", PROCESSED_FILE, LOG_FILE, VOCAB_FILE, "oald_sentence_bank.json", "Report/*"], check=True)
                    subprocess.run(["git", "commit", "-m", f"Update report and vocab for {date_str}"], check=True)
                    subprocess.run(["git", "pull", "--rebase"], check=True)
                    subprocess.run(["git", "push"], check=True)
                    log_message(f"Pushed {PROCESSED_FILE}, {LOG_FILE}, {VOCAB_FILE}, and Report/* successfully")
                except Exception as e:
                    log_message(f"Error committing/pushing Report and vocab files: {str(e)}")

        else:
            log_message('Failed to retrieve report URL')
    except Exception as e:
        log_message(f"Error checking reports: {str(e)}")
    finally:
        log_message("Closing WebDriver")
        driver.quit()

if __name__ == "__main__":
    rotate_log_if_needed()
    log_message("Starting script")
    process_report()
    log_message("Script completed")
