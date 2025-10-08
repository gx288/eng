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

# Configuration
PROCESSED_FILE = "processed.json"
CREDENTIALS_FILE = "credentials.json"
SHEET_ID = "1-MMsbAGlg7MNbBPAzioqARu6QLfry5mCrWJ-Q_aqmIM"
SHEET_NAME = "Report"
SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CLASS_ID = "11005"
LOG_FILE = "class_info_log.txt"

def log_message(message):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")  # In log ra màn hình
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")

def send_notification(subject, body):
    log_message("Preparing to send Telegram notification")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log_message("Missing Telegram credentials, skipping notification")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"<b>{subject}</b>\n\n{body}",
        "parse_mode": "HTML"
    }
    try:
        log_message(f"Sending Telegram message: {body}")
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            log_message("Telegram notification sent successfully")
        else:
            log_message(f"Telegram send failed: {response.text}")
    except Exception as e:
        log_message(f"Error sending Telegram notification: {str(e)}")

def login(driver):
    log_message("Navigating to login page: https://apps.cec.com.vn/login")
    driver.get("https://apps.cec.com.vn/login")
    current_id = os.getenv("NEW_CEC_USER")
    password = os.getenv("NEW_CEC_PASS")
    if not current_id or not password:
        log_message("Missing NEW_CEC_USER or NEW_CEC_PASS")
        raise Exception("Missing credentials")
    try:
        log_message(f"Entering username: {current_id}")
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
        time.sleep(5)

        if "login" in driver.current_url:
            log_message("Login failed: Still on login page")
            raise Exception("Login failed")
        log_message("Login successful")
    except Exception as e:
        log_message(f"Login error: {str(e)}")
        raise

def update_google_sheet(class_id, report_count, timestamp):
    log_message(f"Updating Google Sheet with Class ID {class_id}, {report_count} reports")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
            client = gspread.authorize(creds)
            sheet = client.open_by_key(SHEET_ID)
            worksheet = sheet.worksheet(SHEET_NAME)
            row_data = [str(class_id), str(report_count), timestamp]
            worksheet.append_row(row_data)
            log_message(f"Updated Google Sheet successfully: Class ID {class_id}, {report_count} reports at {timestamp}")
            return True
        except Exception as e:
            log_message(f"Attempt {attempt+1}/{max_retries} failed to update Google Sheet: {str(e)}")
            if attempt == max_retries - 1:
                log_message(f"Error updating Google Sheet: {str(e)}")
                return False
            time.sleep(3)
    return False

def save_processed(report_count):
    log_message(f"Saving processed data to {PROCESSED_FILE}")
    processed = {"class_id": CLASS_ID, "report_count": report_count}
    try:
        with open(PROCESSED_FILE, 'w', encoding='utf-8') as f:
            json.dump(processed, f, indent=2)
        log_message(f"Saved {PROCESSED_FILE} successfully")
    except Exception as e:
        log_message(f"Error saving {PROCESSED_FILE}: {str(e)}")

def is_git_repository():
    try:
        subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], check=True, capture_output=True, text=True)
        log_message("Git repository detected")
        return True
    except Exception as e:
        log_message(f"Not a git repository: {str(e)}")
        return False

def check_reports():
    log_message(f"Starting report check for Class ID {CLASS_ID}")
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

    try:
        login(driver)
        log_message(f"Navigating to class detail page: https://apps.cec.com.vn/student-calendar/class-detail?classID={CLASS_ID}")
        driver.get(f"https://apps.cec.com.vn/student-calendar/class-detail?classID={CLASS_ID}")
        log_message("Waiting for page to load")
        time.sleep(5)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        log_message("Scrolled to bottom of page")

        log_message("Counting report elements")
        report_elements = WebDriverWait(driver, 20).until(
            EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr//i[@data-v-50ef298c and contains(@class, 'isax-card-edit')]"))
        )
        report_count = len(report_elements)
        log_message(f"Found {report_count} reports for Class ID {CLASS_ID}")

        last_report_count = processed.get("report_count", 0)
        if report_count > last_report_count:
            log_message(f"New reports detected (current: {report_count}, last: {last_report_count})")
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            body = f"Hùng ơi, có {report_count} report cho lớp học CEC!\nClass ID: {CLASS_ID}"
            send_notification("New CEC Report Count", body)
            update_google_sheet(CLASS_ID, report_count, timestamp)
            save_processed(report_count)

            if is_git_repository():
                log_message("Committing and pushing changes to GitHub")
                try:
                    subprocess.run(["git", "config", "--global", "user.name", "GitHub Action"], check=True)
                    subprocess.run(["git", "config", "--global", "user.email", "action@github.com"], check=True)
                    subprocess.run(["git", "add", PROCESSED_FILE, LOG_FILE], check=True)
                    subprocess.run(["git", "commit", "-m", f"Update {PROCESSED_FILE} and {LOG_FILE} for Class ID {CLASS_ID}"], check=True)
                    subprocess.run(["git", "push"], check=True)
                    log_message(f"Pushed {PROCESSED_FILE} and {LOG_FILE} successfully")
                except Exception as e:
                    log_message(f"Error committing/pushing: {str(e)}")
        else:
            log_message(f"No new reports (current: {report_count}, last: {last_report_count})")

    except Exception as e:
        log_message(f"Error checking reports: {str(e)}")
    finally:
        log_message("Closing WebDriver")
        driver.quit()

if __name__ == "__main__":
    log_message("Starting script")
    check_reports()
    log_message("Script completed")
