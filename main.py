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
                log_message(f"Error reading Google Sheet: {str(e)}")
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
                    log_message(f"Lesson {lesson_number} of Class ID {class_id} already exists in Sheet")
                    return True
            worksheet.append_row(row_data)
            log_message(f"Updated Google Sheet for Class ID {class_id}, Lesson {lesson_number}")
            return True
        except Exception as e:
            log_message(f"Attempt {attempt+1}/{max_retries} failed to update Google Sheet for Class ID {class_id}, Lesson {lesson_number}: {str(e)}")
            if attempt == max_retries - 1:
                log_message(f"Error updating Google Sheet for Class ID {class_id}, Lesson {lesson_number}: {str(e)}")
                return False
            time.sleep(3)
    return False

def save_processed(processed):
    try:
        with open(PROCESSED_FILE, 'w', encoding='utf-8') as f:
            json.dump(processed, f, indent=2)
        log_message(f"Saved processed.json")
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
        if len(row) < 4:
            continue
        class_id = row[0]
        course_name = row[2]
        try:
            lesson_number = int(row[3])
        except ValueError:
            log_message(f"Invalid lesson number in Google Sheet for Class ID {class_id}: {row[3]}")
            continue
        processed_lessons.add(f"{class_id}:{lesson_number}")
        if course_name not in processed:
            processed[course_name] = {}
        if class_id not in processed[course_name]:
            processed[course_name][class_id] = {'last_lesson': -1, 'total_lessons': 0, 'has_errors': False}
        processed[course_name][class_id]['last_lesson'] = max(
            processed[course_name][class_id].get('last_lesson', -1),
            lesson_number - 1
        )
        processed[course_name][class_id]['has_errors'] = False
    save_processed(processed)
    return processed_lessons

