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
MAX_CLASSES_PER_RUN = 50  # Limit to 50 Class IDs per run

# Initialize Selenium WebDriver with webdriver-manager
options = webdriver.ChromeOptions()
options.add_argument("--start-maximized")
driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)

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

def login():
    driver.get("https://apps.cec.com.vn/login")
    current_id = "40183HN"
    password = "1234567"
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

def sync_processed_from_sheet(processed):
    try:
        scope = SCOPES
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
        existing_data = sheet.get_all_values()
        if not existing_data:
            return processed
        class_id_idx = 0  # Assuming column 1 is Class ID
        course_name_idx = 2  # Assuming column 3 is Course Name
        updated = False
        for row in existing_data[1:]:  # Skip header
            if len(row) > max(class_id_idx, course_name_idx):
                class_id = row[class_id_idx]
                course_name = row[course_name_idx]
                if course_name not in processed:
                    processed[course_name] = class_id
                    log_message(f"Synced {course_name}: {class_id} from Google Sheet to processed")
                    updated = True
        if updated:
            save_processed(processed)
        return processed
    except Exception as e:
        log_message(f"Lỗi khi đồng bộ từ Google Sheet: {str(e)}")
        return processed

def save_processed(processed):
    try:
        with open(PROCESSED_FILE, 'w') as f:
            json.dump(processed, f, indent=2)
        log_message(f"Đã lưu danh sách course đã xử lý vào {PROCESSED_FILE}")
    except Exception as e:
        log_message(f"Lỗi khi lưu file {PROCESSED_FILE}: {str(e)}")

def update_google_sheet(sheet_data, class_id):
    try:
        scope = SCOPES
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
        for row in sheet_data:
            sheet.append_row(row)
        log_message(f"Đã cập nhật Google Sheet cho Class ID {class_id}")
        return True
    except Exception as e:
        log_message(f"Lỗi khi cập nhật Google Sheet cho Class ID {class_id}: {str(e)}")
        return False

