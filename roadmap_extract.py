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

# Set up Selenium WebDriver
options = webdriver.ChromeOptions()
# options.add_argument('--headless')  # Uncomment for GitHub Actions
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

try:
    # Check for credentials.json locally or from environment (for GitHub Actions)
    credentials_file = "credentials.json"
    if not os.path.exists(credentials_file):
        # Check if running in GitHub Actions with GOOGLE_CREDENTIALS secret
        github_credentials = os.getenv("GOOGLE_CREDENTIALS")
        if github_credentials:
            print("Using GOOGLE_CREDENTIALS from environment (GitHub Actions)")
            with open(credentials_file, "w", encoding="utf-8") as f:
                json.dump(json.loads(github_credentials), f)
        else:
            raise FileNotFoundError(
                f"Google Sheets credentials file '{credentials_file}' not found. "
                "Place it in 'D:\\Files\\Desktop\\Python Project\\selenium\\cec\\' or set GOOGLE_CREDENTIALS environment variable."
            )

    # Login
    print("Navigating to login page...")
    driver.get("https://apps.cec.com.vn/login")

    print("Waiting for username field...")
    username_field = WebDriverWait(driver, 20).until(
        EC.visibility_of_element_located((By.ID, "input-14"))
    )
    print("Entering username...")
    username_field.send_keys("61655HN")

    print("Waiting for password field...")
    password_field = WebDriverWait(driver, 20).until(
        EC.visibility_of_element_located((By.ID, "input-18"))
    )
    print("Entering password...")
    password_field.send_keys("1234567")

    print("Locating login button...")
    login_button = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
    )
    print("Clicking login button...")
    login_button.click()

    print("Waiting for page to load after login...")
    WebDriverWait(driver, 30).until_not(
        EC.url_contains("login")
    )
    print(f"Current URL after login: {driver.current_url}")

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
    print("Writing to text file...")
    with open("student_roadmap.txt", "w", encoding="utf-8") as f:
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

    # Set up Google Sheets API
    print("Setting up Google Sheets API...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_file, scope)
    client = gspread.authorize(creds)

    # Open the Google Sheet
    sheet_id = "YOUR_SHEET_ID"  # Replace with actual Sheet ID
    try:
        sheet = client.open_by_key(sheet_id).sheet1
    except gspread.exceptions.SpreadsheetNotFound:
        raise Exception(f"Google Sheet with ID '{sheet_id}' not found or not accessible.")

    # Clear existing content
    sheet.clear()

    # Write headers
    headers = ["Username", "Học sinh", "Mã học sinh", "DOB", "Start date", "Trường học", "Nhân viên tư vấn",
               "Date", "Note", "Course Name", "Level", "Duration", "Final Commitment"]
    sheet.append_row(headers)

    # Write data
    print("Uploading to Google Sheet...")
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

    print("Data successfully uploaded to Google Sheet.")

except Exception as e:
    print(f"Error occurred: {str(e)}")
    print(f"Current URL: {driver.current_url}")
    print(f"Page source at failure:\n{driver.page_source[:1000]}...")
    driver.save_screenshot("error_screenshot.png")
    raise

finally:
    print("Closing browser...")
    driver.quit()
