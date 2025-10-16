import json
import os
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time
from webdriver_manager.chrome import ChromeDriverManager

# Load .env và config
load_dotenv()
with open("config.json", "r") as f:
    config = json.load(f)

# Load secrets
gemini_api_key = os.getenv("GEMINI_API_KEY")
credentials_json = os.getenv("GOOGLE_CREDENTIALS")
cec_username = os.getenv("NEW_CEC_USER")
cec_password = os.getenv("NEW_CEC_PASS")

if not all([gemini_api_key, credentials_json, cec_username, cec_password]):
    raise ValueError("Missing required environment variables")

credentials_info = json.loads(credentials_json)
credentials = Credentials.from_service_account_info(credentials_info)

# Setup Google Docs API
docs_service = build("docs", "v1", credentials=credentials)

# Setup Gemini API
genai.configure(api_key=gemini_api_key)

# Setup Selenium
options = webdriver.ChromeOptions()
options.add_argument("--headless")
options.add_argument("--disable-gpu")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")
options.add_argument("--disable-autofill")
driver = webdriver.Chrome(service=webdriver.chrome.service.Service(ChromeDriverManager().install()), options=options)

# Prompt cho Gemini
PROMPT = """
Extract structured data from the provided text (from a Google Docs lesson plan). Ignore the "Comments" section entirely to avoid storing personal student information. Output a JSON object with the following fields:
- class_name: String, e.g., "Kindergarten 1 - KG1R".
- lesson_unit: String, e.g., "Unit 33 (1st)".
- lesson_date: String, format YYYY-MM-DD, e.g., "2025-06-24".
- learning_objectives: Object with subfields:
  - vocabulary_review: Object { theme: String, words: Array of {english: String, vietnamese: String}, count: Integer }.
  - vocabulary_new: Object { theme: String, words: Array of {english: String, vietnamese: String}, count: Integer }.
  - pronunciation: Object { sound: String, words: Array of {english: String, vietnamese: String}, count: Integer }.
  - phonics: String, e.g., "Distinguish letter Z and letter sound /z/".
- warm_up: Object { description: String, videos: Array of {title: String, url: String} }.
- homework_check: String, summarize without mentioning specific student names.
- running_content: Object { theme: String, review_vocabulary: { link: String, words: Array of {english: String, vietnamese: String}, structure: String, examples: Array of Strings, activities: String } }.
- new_vocabulary: Object { theme: String, words: Array of {english: String, vietnamese: String}, link: String, activities: String }.
- phonics: Object { letter: String, words: Array of {english: String, vietnamese: String}, link: String, activities: String, videos: Array of {title: String, url: String} }.
- homework: Array of Strings.
- links_all: Array of {context: String, url: String, type: String ("youtube" for youtube.com, "quizlet" for quizlet.com, else "other")}.

For hyperlinks, extract URLs and their associated text (e.g., video titles) from the provided text. Ensure all text is preserved accurately, including Vietnamese translations. Return only the JSON output, no additional text.
"""

def log_message(message):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")
    with open("extract_lessons_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")

def login_cec(driver):
    driver.get("https://apps.cec.com.vn/login")
    try:
        username_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "input-14"))
        )
        driver.execute_script("arguments[0].value = '';", username_field)
        username_field.send_keys(cec_username)
        password_field = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "input-18"))
        )
        driver.execute_script("arguments[0].value = '';", password_field)
        password_field.send_keys(cec_password)
        login_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
        )
        login_button.click()
        time.sleep(5)
        if "login" in driver.current_url:
            log_message("Lỗi đăng nhập: Vẫn ở trang login")
            raise Exception("Login failed")
        log_message("Đăng nhập thành công vào CEC")
    except Exception as e:
        log_message(f"Lỗi đăng nhập CEC: {str(e)}")
        raise

def extract_text_from_doc(doc_id):
    try:
        doc = docs_service.documents().get(documentId=doc_id).execute()
        content = doc.get("body").get("content")
        text = []
        links = []
        for element in content:
            if "paragraph" in element:
                for elem in element["paragraph"]["elements"]:
                    if "textRun" in elem:
                        text_content = elem["textRun"]["content"]
                        text.append(text_content)
                        if "textStyle" in elem["textRun"] and "link" in elem["textRun"]["textStyle"]:
                            url = elem["textRun"]["textStyle"]["link"].get("url", "")
                            links.append({"context": text_content.strip(), "url": url})
        full_text = "".join(text)
        return full_text, links
    except Exception as e:
        log_message(f"Error extracting Google Doc {doc_id}: {str(e)}")
        return None, []

def parse_doc_id(url):
    if url and url.startswith("https://docs.google.com"):
        match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
        return match.group(1) if match else None
    return None

