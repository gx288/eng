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
from multiprocessing import Pool, cpu_count

# Configuration
CSV_FILE = "id.csv"
PROCESSED_FILE = "processed.json"
CREDENTIALS_FILE = "credentials.json"  # Will come from GitHub Secrets
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
    current_id = os.getenv("USERNAME", "40183HN")  # From GitHub Secrets
    password = os.getenv("PASSWORD", "1234567")   # From GitHub Secrets
    try:
        username_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "input-14"))
        )
        log_message(f"Đang nhập username: {current_id}")
        username_field.clear()
        username_field.send_keys(current_id)

        password_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "input-18"))
        )
        log_message("Đang nhập password...")
        password_field.clear()
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
        scope = SCOPES
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
        sheet.append_row(row_data)
        log_message(f"Đã cập nhật Google Sheet cho Class ID {class_id}, Lesson {lesson_number}")
        return True
    except Exception as e:
        log_message(f"Lỗi khi cập nhật Google Sheet cho Class ID {class_id}, Lesson {lesson_number}: {str(e)}")
        return False

def process_class_id(args):
    class_id, course_name = args
    invalid = False
    has_errors = False
    driver = None
    folder_name = None
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--start-maximized")
        driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)
        login(driver)
        url = f"https://apps.cec.com.vn/student-calendar/class-detail?classID={class_id}"
        driver.get(url)
        log_message(f"Đang truy cập trang: {url} (Class ID {class_id})")
        time.sleep(3)

        # Extract class code
        try:
            class_code_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//h2[@data-v-50ef298c]/div[@data-v-50ef298c]"))
            )
            class_code = class_code_element.text.strip()
            log_message(f"Lấy được class code: {class_code} (Class ID {class_id})")
        except Exception as e:
            log_message(f"Lỗi khi lấy class code cho class ID {class_id}: {str(e)}")
            class_code = "Error"
            invalid = True
            has_errors = True

        # Extract course name (confirm)
        try:
            course_name_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//div[@data-v-50ef298c and contains(@class, 'col-md-4 col') and text()='Course name']/following-sibling::div[@data-v-50ef298c and contains(@class, 'col-md-8 col')]"))
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
            return None, None, has_errors

        report_links = []
        homework_contents = []

        # Collect all report links and homework data
        lesson_index = 0
        max_retries = 3
        while True:
            try:
                # Re-fetch lesson rows to handle page reloads
                lesson_rows = WebDriverWait(driver, 20).until(
                    EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr"))
                )
                if lesson_index >= len(lesson_rows):
                    break  # Exit loop when all lessons are processed

                row = lesson_rows[lesson_index]
                retry_count = 0
                lesson_has_error = False
                while retry_count < max_retries:
                    try:
                        lesson_number = row.find_element(By.XPATH, "./td[4]").text.strip()
                        log_message(f"Processing lesson {lesson_number} for class ID {class_id}")

                        # Get report link
                        report_link = "No report available"
                        try:
                            report_button = row.find_element(By.XPATH, ".//i[@data-v-50ef298c and contains(@class, 'isax-card-edit')]/parent::a")
                            report_link = report_button.get_attribute('href')
                            log_message(f"Lấy được report link cho lesson {lesson_number}: {report_link} (Class ID {class_id})")
                            if "docs.google.com/document" in report_link:
                                is_accessible, result = check_doc_accessibility(report_link)
                                if not is_accessible:
                                    log_message(f"Invalid report link for lesson {lesson_number}: {result} (Class ID {class_id})")
                                    lesson_has_error = True
                                    has_errors = True
                            else:
                                log_message(f"Invalid report link format for lesson {lesson_number}: {report_link} (Class ID {class_id})")
                                lesson_has_error = True
                                has_errors = True
                        except Exception as e:
                            log_message(f"Lỗi khi lấy report link cho lesson {lesson_number}: {str(e)} (Class ID {class_id})")
                            lesson_has_error = True
                            has_errors = True

                        # Get homework popup
                        homework_content = "No homework available"
                        try:
                            # Re-fetch row to avoid stale element
                            lesson_rows = WebDriverWait(driver, 20).until(
                                EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr"))
                            )
                            row = lesson_rows[lesson_index]
                            homework_button = row.find_element(By.XPATH, ".//i[@data-v-50ef298c and contains(@class, 'isax-book-square')]")
                            homework_button.click()
                            log_message(f"Đã nhấn nút homework cho class ID {class_id}, lesson {lesson_number}")
                            time.sleep(2)

                            popup = WebDriverWait(driver, 20).until(
                                EC.visibility_of_element_located((By.CSS_SELECTOR, ".v-dialog--active"))
                            )
                            header = popup.find_element(By.CSS_SELECTOR, ".v-toolbar__title").text.strip()
                            text_actions = [elem.text.strip() for elem in popup.find_elements(By.CSS_SELECTOR, ".text-action")]
                            link_actions = popup.find_elements(By.CSS_SELECTOR, ".link-action")
                            homework_links = []
                            for link in link_actions:
                                href = link.get_attribute('href')
                                text = link.text.strip()
                                homework_links.append(f"{text}: {href}")
                                # Check non-Drive, non-Docs links (e.g., Quizlet)
                                if 'docs.google.com' not in href and 'drive.google.com' not in href:
                                    try:
                                        response = requests.head(href, allow_redirects=True, timeout=10)
                                        if response.status_code != 200:
                                            log_message(f"Invalid homework link for lesson {lesson_number}: {href} (HTTP {response.status_code}) (Class ID {class_id})")
                                            lesson_has_error = True
                                            has_errors = True
                                    except Exception as e:
                                        log_message(f"Error checking homework link for lesson {lesson_number}: {href} ({str(e)}) (Class ID {class_id})")
                                        lesson_has_error = True
                                        has_errors = True

                            homework_content = f"Homework Header: {header}\n"
                            if text_actions:
                                homework_content += "Text Announcements:\n" + "\n".join(text_actions) + "\n"
                            if homework_links:
                                homework_content += "Links:\n" + "\n".join(homework_links) + "\n"

                            # Close popup
                            try:
                                cancel_button = popup.find_element(By.XPATH, ".//button[.//span[contains(text(), 'Cancel')]]")
                                cancel_button.click()
                                log_message(f"Đã đóng popup homework cho lesson {lesson_number} (Class ID {class_id})")
                            except NoSuchElementException:
                                log_message(f"Không tìm thấy nút Cancel, thử đóng popup bằng ESC cho lesson {lesson_number} (Class ID {class_id})")
                                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                            time.sleep(2)  # Wait for page to stabilize
                        except Exception as e:
                            log_message(f"Lỗi khi lấy homework cho lesson {lesson_number}: {str(e)} (Class ID {class_id})")
                            lesson_has_error = True
                            has_errors = True

                        # Prepare row data for this lesson
                        row_data = [str(class_id), class_code, course_name, lesson_number, report_link, homework_content]
                        if lesson_has_error:
                            row_data.append("Has Errors")  # Add status column if needed
                        else:
                            row_data.append("OK")

                        # Update Google Sheet immediately for this lesson
                        if not update_google_sheet(row_data, class_id, lesson_number):
                            has_errors = True

                        report_links.append((lesson_number, report_link))
                        homework_contents.append((lesson_number, homework_content))
                        lesson_index += 1
                        break  # Exit retry loop on success
                    except StaleElementReferenceException:
                        retry_count += 1
                        log_message(f"Stale element in lesson {lesson_number}, retry {retry_count}/{max_retries} (Class ID {class_id})")
                        time.sleep(2)
                        if retry_count == max_retries:
                            log_message(f"Failed to process lesson {lesson_number} after {max_retries} retries (Class ID {class_id})")
                            has_errors = True
                            lesson_index += 1  # Skip to next lesson
                            break
                        # Re-fetch lesson rows for retry
                        lesson_rows = WebDriverWait(driver, 20).until(
                            EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr"))
                        )
                        row = lesson_rows[lesson_index]
            except Exception as e:
                log_message(f"Lỗi khi lấy danh sách lesson cho class ID {class_id}: {str(e)}")
                invalid = True
                has_errors = True
                break

        if invalid and not report_links and not homework_contents:
            return None, None, has_errors

        # Save homework and report info locally if needed (optional, as per original)
        folder_name = f"{course_name.replace(' ', '_')}"
        os.makedirs(folder_name, exist_ok=True)
        for lesson_number, homework_content in homework_contents:
            homework_file = os.path.join(folder_name, f"lesson_{lesson_number}_btvn.txt")
            with open(homework_file, "w", encoding="utf-8") as f:
                f.write(homework_content)

        with open(f"{folder_name}/report_info.txt", "w", encoding="utf-8") as f:
            f.write(f"Class ID: {class_id}\n")
            f.write(f"Class Code: {class_code}\n")
            f.write(f"Course Name: {course_name}\n")
            f.write("Report Links:\n")
            for lesson_number, report_link in report_links:
                f.write(f"Lesson {lesson_number}: {report_link}\n")
            f.write("\nHomework Contents:\n")
            for lesson_number, homework_content in homework_contents:
                f.write(f"Lesson {lesson_number}:\n{homework_content}\n")

        return class_id, course_name, has_errors

    except Exception as e:
        log_message(f"Lỗi chung khi xử lý class ID {class_id}: {str(e)}")
        if folder_name and os.path.exists(folder_name):
            shutil.rmtree(folder_name)
            log_message(f"Đã xóa thư mục {folder_name} do lỗi (Class ID {class_id})")
        return None, None, True
    finally:
        if driver:
            driver.quit()

