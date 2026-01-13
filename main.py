import pandas as pd
import json
import subprocess
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException
from selenium.webdriver.common.keys import Keys
import time
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from urllib.parse import urlparse
from webdriver_manager.chrome import ChromeDriverManager

# Configuration
CSV_FILE = "id.csv"
PROCESSED_FILE = "processed.json"
CREDENTIALS_FILE = "credentials.json"
SHEET_ID = "1-MMsbAGlg7MNbBPAzioqARu6QLfry5mCrWJ-Q_aqmIM"
SHEET_NAME = "Trang tính3"
SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def log_message(message):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")
    with open("class_info_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")

def check_doc_accessibility(url):
    try:
        if "docs.google.com/document" in url:
            doc_id = urlparse(url).path.split('/d/')[1].split('/')[0]
            export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"
            response = requests.head(export_url, allow_redirects=True, timeout=10)
            return response.status_code == 200, export_url if response.status_code == 200 else f"HTTP {response.status_code}"
        elif "drive.google.com/drive/folders" in url:
            response = requests.head(url, allow_redirects=True, timeout=10)
            return response.status_code == 200, url if response.status_code == 200 else f"HTTP {response.status_code}"
        return False, "Not a supported Google URL"
    except Exception as e:
        return False, str(e)

def login(driver):
    driver.get("https://apps.cec.com.vn/login")
    current_id = os.getenv("CEC_USERNAME", "40183HN")
    password = os.getenv("CEC_PASSWORD", "1234567")
    try:
        username_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "input-14"))
        )
        driver.execute_script("arguments[0].value = '';", username_field)
        username_field.send_keys(current_id)
        password_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "input-18"))
        )
        driver.execute_script("arguments[0].value = '';", password_field)
        password_field.send_keys(password)
        login_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
        )
        login_button.click()
        time.sleep(5)
        if "login" in driver.current_url:
            log_message("Lỗi đăng nhập: Vẫn ở trang login")
            raise Exception("Login failed")
        log_message("Đăng nhập thành công")
    except Exception as e:
        log_message(f"Lỗi đăng nhập: {str(e)}")
        raise

def get_google_sheet_data():
    max_retries = 3
    for attempt in range(max_retries):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
            client = gspread.authorize(creds)
            sheet = client.open_by_key(SHEET_ID)
            worksheet = sheet.worksheet(SHEET_NAME)
            return worksheet.get_all_values()
        except Exception as e:
            log_message(f"Attempt {attempt+1}/{max_retries} failed to read Google Sheet: {str(e)}")
            if attempt == max_retries - 1:
                return []
            time.sleep(3)
    return []

def update_google_sheet(row_data, class_id, lesson_number):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
            client = gspread.authorize(creds)
            sheet = client.open_by_key(SHEET_ID)
            worksheet = sheet.worksheet(SHEET_NAME)
            existing_data = worksheet.get_all_values()
            unique_id = f"{class_id}:{lesson_number}"
            for row in existing_data:
                if len(row) >= 4 and f"{row[0]}:{row[3]}" == unique_id:
                    return True
            worksheet.append_row(row_data)
            log_message(f"Updated Google Sheet for Class ID {class_id}, Lesson {lesson_number}")
            return True
        except Exception as e:
            if attempt == max_retries - 1:
                log_message(f"Error updating Google Sheet: {str(e)}")
                return False
            time.sleep(3)
    return False

def save_processed(processed):
    try:
        with open(PROCESSED_FILE, 'w', encoding='utf-8') as f:
            json.dump(processed, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_message(f"Error saving processed.json: {str(e)}")

def is_git_repository():
    try:
        subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], check=True, capture_output=True, text=True)
        return True
    except:
        return False

def sync_processed_with_sheet(processed, sheet_data):
    processed_lessons = set()
    for row in sheet_data:
        if len(row) < 4: continue
        class_id = row[0]
        course_name = row[2]
        try:
            lesson_number = int(row[3])
        except ValueError: continue
        processed_lessons.add(f"{class_id}:{lesson_number}")
        if course_name not in processed: processed[course_name] = {}
        if class_id not in processed[course_name]:
            processed[course_name][class_id] = {'last_lesson': -1, 'total_lessons': 0, 'has_errors': False}
        processed[course_name][class_id]['last_lesson'] = max(
            processed[course_name][class_id].get('last_lesson', -1),
            lesson_number - 1
        )
    save_processed(processed)
    return processed_lessons

