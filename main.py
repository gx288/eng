import pandas as pd
from datetime import datetime
import json
import shutil
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
    formatted_message = f"[{timestamp}] {message}"
    print(formatted_message)
    with open("class_info_log.txt", "a", encoding="utf-8") as f:
        f.write(f"{formatted_message}\n")

def check_doc_accessibility(url):
    try:
        if "docs.google.com/document" in url:
            doc_id = urlparse(url).path.split('/d/')[1].split('/')[0]
            export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"
            response = requests.head(export_url, allow_redirects=True, timeout=10)
            if response.status_code == 200:
                return True, export_url
            elif response.status_code in (401, 403):
                return False, "Requires authentication"
            elif response.status_code == 404:
                return False, "Deleted or not found"
            else:
                return False, f"HTTP {response.status_code}"
        return False, "Not a Google Docs URL"
    except Exception as e:
        return False, str(e)

def login(driver):
    driver.get("https://apps.cec.com.vn/login")
    current_id = os.getenv("CEC_USERNAME", "40183HN")  # Avoid system USERNAME
    password = os.getenv("CEC_PASSWORD", "1234567")
    try:
        username_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "input-14"))
        )
        log_message(f"Đang nhập username: {current_id}")
        print(f"DEBUG: current_id = '{current_id}'")  # Debug
        driver.execute_script("arguments[0].value = '';", username_field)  # Clear by JS
        username_field.send_keys(current_id)
        print(f"DEBUG: Username field value = '{username_field.get_attribute('value')}'")  # Check value

        password_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "input-18"))
        )
        log_message("Đang nhập password...")
        driver.execute_script("arguments[0].value = '';", password_field)
        password_field.send_keys(password)

        login_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
        )
        log_message("Nhấn nút đăng nhập...")
        login_button.click()
        time.sleep(5)
    except Exception as e:
        log_message(f"Lỗi khi đăng nhập: {str(e)}")
        raise

def update_google_sheet(row_data, class_id, lesson_number):
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
        # Check for duplicate
        existing_data = sheet.get_all_values()
        unique_id = f"{class_id}:{lesson_number}"
        for row in existing_data:
            if len(row) >= 4 and f"{row[0]}:{row[3]}" == unique_id:
                log_message(f"Skip append: Lesson {lesson_number} of Class ID {class_id} already exists in Sheet")
                return True
        sheet.append_row(row_data)
        log_message(f"Đã cập nhật Google Sheet cho Class ID {class_id}, Lesson {lesson_number}")
        return True
    except Exception as e:
        log_message(f"Lỗi khi cập nhật Google Sheet cho Class ID {class_id}, Lesson {lesson_number}: {str(e)}")
        return False

def save_processed(processed):
    try:
        with open(PROCESSED_FILE, 'w') as f:
            json.dump(processed, f, indent=2)
        log_message(f"Đã lưu processed.json")
    except Exception as e:
        log_message(f"Lỗi khi lưu processed.json: {str(e)}")