def main():
    try:
        df = pd.read_csv(CSV_FILE)
        df['Start date'] = pd.to_datetime(df['Start date'], dayfirst=True)
    except Exception as e:
        log_message(f"Lỗi khi đọc file CSV {CSV_FILE}: {str(e)}")
        return

    processed = {}
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r') as f:
                processed = json.load(f)
        except Exception as e:
            log_message(f"Lỗi khi đọc file {PROCESSED_FILE}: {str(e)}")

    course_names = df['Course name'].unique()
    args_list = []

    for course_name in course_names:
        if course_name in processed:
            log_message(f"Đã xử lý course {course_name} với Class ID {processed[course_name]}")
            continue

        log_message(f"Bắt đầu xử lý course: {course_name}")
        course_group = df[df['Course name'] == course_name]
        sorted_group = course_group.sort_values(by=['Start date', 'Rate'], ascending=[False, False])
        class_ids = sorted_group['Class ID'].tolist()
        max_class_ids = min(3, len(class_ids))
        success = False

        for i in range(max_class_ids):
            class_id = class_ids[i]
            log_message(f"Thử xử lý Class ID {class_id} (thứ {i+1}/{max_class_ids}) cho course {course_name}")
            args_list.append((class_id, course_name))
            # Since parallel, but to ensure one per course first, we collect all but process in order later if needed
            # But with pool.map, it processes in order of args_list, so by building list course-by-course, it prioritizes one per course

    # Parallel processing
    num_processes = min(cpu_count(), 4)  # Limit to 4 to avoid overload on GitHub
    with Pool(num_processes) as pool:
        results = pool.map(process_class_id, args_list)

    # Post-process results
    for successful_id, course_name, has_errors in results:
        if successful_id and not has_errors:
            processed[course_name] = successful_id
            log_message(f"Hoàn thành course {course_name} với Class ID {successful_id} (không có lỗi)")

    # Save processed courses
    try:
        with open(PROCESSED_FILE, 'w') as f:
            json.dump(processed, f, indent=2)
        log_message(f"Đã lưu danh sách course đã xử lý vào {PROCESSED_FILE}")
    except Exception as e:
        log_message(f"Lỗi khi lưu file {PROCESSED_FILE}: {str(e)}")

if __name__ == "__main__":
    # Write credentials from secrets
    if 'CREDENTIALS_JSON' in os.environ:
        with open(CREDENTIALS_FILE, 'w') as f:
            f.write(os.environ['CREDENTIALS_JSON'])

    main()