def process_doc_to_json(doc_id, class_name, lesson_number):
    if not doc_id:
        return None
    text, links = extract_text_from_doc(doc_id)
    if not text:
        return None
    model = genai.GenerativeModel("gemini-1.5-pro")
    response = model.generate_content([PROMPT, text])
    try:
        json_data = json.loads(response.text)
        json_data["lesson_number"] = lesson_number
        json_data["class_name"] = class_name
        if "links_all" not in json_data:
            json_data["links_all"] = []
        for link in links:
            link_type = "youtube" if "youtube.com" in link["url"] else "quizlet" if "quizlet.com" in link["url"] else "other"
            json_data["links_all"].append({
                "context": link["context"],
                "url": link["url"],
                "type": link_type
            })
        return json_data
    except Exception as e:
        log_message(f"Error parsing JSON for doc {doc_id}: {str(e)}")
        return None

def process_class(class_info):
    class_id = class_info["class_id"]
    
    # Selenium: Lấy report_link, homework_content và class_name
    driver.get(f"https://apps.cec.com.vn/student-calendar/class-detail?classID={class_id}")
    log_message(f"Processing Class ID {class_id}")
    time.sleep(5)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    
    try:
        class_code_element = WebDriverWait(driver, 40).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h2.d-flex div"))
        )
        class_name = class_code_element.text.strip()
        
        lesson_rows = WebDriverWait(driver, 20).until(
            EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr"))
        )
        
        class_data = {
            "class_name": class_name,
            "class_id": class_id,
            "lessons": []
        }
        
        for lesson_index, row in enumerate(lesson_rows):
            try:
                lesson_number = row.find_element(By.XPATH, "./td[4]").text.strip()
                report_link = "No report available"
                doc_id = None
                try:
                    report_button = row.find_element(By.XPATH, ".//i[@data-v-50ef298c and contains(@class, 'isax-card-edit')]")
                    driver.execute_script("arguments[0].scrollIntoView(true);", report_button)
                    original_window = driver.current_window_handle
                    driver.execute_script("arguments[0].click();", report_button)
                    WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
                    new_window = [window for window in driver.window_handles if window != original_window][0]
                    driver.switch_to.window(new_window)
                    report_link = driver.current_url
                    doc_id = parse_doc_id(report_link)
                    driver.close()
                    driver.switch_to.window(original_window)
                except:
                    log_message(f"No report link for lesson {lesson_number} in Class ID {class_id}")
                
                homework_content = "No homework available"
                try:
                    homework_button = row.find_element(By.XPATH, ".//i[@data-v-50ef298c and contains(@class, 'isax-book-square')]")
                    driver.execute_script("arguments[0].scrollIntoView(true);", homework_button)
                    driver.execute_script("arguments[0].click();", homework_button)
                    popup = WebDriverWait(driver, 30).until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".v-dialog--active")))
                    header = popup.find_element(By.CSS_SELECTOR, ".v-toolbar__title").text.strip()
                    text_actions = [elem.text.strip() for elem in popup.find_elements(By.CSS_SELECTOR, ".text-action")]
                    link_actions = popup.find_elements(By.CSS_SELECTOR, ".link-action")
                    homework_links = [f"{link.text.strip()}: {link.get_attribute('href')}" for link in link_actions]
                    homework_content = f"Homework Header: {header}\n" + ("Text:\n" + "\n".join(text_actions) + "\n" if text_actions else "") + ("Links:\n" + "\n".join(homework_links) if homework_links else "")
                    try:
                        popup.find_element(By.XPATH, ".//button[.//span[contains(text(), 'Cancel')]]").click()
                    except:
                        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                    time.sleep(2)
                except:
                    log_message(f"No homework content for lesson {lesson_number} in Class ID {class_id}")
                
                # Process Google Doc
                json_data = process_doc_to_json(doc_id, class_name, lesson_number)
                if json_data:
                    json_data["report_link"] = report_link
                    json_data["homework_content"] = homework_content
                    class_data["lessons"].append(json_data)
                else:
                    log_message(f"Failed to process doc {doc_id} for lesson {lesson_number}")
            
            except Exception as e:
                log_message(f"Error processing lesson {lesson_index + 1} for Class ID {class_id}: {str(e)}")
                continue
        
        # Lưu JSON cho lớp
        os.makedirs(config["output"]["dir"], exist_ok=True)
        output_path = os.path.join(config["output"]["dir"], f"{class_id}.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(class_data, f, ensure_ascii=False, indent=4)
        log_message(f"Saved {class_id}.json")
        
        return class_data
    
    except Exception as e:
        log_message(f"Error processing Class ID {class_id}: {str(e)}")
        return None

def main():
    try:
        login_cec(driver)
        all_classes = []
        for class_info in config["class_info"]:
            class_data = process_class(class_info)
            if class_data:
                all_classes.append(class_data)
        
        # Lưu tổng hợp
        all_classes_path = config["output"]["all_classes_file"]
        with open(all_classes_path, "w", encoding="utf-8") as f:
            json.dump(all_classes, f, ensure_ascii=False, indent=4)
        log_message(f"Saved all classes to {all_classes_path}")
    
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
