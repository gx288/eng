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

# ────────────────────────────────────────────────
#               CẤU HÌNH
# ────────────────────────────────────────────────

CLASS_IDS = [
    "10539",  # thử lại, giờ sẽ skip nếu bảng bất thường
    # thêm các lớp khác
]

PROCESSED_FILE    = "processed.json"
CREDENTIALS_FILE  = "credentials.json"
SHEET_ID          = "1-MMsbAGlg7MNbBPAzioqARu6QLfry5mCrWJ-Q_aqmIM"
SHEET_NAME        = "10539"
SCOPES            = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

MAX_CLASSES_PER_RUN = 50

# ────────────────────────────────────────────────

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
            resp = requests.head(export_url, allow_redirects=True, timeout=10)
            return resp.status_code == 200, export_url if resp.status_code == 200 else f"HTTP {resp.status_code}"
        if "drive.google.com" in url:
            resp = requests.head(url, allow_redirects=True, timeout=10)
            return resp.status_code == 200, url if resp.status_code == 200 else f"HTTP {resp.status_code}"
        return False, "Not supported"
    except Exception as e:
        return False, str(e)

def login(driver):
    driver.get("https://apps.cec.com.vn/login")
    username = os.getenv("CEC_USERNAME", "40183HN")
    password = os.getenv("CEC_PASSWORD", "1234567")
    try:
        WebDriverWait(driver, 30).until(EC.visibility_of_element_located((By.ID, "input-14"))).send_keys(username)
        WebDriverWait(driver, 30).until(EC.visibility_of_element_located((By.ID, "input-18"))).send_keys(password)
        WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))).click()
        time.sleep(5)
        if "login" in driver.current_url:
            raise Exception("Login failed")
        log_message("Đăng nhập thành công")
    except Exception as e:
        log_message(f"Lỗi đăng nhập: {str(e)}")
        raise

def get_google_sheet_data():
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID)
        return sheet.worksheet(SHEET_NAME).get_all_values()
    except Exception as e:
        log_message(f"Không đọc được Google Sheet: {str(e)}")
        return []

def update_google_sheet(row_data, class_id, lesson_number):
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID)
        worksheet = sheet.worksheet(SHEET_NAME)
        data = worksheet.get_all_values()
        unique = f"{class_id}:{lesson_number}"
        for row in data:
            if len(row) >= 4 and f"{row[0]}:{row[3]}" == unique:
                log_message(f"Buổi {lesson_number} lớp {class_id} đã có → bỏ qua")
                return True
        worksheet.append_row(row_data)
        log_message(f"Đã ghi buổi {lesson_number} lớp {class_id}")
        return True
    except Exception as e:
        log_message(f"Lỗi ghi sheet {class_id} buổi {lesson_number}: {str(e)}")
        return False

