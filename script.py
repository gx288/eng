from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# Set up Selenium WebDriver
options = webdriver.ChromeOptions()
options.add_argument('--headless')  # Required for GitHub Actions
options.add_argument('--no-sandbox')  # Required for Linux environments
options.add_argument('--disable-dev-shm-usage')  # Avoids issues in Dockerized environments
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

try:
    # Navigate to the login page
    driver.get("https://apps.cec.com.vn/login")

    # Wait for the username field to be present
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "input-66")))

    # Enter username
    username_field = driver.find_element(By.ID, "input-66")
    username_field.send_keys("61655HN")

    # Enter password
    password_field = driver.find_element(By.ID, "input-70")
    password_field.send_keys("1234567")

    # Find and click the login button (adjust XPath if needed)
    login_button = driver.find_element(By.XPATH, "//button[@type='submit']")
    login_button.click()

    # Wait for the page to load after login
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    # Print HTML content after login
    html_content = driver.page_source
    print("HTML content after login:")
    print(html_content)

except Exception as e:
    print(f"An error occurred: {e}")
    raise  # Raise the error to fail the GitHub Action if something goes wrong

finally:
    driver.quit()