def process_class_id(driver, class_id, course_name, processed, sheet_data, processed_lessons, csv_total_lessons):
    try:
        url = f"https://apps.cec.com.vn/student-calendar/class-detail?classID={class_id}"
        driver.get(url)
        log_message(f"Processing Class ID {class_id} for course {course_name}")
        time.sleep(5)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        class_code_element = WebDriverWait(driver, 40).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h2.d-flex div"))
        )
        class_code = class_code_element.text.strip()
        course_name_element = WebDriverWait(driver, 40).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'list-info')]//div[contains(text(), 'Course name')]/following-sibling::div"))
        )
        extracted_course_name = course_name_element.text.strip()
        if extracted_course_name != course_name:
            log_message(f"Course name mismatch for Class ID {class_id}: expected {course_name}, got {extracted_course_name}")
            return True
        class_progress = processed.get(course_name, {}).get(class_id, {})
        total_lessons_prev = class_progress.get('total_lessons', 0)
        lesson_rows = WebDriverWait(driver, 20).until(EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr")))
        total_lessons = len(lesson_rows)
        if total_lessons != total_lessons_prev:
            log_message(f"Total lessons updated for Class ID {class_id}: {total_lessons_prev} -> {total_lessons}")
        if total_lessons_prev == 0 and csv_total_lessons > 0:
            log_message(f"Using total lessons from id.csv for Class ID {class_id}: {csv_total_lessons}")
            total_lessons = max(total_lessons, csv_total_lessons)
        processed.setdefault(course_name, {})[class_id] = {
            'last_lesson': class_progress.get('last_lesson', -1),
            'total_lessons': total_lessons,
            'has_errors': class_progress.get('has_errors', False)
        }
        save_processed(processed)
        has_errors = False
        for lesson_index in range(class_progress.get('last_lesson', -1) + 1, total_lessons):
            unique_id = f"{class_id}:{lesson_index + 1}"  # lesson_number is 1-indexed
            if unique_id in processed_lessons:
                log_message(f"Skipping lesson {lesson_index + 1} for Class ID {class_id} - already in Sheet")
                processed[course_name][class_id]['last_lesson'] = lesson_index
                save_processed(processed)
                continue
            lesson_has_error = False
            retry_count = 0
            max_retries = 3
            while retry_count < max_retries:
                try:
                    lesson_rows = WebDriverWait(driver, 20).until(EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr")))
                    row = lesson_rows[lesson_index]
                    lesson_number = row.find_element(By.XPATH, "./td[4]").text.strip()
                    log_message(f"Processing lesson {lesson_number} for Class ID {class_id}")
                    report_link = "No report available"
                    try:
                        report_button = row.find_element(By.XPATH, ".//i[@data-v-50ef298c and contains(@class, 'isax-card-edit')]")
                        driver.execute_script("arguments[0].scrollIntoView(true);", report_button)
                        original_window = driver.current_window_handle
                        driver.execute_script("arguments[0].click();", report_button)
                        WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
                        new_window = [window for window in driver.window_handles if window != original_window][0]
                        driver.switch_to.window(new_window)
                        report_link = driver.current_url
                        if "docs.google.com/document" in report_link:
                            is_accessible, result = check_doc_accessibility(report_link)
                            if not is_accessible:
                                log_message(f"Invalid report link for lesson {lesson_number}: {result}")
                                lesson_has_error = True
                                has_errors = True
                        driver.close()
                        driver.switch_to.window(original_window)
                    except Exception as e:
                        log_message(f"Error getting report link for lesson {lesson_number}: {str(e)}")
                        lesson_has_error = True
                        has_errors = True
                    homework_content = "No homework available"
                    try:
                        homework_button = row.find_element(By.XPATH, ".//i[@data-v-50ef298c and contains(@class, 'isax-book-square')]")
                        driver.execute_script("arguments[0].scrollIntoView(true);", homework_button)
                        for attempt in range(3):
                            try:
                                driver.execute_script("arguments[0].click();", homework_button)
                                popup = WebDriverWait(driver, 30).until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".v-dialog--active")))
                                header = popup.find_element(By.CSS_SELECTOR, ".v-toolbar__title").text.strip()
                                text_actions = [elem.text.strip() for elem in popup.find_elements(By.CSS_SELECTOR, ".text-action")]
                                link_actions = popup.find_elements(By.CSS_SELECTOR, ".link-action")
                                homework_links = [f"{link.text.strip()}: {link.get_attribute('href')}" for link in link_actions]
                                for href in [l.split(': ')[1] for l in homework_links]:
                                    if 'docs.google.com' in href or 'drive.google.com' in href:
                                        is_accessible, result = check_doc_accessibility(href)
                                        if not is_accessible:
                                            log_message(f"Invalid homework link for lesson {lesson_number}: {result}")
                                            lesson_has_error = True
                                            has_errors = True
                                    else:
                                        response = requests.head(href, allow_redirects=True, timeout=10)
                                        if response.status_code != 200:
                                            log_message(f"Invalid homework link for lesson {lesson_number}: HTTP {response.status_code}")
                                            lesson_has_error = True
                                            has_errors = True
                                homework_content = f"Homework Header: {header}\n" + ("Text:\n" + "\n".join(text_actions) + "\n" if text_actions else "") + ("Links:\n" + "\n".join(homework_links) if homework_links else "")
                                log_message(f"Got homework for lesson {lesson_number}: {homework_content}")
                                try:
                                    popup.find_element(By.XPATH, ".//button[.//span[contains(text(), 'Cancel')]]").click()
                                except:
                                    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                                time.sleep(2)
                                break
                            except Exception as e:
                                if attempt == 2:
                                    log_message(f"Error opening homework popup for lesson {lesson_number}: {str(e)}")
                                    lesson_has_error = True
                                    has_errors = True
                                time.sleep(2)
                    except Exception as e:
                        log_message(f"Error getting homework for lesson {lesson_number}: {str(e)}")
                        lesson_has_error = True
                        has_errors = True
                    row_data = [str(class_id), class_code, course_name, lesson_number, report_link, homework_content, "OK" if not lesson_has_error else "Has Errors"]
                    processed[course_name][class_id]['last_lesson'] = lesson_index
                    processed[course_name][class_id]['has_errors'] = has_errors
                    save_processed(processed)
                    if update_google_sheet(row_data, class_id, lesson_number) and is_git_repository():
                        try:
                            subprocess.run(["git", "config", "--global", "user.name", "GitHub Action"], check=True)
                            subprocess.run(["git", "config", "--global", "user.email", "action@github.com"], check=True)
                            subprocess.run(["git", "add", PROCESSED_FILE], check=True)
                            subprocess.run(["git", "commit", "-m", f"Update processed.json for Class ID {class_id}, Lesson {lesson_number}"], check=True)
                            subprocess.run(["git", "push"], check=True)
                            log_message(f"Pushed processed.json for Class ID {class_id}, Lesson {lesson_number}")
                        except Exception as e:
                            log_message(f"Error committing/pushing processed.json for Class ID {class_id}, Lesson {lesson_number}: {str(e)}")
                    processed_lessons.add(unique_id)
                    break
                except StaleElementReferenceException:
                    retry_count += 1
                    log_message(f"Stale element in lesson {lesson_number}, retry {retry_count}/{max_retries}")
                    if retry_count == max_retries:
                        log_message(f"Failed lesson {lesson_number} after {max_retries} retries")
                        lesson_has_error = True
                        has_errors = True
                        processed[course_name][class_id]['last_lesson'] = lesson_index
                        processed[course_name][class_id]['has_errors'] = has_errors
                        save_processed(processed)
                        break
        log_message(f"Completed Class ID {class_id} for course {course_name}")
        return has_errors
    except Exception as e:
        log_message(f"Error processing Class ID {class_id}: {str(e)}")
        processed[course_name][class_id]['has_errors'] = True
        save_processed(processed)
        return True