def save_processed(processed):
    try:
        with open(PROCESSED_FILE, 'w', encoding='utf-8') as f:
            json.dump(processed, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_message(f"Lỗi lưu processed.json: {str(e)}")

def sync_processed_with_sheet(processed, sheet_data):
    processed_lessons = set()
    for row in sheet_data:
        if len(row) < 4: continue
        cid = row[0]
        try:
            ln = int(row[3])
        except:
            continue
        processed_lessons.add(f"{cid}:{ln}")
        if cid not in processed:
            processed[cid] = {'last_lesson': -1, 'total_lessons': 0}
        processed[cid]['last_lesson'] = max(processed[cid].get('last_lesson', -1), ln - 1)
    save_processed(processed)
    return processed_lessons

def process_class(driver, class_id, processed, processed_lessons):
    try:
        driver.get(f"https://apps.cec.com.vn/student-calendar/class-detail?classID={class_id}")
        log_message(f"Xử lý lớp {class_id}")
        time.sleep(5)

        lesson_rows = WebDriverWait(driver, 25).until(
            EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr"))
        )
        total_lessons = len(lesson_rows)
        log_message(f"Lớp {class_id} có {total_lessons} buổi")

        if not lesson_rows:
            log_message(f"Lớp {class_id}: Không có buổi nào → bỏ qua")
            return False

        # Kiểm tra cấu trúc bảng (cách cũ: dựa vào số cột hoặc tồn tại icon)
        first_row = lesson_rows[0]
        tds = first_row.find_elements(By.TAG_NAME, "td")
        num_columns = len(tds)
        log_message(f"Lớp {class_id} - số cột: {num_columns}")

        # Nếu số cột < 4 hoặc không có icon Action → skip lớp
        has_action_icon = False
        try:
            first_row.find_element(By.XPATH, ".//i[contains(@class, 'isax-card-edit')]")
            has_action_icon = True
        except:
            pass

        if num_columns < 4 or not has_action_icon:
            log_message(f"Lớp {class_id} bảng bất thường (ít cột hoặc thiếu icon Action) → bỏ qua lớp này")
            return False

        progress = processed.get(class_id, {'last_lesson': -1, 'total_lessons': 0})
        start_from = progress['last_lesson'] + 1

        processed[class_id] = {'last_lesson': progress['last_lesson'], 'total_lessons': total_lessons}

        has_error = False

        for i in range(start_from, total_lessons):
            unique = f"{class_id}:{i+1}"
            if unique in processed_lessons:
                log_message(f"Bỏ qua buổi {i+1} lớp {class_id} (đã có)")
                processed[class_id]['last_lesson'] = i
                save_processed(processed)
                continue

            lesson_error = False
            retry = 0
            while retry < 3:
                try:
                    rows = driver.find_elements(By.XPATH, "//tbody/tr")
                    row = rows[i]

                    # Lấy lesson number (cách cũ + fallback)
                    try:
                        lesson_num = row.find_element(By.XPATH, "./td[4]").text.strip()
                        if not lesson_num.isdigit():
                            lesson_num = str(i + 1)
                    except:
                        lesson_num = str(i + 1)
                        log_message(f"Buổi {i+1}: Không lấy được td[4] → dùng {lesson_num}")

                    # Report
                    report_link = "No report available"
                    try:
                        btn = row.find_element(By.XPATH, ".//i[contains(@class, 'isax-card-edit')]")
                        driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                        original_win = driver.current_window_handle
                        driver.execute_script("arguments[0].click();", btn)
                        WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
                        new_win = [w for w in driver.window_handles if w != original_win][0]
                        driver.switch_to.window(new_win)
                        report_link = driver.current_url
                        if "docs.google.com" in report_link:
                            ok, msg = check_doc_accessibility(report_link)
                            if not ok:
                                lesson_error = True
                        driver.close()
                        driver.switch_to.window(original_win)
                    except:
                        log_message(f"Buổi {lesson_num}: Không có nút report → skip phần này")

                    # Homework (tương tự)
                    homework_content = "No homework available"
                    try:
                        btn = row.find_element(By.XPATH, ".//i[contains(@class, 'isax-book-square')]")
                        driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                        driver.execute_script("arguments[0].click();", btn)
                        popup = WebDriverWait(driver, 20).until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".v-dialog--active")))
                        header = popup.find_element(By.CSS_SELECTOR, ".v-toolbar__title").text.strip()
                        texts = [el.text.strip() for el in popup.find_elements(By.CSS_SELECTOR, ".text-action")]
                        links = [f"{el.text.strip()}: {el.get_attribute('href')}" for el in popup.find_elements(By.CSS_SELECTOR, ".link-action") if el.get_attribute('href')]
                        homework_content = f"Header: {header}\nText:\n" + "\n".join(texts) + "\nLinks:\n" + "\n".join(links)
                        try:
                            popup.find_element(By.XPATH, ".//button[.//span[contains(text(), 'Cancel')]]").click()
                        except:
                            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                        time.sleep(1)
                    except:
                        log_message(f"Buổi {lesson_num}: Không có nút homework → skip phần này")

                    row_data = [str(class_id), "", "", lesson_num, report_link, homework_content, "OK" if not lesson_error else "ERROR"]

                    update_google_sheet(row_data, class_id, lesson_num)

                    processed[class_id]['last_lesson'] = i
                    processed_lessons.add(unique)
                    save_processed(processed)
                    break

                except StaleElementReferenceException:
                    retry += 1
                    time.sleep(2)
                except Exception as e:
                    log_message(f"Buổi {i+1} lỗi: {str(e)}")
                    lesson_error = True
                    break

            if lesson_error:
                has_error = True

        return has_error

    except Exception as e:
        log_message(f"Lớp {class_id} lỗi: {str(e)}")
        return True

def main():
    processed = {}
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r', encoding='utf-8') as f:
                processed = json.load(f)
        except:
            pass

    if 'GOOGLE_CREDENTIALS' in os.environ:
        try:
            with open(CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
                f.write(os.environ['GOOGLE_CREDENTIALS'])
        except Exception as e:
            log_message(f"Lỗi tạo credentials: {e}")
            return

    sheet_data = get_google_sheet_data()
    processed_lessons = sync_processed_with_sheet(processed, sheet_data)

    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)

    try:
        login(driver)
        count = 0
        for cid in CLASS_IDS:
            if count >= MAX_CLASSES_PER_RUN:
                break
            process_class(driver, cid, processed, processed_lessons)
            count += 1
        save_processed(processed)
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
