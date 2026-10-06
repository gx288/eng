import json
import os
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import parse_qs, urlparse
import pdfplumber

# Config
PROCESSED_FILE = "processed2.json"
LOG_FILE = "class_info_log2.txt"

def log_message(message):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")

def login(driver):
    driver.get("https://apps.cec.com.vn/login")
    username = os.getenv("NEW_CEC_USER")
    password = os.getenv("NEW_CEC_PASS")
    if not username or not password:
        log_message("Missing NEW_CEC_USER or NEW_CEC_PASS")
        raise Exception("Missing credentials")
    try:
        username_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "input-14"))
        )
        driver.execute_script("arguments[0].value = '';", username_field)
        username_field.send_keys(username)
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
            log_message("Login failed")
            raise Exception("Login failed")
        log_message("Login successful")
    except Exception as e:
        log_message(f"Login error: {str(e)}")
        raise

def main():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")
    driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)

    try:
        login(driver)
        driver.get("https://apps.cec.com.vn/student-calendar/overview")
        time.sleep(5)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        class_events = WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'v-event') and @data-date]"))
        )
        log_message(f"Found {len(class_events)} class events")

        latest_date = None
        latest_event = None
        for event in class_events:
            date_str = event.get_attribute("data-date")
            try:
                event_date = datetime.strptime(date_str, "%Y-%m-%d")
                if event_date.date() < datetime.now().date() and (latest_date is None or event_date > latest_date):
                    latest_date = event_date
                    latest_event = event
            except Exception as e:
                log_message(f"Error parsing date {date_str}: {str(e)}")
                continue

        if not latest_date:
            log_message("No classes found before today")
            return

        date_str = latest_date.strftime("%Y-%m-%d")
        driver.execute_script("arguments[0].click();", latest_event)
        time.sleep(3)

        popup = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.XPATH, "//div[contains(@class, 'v-menu__content') and contains(@class, 'menuable__content__active')]"))
        )
        class_name = popup.find_element(By.CLASS_NAME, "v-toolbar__title").text.strip().split(" : ")[-1]

        report_button = popup.find_element(By.XPATH, "//button[.//p[text()='Báo cáo bài học']]")
        if "v-btn--disabled" not in report_button.get_attribute("class"):
            original_window = driver.current_window_handle
            report_button.click()
            time.sleep(3)
            for window_handle in driver.window_handles:
                if window_handle != original_window:
                    driver.switch_to.window(window_handle)
                    break
            report_url = driver.current_url
            driver.close()
            driver.switch_to.window(original_window)

            parsed_url = urlparse(report_url)
            query_params = parse_qs(parsed_url.query)
            pdf_url = query_params.get('url', [None])[0]
            if pdf_url:
                pdf_response = requests.get(pdf_url)
                pdf_path = "temp_report.pdf"
                with open(pdf_path, "wb") as f:
                    f.write(pdf_response.content)
                with pdfplumber.open(pdf_path) as pdf:
                    text = "".join(page.extract_text() or "" for page in pdf.pages)
                    links = [annot["uri"] for page in pdf.pages for annot in (page.annots or []) if "uri" in annot]
                os.remove(pdf_path)

                data = {
                    "date": date_str,
                    "class_name": class_name,
                    "report_url": report_url,
                    "doc_content": text,
                    "doc_links": links
                }
                with open(f"Report/{date_str}_report.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
                    json.dump({"date": date_str, "class_name": class_name, "report_url": report_url}, f, indent=2)
        else:
            log_message("No report available")
    except Exception as e:
        log_message(f"Error: {str(e)}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