def main():
    try:
        df = pd.read_csv(CSV_FILE)
        df['Start date'] = pd.to_datetime(df['Start date'], dayfirst=True, errors='coerce')
        if 'Lessons' not in df.columns:
            log_message("Error: 'Lessons' column not found in id.csv")
            return
    except Exception as e:
        log_message(f"Error reading CSV: {str(e)}")
        return
    processed = {}
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r', encoding='utf-8') as f:
                processed = json.load(f)
        except Exception as e:
            log_message(f"Error reading processed.json: {str(e)}")
    if 'GOOGLE_CREDENTIALS' in os.environ:
        try:
            creds_content = os.environ['GOOGLE_CREDENTIALS'].strip().encode('utf-8').decode('utf-8-sig')
            with open(CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
                json.dump(json.loads(creds_content), f, indent=2, ensure_ascii=False)
            log_message("Successfully wrote credentials.json from GOOGLE_CREDENTIALS")
        except Exception as e:
            log_message(f"Error writing credentials.json: {str(e)}")
            return
    else:
        log_message("GOOGLE_CREDENTIALS environment variable not set")
        return
    sheet_data = get_google_sheet_data()
    processed_lessons = sync_processed_with_sheet(processed, sheet_data) if sheet_data else set()
    if not sheet_data:
        log_message("Failed to retrieve Google Sheet data, proceeding with processed.json only")
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")
    options.add_argument("--disable-autofill")
    driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)
    try:
        login(driver)
        course_names = df['Course name'].unique()
        max_classes_per_run = 50
        classes_processed = 0
        for course_name in course_names:
            course_progress = processed.get(course_name, {})
            course_group = df[df['Course name'] == course_name]
            sorted_group = course_group.sort_values(by=['Start date', 'Rate'], ascending=[False, False])
            class_ids = sorted_group['Class ID'].unique().tolist()
            if not class_ids:
                log_message(f"No class IDs found for course {course_name}")
                continue
            for class_id in class_ids:
                if classes_processed >= max_classes_per_run:
                    log_message(f"Reached limit of {max_classes_per_run} classes processed this run, stopping")
                    break
                class_progress = course_progress.get(class_id, {'last_lesson': -1, 'total_lessons': 0})
                csv_total_lessons = int(df[df['Class ID'] == class_id]['Lessons'].iloc[0]) if not df[df['Class ID'] == class_id].empty else 0
                log_message(f"Class ID {class_id}: CSV lessons={csv_total_lessons}, Processed last_lesson={class_progress['last_lesson']}, total_lessons={class_progress['total_lessons']}")
                # Check if all lessons are in Google Sheet
                all_lessons_processed = True
                if csv_total_lessons > 0:
                    for lesson in range(1, csv_total_lessons + 1):
                        if f"{class_id}:{lesson}" not in processed_lessons:
                            all_lessons_processed = False
                            break
                if all_lessons_processed and class_progress.get('total_lessons', 0) <= csv_total_lessons:
                    log_message(f"Class ID {class_id} fully processed in Google Sheet, skipping")
                    continue
                has_errors = process_class_id(driver, class_id, course_name, processed, sheet_data, processed_lessons, csv_total_lessons)
                classes_processed += 1
                log_message(f"{'Success' if not has_errors else 'Has errors'} for Class ID {class_id} in course {course_name}")
        save_processed(processed)
        log_message("Run completed")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
