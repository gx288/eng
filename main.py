import json
import subprocess
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.keys import Keys
import time
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from urllib.parse import urlparse
from webdriver_manager.chrome import ChromeDriverManager

# ────────────────────────────────────────────────
#               CẤU HÌNH – CHỈNH Ở ĐÂY
# ────────────────────────────────────────────────

CLASS_IDS = [
    "10539",     # thay bằng class id thật của bạn

    # thêm bao nhiêu tùy ý
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
        return False, "Not supported Google URL"
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
        log_message(f"Lỗi đăng nhập: {e}")
        raise

def get_google_sheet_data():
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID)
        return sheet.worksheet(SHEET_NAME).get_all_values()
    except Exception as e:
        log_message(f"Không đọc được Google Sheet: {e}")
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
        log_message(f"Ghi buổi {lesson_number} lớp {class_id}")
        return True
    except Exception as e:
        log_message(f"Lỗi ghi Google Sheet {class_id} buổi {lesson_number}: {e}")
        return False

def save_processed(processed):
    try:
        with open(PROCESSED_FILE, 'w', encoding='utf-8') as f:
            json.dump(processed, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_message(f"Lỗi lưu processed.json: {e}")

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
        time.sleep(4)

        # Đếm tổng số buổi từ bảng
        lesson_rows = WebDriverWait(driver, 25).until(
            EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr"))
        )
        total_lessons = len(lesson_rows)
        log_message(f"Lớp {class_id} có {total_lessons} buổi")

        progress = processed.get(class_id, {'last_lesson': -1, 'total_lessons': 0})
        start_from = progress['last_lesson'] + 1

        processed[class_id] = {'last_lesson': progress['last_lesson'], 'total_lessons': total_lessons}

        has_error = False

        for i in range(start_from, total_lessons):
            unique = f"{class_id}:{i+1}"
            if unique in processed_lessons:
                log_message(f"Bỏ qua buổi {i+1} lớp {class_id} (đã có trong sheet)")
                processed[class_id]['last_lesson'] = i
                save_processed(processed)
                continue

            lesson_error = False
            retry = 0
            while retry < 3:
                try:
                    # Lấy lại rows vì có thể stale
                    rows = driver.find_elements(By.XPATH, "//tbody/tr")
                    row = rows[i]

                    lesson_num = row.find_element(By.XPATH, "./td[4]").text.strip()

                    report_link = "No report"
                    try:
                        btn = row.find_element(By.XPATH, ".//i[contains(@class, 'isax-card-edit')]")
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        original_win = driver.current_window_handle
                        driver.execute_script("arguments[0].click();", btn)
                        WebDriverWait(driver, 8).until(EC.number_of_windows_to_be(2))
                        new_win = next(w for w in driver.window_handles if w != original_win)
                        driver.switch_to.window(new_win)
                        report_link = driver.current_url
                        if "docs.google.com" in report_link:
                            ok, msg = check_doc_accessibility(report_link)
                            if not ok:
                                log_message(f"Report buổi {lesson_num} lỗi: {msg}")
                                lesson_error = True
                        driver.close()
                        driver.switch_to.window(original_win)
                    except Exception as e:
                        log_message(f"Không lấy được report buổi {lesson_num}: {e}")
                        lesson_error = True

                    homework_content = "No homework"
                    try:
                        btn = row.find_element(By.XPATH, ".//i[contains(@class, 'isax-book-square')]")
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        driver.execute_script("arguments[0].click();", btn)
                        popup = WebDriverWait(driver, 20).until(
                            EC.visibility_of_element_located((By.CSS_SELECTOR, ".v-dialog--active"))
                        )
                        header = popup.find_element(By.CSS_SELECTOR, ".v-toolbar__title").text.strip()
                        texts = [el.text.strip() for el in popup.find_elements(By.CSS_SELECTOR, ".text-action")]
                        links_el = popup.find_elements(By.CSS_SELECTOR, ".link-action")
                        links = [f"{el.text.strip()}: {el.get_attribute('href')}" for el in links_el]

                        for href in [l.split(': ',1)[1] for l in links if ': ' in l]:
                            if 'docs.google.com' in href or 'drive.google.com' in href:
                                ok, msg = check_doc_accessibility(href)
                                if not ok:
                                    log_message(f"Homework link lỗi buổi {lesson_num}: {msg}")
                                    lesson_error = True
                            else:
                                try:
                                    r = requests.head(href, allow_redirects=True, timeout=8)
                                    if r.status_code >= 400:
                                        log_message(f"Homework link lỗi HTTP {r.status_code} buổi {lesson_num}")
                                        lesson_error = True
                                except:
                                    lesson_error = True

                        homework_content = f"Header: {header}\n" + \
                                           (f"Text:\n" + "\n".join(texts) + "\n" if texts else "") + \
                                           (f"Links:\n" + "\n".join(links) if links else "")

                        # Đóng popup
                        try:
                            popup.find_element(By.XPATH, ".//button[.//span[contains(text(),'Cancel')]]").click()
                        except:
                            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                        time.sleep(1.5)
                    except Exception as e:
                        log_message(f"Không lấy được homework buổi {lesson_num}: {e}")
                        lesson_error = True

                    row_data = [
                        str(class_id),
                        "",               # class code - bỏ
                        "",               # course name - bỏ
                        lesson_num,
                        report_link,
                        homework_content,
                        "OK" if not lesson_error else "ERROR"
                    ]

                    if update_google_sheet(row_data, class_id, lesson_num) and subprocess.run(["git","rev-parse","--is-inside-work-tree"], capture_output=True).returncode == 0:
                        try:
                            subprocess.run(["git", "add", PROCESSED_FILE], check=True)
                            subprocess.run(["git", "commit", "-m", f"Update {class_id} lesson {lesson_num}"], check=True)
                            subprocess.run(["git", "push"], check=True)
                        except Exception as git_e:
                            log_message(f"Git push lỗi: {git_e}")

                    processed[class_id]['last_lesson'] = i
                    processed_lessons.add(unique)
                    save_processed(processed)
                    break

                except StaleElementReferenceException:
                    retry += 1
                    time.sleep(1.5)
                    if retry == 3:
                        lesson_error = True
                        log_message(f"Stale element quá 3 lần → bỏ qua buổi {i+1}")

            if lesson_error:
                has_error = True

        log_message(f"Hoàn thành lớp {class_id}")
        return has_error

    except Exception as e:
        log_message(f"Lớp {class_id} lỗi nghiêm trọng: {e}")
        return True

def main():
    processed = {}
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, encoding='utf-8') as f:
                processed = json.load(f)
        except:
            pass

    # Tạo credentials.json nếu có biến môi trường
    if 'GOOGLE_CREDENTIALS' in os.environ:
        try:
            import json
            content = os.environ['GOOGLE_CREDENTIALS'].strip()
            with open(CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            log_message(f"Không tạo được credentials.json: {e}")
            return

    sheet_data = get_google_sheet_data()
    processed_lessons = sync_processed_with_sheet(processed, sheet_data)

    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)

    try:
        login(driver)

        count = 0
        for cid in CLASS_IDS:
            if count >= MAX_CLASSES_PER_RUN:
                log_message("Đạt giới hạn số lớp mỗi lần chạy")
                break

            # Bỏ qua nếu đã xử lý hết (dựa vào total_lessons và last_lesson)
            prog = processed.get(cid, {'last_lesson': -1, 'total_lessons': 0})
            if prog['total_lessons'] > 0 and prog['last_lesson'] >= prog['total_lessons'] - 1:
                log_message(f"Lớp {cid} đã hoàn thành → bỏ qua")
                continue

            process_class(driver, cid, processed, processed_lessons)
            count += 1

        save_processed(processed)
        log_message("Hoàn tất toàn bộ")

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
