import json
import time
import os
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
async def send_detailed_telegram_message(bot, chat_id, result_data):
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
    except Exception as e:
        log_message(f"Failed to send detailed Telegram messages to chat_id {chat_id}: {str(e)}")
        raise

# Get available Gemini model
def get_available_model(attempt=0):
    try:
        models = genai.list_models()
        available_models = [model.name for model in models if 'generateContent' in model.supported_generation_methods]
        log_message(f"Available models: {available_models}")
        for model in available_models:
            if attempt == 0 and 'gemini-2.5-flash' in model:
                return model
            if attempt == 1 and 'gemini-2.5-pro' in model:
                return model
            if attempt == 2 and 'gemini-pro' in model:
                return model
        return available_models[0] if available_models else None
    except Exception as e:
        log_message(f"Failed to list models: {str(e)}")
        return None

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
                        driver = restart_webdriver(driver, options)
                    report_button.click()
                    WebDriverWait(driver, 10).until(
                        EC.number_of_windows_to_be(len(driver.window_handles))
                    )
                    for window_handle in driver.window_handles:
                        if window_handle != original_window:
                            driver.switch_to.window(window_handle)
                            break
                    WebDriverWait(driver, 30).until(
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
                    time.sleep(3)

            if report_url:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                body = f"Báo cáo bài học mới cho lớp {class_name} ngày {date_str}\nLink: {report_url}"
                send_basic_notification("Có Báo cáo bài học mới!", body)

                update_google_sheet(date_str, class_name, report_url, timestamp)
                save_processed(date_str, class_name, report_url)

                if is_git_repository():
                    log_message("Committing and pushing changes to GitHub")
                    try:
                        subprocess.run(["git", "config", "--global", "user.name", "GitHub Action"], check=True)
                        subprocess.run(["git", "config", "--global", "user.email", "action@github.com"], check=True)
                        subprocess.run(["git", "add", PROCESSED_FILE, LOG_FILE], check=True)
                        subprocess.run(["git", "commit", "-m", f"Update {PROCESSED_FILE} and {LOG_FILE} for {date_str}"], check=True)
                        subprocess.run(["git", "push"], check=True)
                        log_message(f"Pushed {PROCESSED_FILE} and {LOG_FILE} successfully")
                    except Exception as e:
                        log_message(f"Error committing/pushing: {str(e)}")

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
                        if attempt == max_attempts - 1:
                            log_message("All API attempts failed. Using best response or default.")
                            extracted_data = best_response or {
                                "new_vocabulary": {"pot": "cái nồi"},
                                "sentence_structures": {},
                                "report_date": date_str,
                                "lesson_title": "cannot find info",
                                "homework": "cannot find info",
                                "links": pdf_links,
                                "student_comments_minh_huy": "cannot find info"
                            }

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
                title = extracted_data['lesson_title'].replace(' ', '_') if extracted_data['lesson_title'] else 'unknown'
                result_filename = f"Report/{date_str}_{title}.json"

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
                    for chat_id in [TELEGRAM_CHAT_ID, TELEGRAM_CHAT_ID_2]:
                        if chat_id:
                            log_message(f"Sending detailed Telegram messages to chat_id {chat_id}")
                            await send_detailed_telegram_message(bot, chat_id, result_data)
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