def process_class_id(class_id, course_name, processed):
    invalid = False
    has_errors = False
    driver = None
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")
        options.add_argument("--disable-autofill")
        driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)
        login(driver)
        url = f"https://apps.cec.com.vn/student-calendar/class-detail?classID={class_id}"
        driver.get(url)
        log_message(f"Đang truy cập trang: {url} (Class ID {class_id})")
        time.sleep(3)

        # Extract class_code (try multiple selectors)
        try:
            class_code_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h2.class-code, #class-code, h2.title-class, div.class-code"))
            )
            class_code = class_code_element.text.strip()
            log_message(f"Lấy được class code: {class_code} (Class ID {class_id})")
        except Exception as e:
            log_message(f"Lỗi khi lấy class code cho class ID {class_id}: {str(e)}")
            class_code = "Error"
            invalid = True
            has_errors = True

        # Extract course_name
        try:
            course_name_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".course-name-value, .course-name, div.label-course-name + div, div.course-info"))
            )
            extracted_course_name = course_name_element.text.strip()
            log_message(f"Lấy được course name: {extracted_course_name} (Class ID {class_id})")
            if extracted_course_name != course_name:
                log_message(f"Course name mismatch for class ID {class_id}: expected {course_name}, got {extracted_course_name}")
                invalid = True
                has_errors = True
        except Exception as e:
            log_message(f"Lỗi khi lấy course name cho class ID {class_id}: {str(e)}")
            invalid = True
            has_errors = True

        if invalid:
            return has_errors

        # Get start_lesson
        class_progress = processed.get(course_name, {}).get(class_id, {})
        start_lesson_index = class_progress.get('last_lesson', -1) + 1
        log_message(f"Resume Class ID {class_id} from lesson index {start_lesson_index}")

        # Fetch lesson rows
        lesson_rows = WebDriverWait(driver, 20).until(EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr")))
        total_lessons = len(lesson_rows)
        processed.setdefault(course_name, {})[class_id] = {'last_lesson': start_lesson_index - 1, 'total_lessons': total_lessons, 'has_errors': has_errors}
        save_processed(processed)

        for lesson_index in range(start_lesson_index, total_lessons):
            lesson_has_error = False
            retry_count = 0
            max_retries = 3
            while retry_count < max_retries:
                try:
                    lesson_rows = WebDriverWait(driver, 20).until(EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr")))
                    row = lesson_rows[lesson_index]
                    lesson_number = row.find_element(By.XPATH, "./td[4]").text.strip()
                    log_message(f"Processing lesson {lesson_number} (index {lesson_index}) for class ID {class_id}")

                    # Get report_link
                    report_link = "No report available"
                    try:
                        report_button = row.find_element(By.XPATH, ".//i[@data-v-50ef298c and contains(@class, 'isax-card-edit')]/parent::a")
                        report_link = report_button.get_attribute('href')
                        if "docs.google.com/document" in report_link:
                            is_accessible, result = check_doc_accessibility(report_link)
                            if not is_accessible:
                                log_message(f"Invalid report link for lesson {lesson_number}: {result}")
                                lesson_has_error = True
                                has_errors = True
                    except Exception as e:
                        log_message(f"Lỗi report link lesson {lesson_number}: {str(e)}")
                        lesson_has_error = True
                        has_errors = True

                    # Get homework_content
                    homework_content = "No homework available"
                    try:
                        homework_button = row.find_element(By.XPATH, ".//i[@data-v-50ef298c and contains(@class, 'isax-book-square')]")
                        homework_button.click()
                        time.sleep(2)
                        popup = WebDriverWait(driver, 20).until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".v-dialog--active")))
                        header = popup.find_element(By.CSS_SELECTOR, ".v-toolbar__title").text.strip()
                        text_actions = [elem.text.strip() for elem in popup.find_elements(By.CSS_SELECTOR, ".text-action")]
                        link_actions = popup.find_elements(By.CSS_SELECTOR, ".link-action")
                        homework_links = [f"{link.text.strip()}: {link.get_attribute('href')}" for link in link_actions]
                        for href in [l.split(': ')[1] for l in homework_links if 'docs.google.com' not in l and 'drive.google.com' not in l]:
                            try:
                                response = requests.head(href, allow_redirects=True, timeout=10)
                                if response.status_code != 200:
                                    lesson_has_error = True
                                    has_errors = True
                            except:
                                lesson_has_error = True
                                has_errors = True

                        homework_content = f"Homework Header: {header}\n" + ("Text:\n" + "\n".join(text_actions) + "\n" if text_actions else "") + ("Links:\n" + "\n".join(homework_links) if homework_links else "")

                        try:
                            popup.find_element(By.XPATH, ".//button[.//span[contains(text(), 'Cancel')]]").click()
                        except:
                            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                        time.sleep(2)
                    except Exception as e:
                        log_message(f"Lỗi homework lesson {lesson_number}: {str(e)}")
                        lesson_has_error = True
                        has_errors = True

                    # Update Sheet
                    row_data = [str(class_id), class_code, course_name, lesson_number, report_link, homework_content, "OK" if not lesson_has_error else "Has Errors"]
                    update_google_sheet(row_data, class_id, lesson_number)

                    # Update processed
                    processed[course_name][class_id]['last_lesson'] = lesson_index
                    processed[course_name][class_id]['has_errors'] = has_errors
                    save_processed(processed)

                    # Commit/push processed.json
                    try:
                        import subprocess
                        subprocess.run(["git", "config", "--global", "user.name", "GitHub Action"])
                        subprocess.run(["git", "config", "--global", "user.email", "action@github.com"])
                        subprocess.run(["git", "add", PROCESSED_FILE])
                        subprocess.run(["git", "commit", "-m", f"Update processed.json for Class ID {class_id}, Lesson {lesson_number}"])
                        subprocess.run(["git", "push"])
                        log_message(f"Pushed processed.json for Class ID {class_id}, Lesson {lesson_number}")
                    except Exception as e:
                        log_message(f"Lỗi khi commit/push processed.json cho Class ID {class_id}, Lesson {lesson_number}: {str(e)}")

                    break
                except StaleElementReferenceException:
                    retry_count += 1
                    log_message(f"Stale element in lesson {lesson_number}, retry {retry_count}/{max_retries}")
                    time.sleep(2)
                    if retry_count == max_retries:
                        log_message(f"Failed lesson {lesson_number} after {max_retries} retries")
                        lesson_has_error = True
                        has_errors = True
                        break

        log_message(f"Hoàn thành Class ID {class_id} cho course {course_name}")
        return has_errors

    except Exception as e:
        log_message(f"Lỗi chung Class ID {class_id}: {str(e)}")
        return True
    finally:
        if driver:
            driver.quit()