def process_class_id(driver, class_id, course_name, processed, processed_lessons, csv_total_sessions):
    try:
        url = f"https://apps.cec.com.vn/student-calendar/class-detail?classID={class_id}"
        driver.get(url)
        log_message(f"Processing Class ID {class_id}")
        time.sleep(5)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        
        class_code = WebDriverWait(driver, 40).until(EC.presence_of_element_located((By.CSS_SELECTOR, "h2.d-flex div"))).text.strip()
        
        lesson_rows = WebDriverWait(driver, 20).until(EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr")))
        total_lessons = max(len(lesson_rows), csv_total_sessions)

        processed.setdefault(course_name, {})[class_id] = {
            'last_lesson': processed.get(course_name, {}).get(class_id, {}).get('last_lesson', -1),
            'total_lessons': total_lessons,
            'has_errors': False
        }

        has_errors = False
        for lesson_index in range(processed[course_name][class_id]['last_lesson'] + 1, len(lesson_rows)):
            lesson_number = lesson_index + 1
            unique_id = f"{class_id}:{lesson_number}"
            
            if unique_id in processed_lessons:
                processed[course_name][class_id]['last_lesson'] = lesson_index
                continue

            lesson_has_error = False
            try:
                row = lesson_rows[lesson_index]
                # Lấy Report Link
                report_link = "No report available"
                try:
                    report_button = row.find_element(By.XPATH, ".//i[contains(@class, 'isax-card-edit')]")
                    original_window = driver.current_window_handle
                    driver.execute_script("arguments[0].click();", report_button)
                    WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
                    new_window = [w for w in driver.window_handles if w != original_window][0]
                    driver.switch_to.window(new_window)
                    report_link = driver.current_url
                    is_accessible, _ = check_doc_accessibility(report_link)
                    if not is_accessible: lesson_has_error = True
                    driver.close()
                    driver.switch_to.window(original_window)
                except: report_link = "Error or No Link"

                # Lấy Homework
                homework_content = "No homework available"
                try:
                    homework_button = row.find_element(By.XPATH, ".//i[contains(@class, 'isax-book-square')]")
                    driver.execute_script("arguments[0].click();", homework_button)
                    popup = WebDriverWait(driver, 15).until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".v-dialog--active")))
                    header = popup.find_element(By.CSS_SELECTOR, ".v-toolbar__title").text.strip()
                    homework_content = f"Header: {header}"
                    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                    time.sleep(1)
                except: pass

                row_data = [str(class_id), class_code, course_name, str(lesson_number), report_link, homework_content, "OK" if not lesson_has_error else "Has Errors"]
                if update_google_sheet(row_data, class_id, lesson_number):
                    processed[course_name][class_id]['last_lesson'] = lesson_index
                    save_processed(processed)
            except Exception as e:
                log_message(f"Error at lesson {lesson_number}: {str(e)}")
                has_errors = True

        return has_errors
    except Exception as e:
        log_message(f"Global error Class {class_id}: {str(e)}")
        return True

def main():
    try:
        df = pd.read_csv(CSV_FILE)
        # Chỉ lấy các cột cần thiết, bỏ qua Rate
        df['Start date'] = pd.to_datetime(df['Start date'], dayfirst=True, errors='coerce')
    except Exception as e:
        log_message(f"Error reading CSV: {str(e)}"); return

    processed = {}
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, 'r', encoding='utf-8') as f: processed = json.load(f)

    if 'GOOGLE_CREDENTIALS' in os.environ:
        creds_content = os.environ['GOOGLE_CREDENTIALS'].strip().encode('utf-8').decode('utf-8-sig')
        with open(CREDENTIALS_FILE, 'w', encoding='utf-8') as f: f.write(creds_content)
    else: log_message("GOOGLE_CREDENTIALS not set"); return

    sheet_data = get_google_sheet_data()
    processed_lessons = sync_processed_with_sheet(processed, sheet_data) if sheet_data else set()

    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)

    try:
        login(driver)
        # Sắp xếp theo Start date (không dùng Rate)
        df_sorted = df.sort_values(by=['Start date'], ascending=[False])
        
        classes_processed = 0
        for _, row in df_sorted.iterrows():
            if classes_processed >= 50: break
            
            class_id = str(row['Class ID'])
            course_name = row['Course name']
            csv_total_sessions = int(row['Total Sessions'])

            # Kiểm tra xem đã hoàn thành lớp này chưa
            is_done = all(f"{class_id}:{i}" in processed_lessons for i in range(1, csv_total_sessions + 1))
            if is_done:
                continue

            process_class_id(driver, class_id, course_name, processed, processed_lessons, csv_total_sessions)
            classes_processed += 1
            
            # Git push sau mỗi lớp để lưu tiến độ
            if is_git_repository():
                try:
                    subprocess.run(["git", "add", PROCESSED_FILE], check=True)
                    subprocess.run(["git", "commit", "-m", f"Update Class {class_id}"], check=True)
                    subprocess.run(["git", "push"], check=True)
                except: pass

        log_message("Run completed")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