def process_class_id(class_id, course_name):
    invalid = False
    folder_name = None
    try:
        url = f"https://apps.cec.com.vn/student-calendar/class-detail?classID={class_id}"
        driver.get(url)
        log_message(f"Đang truy cập trang: {url}")
        time.sleep(5)

        # Extract class code
        try:
            class_code_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//h2[@data-v-50ef298c]/div[@data-v-50ef298c]"))
            )
            class_code = class_code_element.text.strip()
            log_message(f"Lấy được class code: {class_code}")
        except Exception as e:
            log_message(f"Lỗi khi lấy class code cho class ID {class_id}: {str(e)}")
            class_code = "Error"
            invalid = True

        # Extract course name (confirm)
        try:
            course_name_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//div[@data-v-50ef298c and contains(@class, 'col-md-4 col') and text()='Course name']/following-sibling::div[@data-v-50ef298c and contains(@class, 'col-md-8 col')]"))
            )
            extracted_course_name = course_name_element.text.strip()
            log_message(f"Lấy được course name: {extracted_course_name}")
            if extracted_course_name != course_name:
                log_message(f"Course name mismatch for class ID {class_id}: expected {course_name}, got {extracted_course_name}")
                invalid = True
        except Exception as e:
            log_message(f"Lỗi khi lấy course name cho class ID {class_id}: {str(e)}")
            invalid = True

        if invalid:
            return None, None

        folder_name = f"{course_name.replace(' ', '_')}"
        os.makedirs(folder_name, exist_ok=True)
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
                while retry_count < max_retries:
                    try:
                        lesson_number = row.find_element(By.XPATH, "./td[4]").text.strip()
                        log_message(f"Processing lesson {lesson_number} for class ID {class_id}")

                        # Get report link
                        report_link = "No report available"
                        try:
                            report_button = row.find_element(By.XPATH, ".//i[@data-v-50ef298c and contains(@class, 'isax-card-edit')]")
                            report_button.click()
                            log_message(f"Đã nhấn nút report cho class ID {class_id}, lesson {lesson_number}")
                            time.sleep(3)
                            if len(driver.window_handles) > 1:
                                driver.switch_to.window(driver.window_handles[-1])
                                report_link = driver.current_url
                                log_message(f"Lấy được report link cho lesson {lesson_number}: {report_link}")
                                driver.close()
                                driver.switch_to.window(driver.window_handles[0])
                                # Check report link validity
                                if "docs.google.com/document" in report_link:
                                    is_accessible, result = check_doc_accessibility(report_link)
                                    if not is_accessible:
                                        log_message(f"Invalid report link for lesson {lesson_number}: {result}")
                                else:
                                    log_message(f"Invalid report link format for lesson {lesson_number}: {report_link}")
                        except Exception as e:
                            log_message(f"Lỗi khi lấy report link cho lesson {lesson_number}: {str(e)}")

                        # Get homework popup
                        homework_content = "No homework available"
                        try:
                            homework_button = row.find_element(By.XPATH, ".//i[@data-v-50ef298c and contains(@class, 'isax-book-square')]")
                            homework_button.click()
                            log_message(f"Đã nhấn nút homework cho class ID {class_id}, lesson {lesson_number}")
                            time.sleep(3)

                            popup = WebDriverWait(driver, 30).until(
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
                                            log_message(f"Invalid homework link for lesson {lesson_number}: {href} (HTTP {response.status_code})")
                                        else:
                                            log_message(f"Valid homework link for lesson {lesson_number}: {href} (HTTP {response.status_code})")
                                    except Exception as e:
                                        log_message(f"Error checking homework link for lesson {lesson_number}: {href} ({str(e)})")
                                elif 'drive.google.com' in href:
                                    is_accessible, result = check_doc_accessibility(href)
                                    if not is_accessible:
                                        log_message(f"Invalid homework Drive link for lesson {lesson_number}: {result}")

                            homework_content = f"Homework Header: {header}\n"
                            if text_actions:
                                homework_content += "Text Announcements:\n" + "\n".join(text_actions) + "\n"
                            if homework_links:
                                homework_content += "Links:\n" + "\n".join(homework_links) + "\n"

                            # Close popup
                            try:
                                cancel_button = popup.find_element(By.XPATH, ".//button[.//span[contains(text(), 'Cancel')]]")
                                cancel_button.click()
                                log_message(f"Đã đóng popup homework cho lesson {lesson_number}")
                            except NoSuchElementException:
                                log_message(f"Không tìm thấy nút Cancel, thử đóng popup bằng ESC cho lesson {lesson_number}")
                                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                            time.sleep(3)  # Wait for page to stabilize after reload
                        except Exception as e:
                            log_message(f"Lỗi khi lấy homework cho lesson {lesson_number}: {str(e)}")

                        report_links.append((lesson_number, report_link))
                        homework_contents.append((lesson_number, homework_content))
                        lesson_index += 1
                        break  # Exit retry loop on success
                    except StaleElementReferenceException:
                        retry_count += 1
                        log_message(f"Stale element in lesson {lesson_number}, retry {retry_count}/{max_retries}")
                        time.sleep(2)
                        if retry_count == max_retries:
                            log_message(f"Failed to process lesson {lesson_number} after {max_retries} retries")
                            break
                        # Re-fetch lesson rows for retry
                        lesson_rows = WebDriverWait(driver, 20).until(
                            EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr"))
                        )
                        row = lesson_rows[lesson_index]
                if invalid:
                    break
            except Exception as e:
                log_message(f"Lỗi khi lấy danh sách lesson cho class ID {class_id}: {str(e)}")
                invalid = True
                break

        if invalid:
            if folder_name and os.path.exists(folder_name):
                shutil.rmtree(folder_name)
                log_message(f"Đã xóa thư mục {folder_name} do lỗi")
            return None, None

        # Save homework txt
        for lesson_number, homework_content in homework_contents:
            homework_file = os.path.join(folder_name, f"lesson_{lesson_number}_btvn.txt")
            with open(homework_file, "w", encoding="utf-8") as f:
                f.write(homework_content)

        # Save report info
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

        # Prepare sheet data
        sheet_data = []
        for i in range(len(report_links)):
            lesson_number = report_links[i][0]
            report_link = report_links[i][1]
            homework_content = homework_contents[i][1]
            sheet_data.append([str(class_id), class_code, course_name, lesson_number, report_link, homework_content])

        # Write to Google Sheet immediately
        if sheet_data:
            update_google_sheet(sheet_data, class_id)

        return sheet_data, class_id

    except Exception as e:
        log_message(f"Lỗi chung khi xử lý class ID {class_id}: {str(e)}")
        if folder_name and os.path.exists(folder_name):
            shutil.rmtree(folder_name)
            log_message(f"Đã xóa thư mục {folder_name} do lỗi")
        return None, None

def main():
    # Load CSV
    try:
        df = pd.read_csv(CSV_FILE)
        df['Start date'] = pd.to_datetime(df['Start date'], dayfirst=True)
    except Exception as e:
        log_message(f"Lỗi khi đọc file CSV {CSV_FILE}: {str(e)}")
        return

    grouped = df.groupby('Course name')

    # Load processed courses
    processed = {}
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r') as f:
                processed = json.load(f)
        except Exception as e:
            log_message(f"Lỗi khi đọc file {PROCESSED_FILE}: {str(e)}")

    # Sync from Google Sheet
    processed = sync_processed_from_sheet(processed)

    classes_processed_this_run = 0
    for course_name, group in grouped:
        if classes_processed_this_run >= MAX_CLASSES_PER_RUN:
            log_message(f"Đã đạt giới hạn {MAX_CLASSES_PER_RUN} ID cho lần chạy này, dừng lại")
            break
        if course_name in processed:
            log_message(f"Đã xử lý course {course_name} với Class ID {processed[course_name]}")
            continue

        log_message(f"Bắt đầu xử lý course: {course_name}")
        sorted_group = group.sort_values(by=['Start date', 'Rate'], ascending=[False, False])
        for _, row in sorted_group.iterrows():
            class_id = row['Class ID']
            log_message(f"Thử xử lý Class ID {class_id} cho course {course_name}")
            sheet_data, successful_id = process_class_id(class_id, course_name)
            if successful_id:
                processed[course_name] = successful_id
                classes_processed_this_run += 1
                log_message(f"Hoàn thành xử lý course {course_name} với Class ID {successful_id}")
                save_processed(processed)
                break
        else:
            log_message(f"Không tìm thấy Class ID hợp lệ cho course {course_name}")

if __name__ == "__main__":
    try:
        login()
        main()
    finally:
        driver.quit()