def main():
    try:
        df = pd.read_csv(CSV_FILE)
        df['Start date'] = pd.to_datetime(df['Start date'], dayfirst=True)
    except Exception as e:
        log_message(f"Lỗi đọc CSV: {str(e)}")
        return

    processed = {}
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r') as f:
                processed = json.load(f)
        except Exception as e:
            log_message(f"Lỗi đọc processed: {str(e)}")

    course_names = df['Course name'].unique()
    max_classes_per_run = 50  # Limit to avoid timeout
    classes_processed = 0

    for course_name in course_names:
        if classes_processed >= max_classes_per_run:
            log_message(f"Đạt giới hạn {max_classes_per_run} classes/run, dừng lại")
            break
        course_progress = processed.get(course_name, {})
        if any(class_prog.get('last_lesson', -1) + 1 >= class_prog.get('total_lessons', 0) for class_prog in course_progress.values()):
            log_message(f"Course {course_name} đã hoàn thành ít nhất 1 class")
            continue

        log_message(f"Bắt đầu course: {course_name}")
        course_group = df[df['Course name'] == course_name]
        sorted_group = course_group.sort_values(by=['Start date', 'Rate'], ascending=[False, False])
        class_ids = sorted_group['Class ID'].tolist()
        max_class_ids = min(3, len(class_ids))
        course_has_success = False

        for i in range(max_class_ids):
            class_id = class_ids[i]
            if class_id in course_progress and course_progress[class_id].get('last_lesson', -1) + 1 >= course_progress[class_id].get('total_lessons', 0):
                log_message(f"Class ID {class_id} đã hoàn thành cho course {course_name}")
                continue

            log_message(f"Process Class ID {class_id} (thứ {i+1}/{max_class_ids}) cho course {course_name}")
            has_errors = process_class_id(class_id, course_name, processed)
            if not has_errors:
                course_has_success = True
                log_message(f"Success Class ID {class_id} cho course {course_name}")
                break
            else:
                log_message(f"Has errors Class ID {class_id}, thử class tiếp theo")
            classes_processed += 1

    save_processed(processed)
    log_message("Hoàn thành run")

if __name__ == "__main__":
    if 'CREDENTIALS_JSON' in os.environ:
        with open(CREDENTIALS_FILE, 'w') as f:
            f.write(os.environ['CREDENTIALS_JSON'])
    main()
