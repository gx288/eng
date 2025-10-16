import json
import os
from dotenv import load_dotenv  # pip install python-dotenv
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import re  # Để parse doc_id từ URL nếu cần

# Load .env và config
load_dotenv()
with open("config.json", "r") as f:
    config = json.load(f)

# Load secrets từ .env
os.environ["GEMINI_API_KEY"] = os.getenv("GEMINI_API_KEY")
credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
if credentials_json:
    credentials_info = json.loads(credentials_json)
    credentials = Credentials.from_service_account_info(credentials_info)
else:
    # Hoặc load từ file nếu không dùng .env
    credentials = Credentials.from_service_account_file(config["google_docs"]["credentials_file"])

# Setup Google Docs API
docs_service = build("docs", "v1", credentials=credentials)

# Setup Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Prompt cho Gemini (giữ nguyên từ trước)
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

def extract_text_from_doc(doc_id):
    """Trích xuất text và hyperlink từ Google Docs"""
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

def process_doc_to_json(doc_id):
    """Gửi text từ Google Docs lên Gemini và trả về JSON"""
    text, links = extract_text_from_doc(doc_id)
    
    model = genai.GenerativeModel("gemini-1.5-pro")
    response = model.generate_content([PROMPT, text])
    
    try:
        json_data = json.loads(response.text)
    except Exception as e:
        print(f"Error parsing JSON: {e}")
        return None
    
    # Thêm links vào json_data["links_all"]
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

def parse_doc_id(line):
    """Parse doc_id từ line (có thể là ID hoặc full URL)"""
    line = line.strip()
    if line.startswith("https://docs.google.com"):
        match = re.search(r'/d/([a-zA-Z0-9_-]+)', line)
        return match.group(1) if match else None
    return line  # Giả sử là doc_id trực tiếp

# Main process
os.makedirs(config["output"]["json_dir"], exist_ok=True)
all_classes = []

# Loop qua các lớp (từ config hoặc auto-detect files trong links.dir)
links_dir = config["links"]["dir"]
os.makedirs(links_dir, exist_ok=True)

for class_name in config["links"]["class_names"]:  # Hoặc os.listdir(links_dir) để auto
    links_file = os.path.join(links_dir, f"{class_name}_links.txt")
    if not os.path.exists(links_file):
        print(f"File links not found for {class_name}: {links_file}")
        continue
    
    # Xử lý nội dung file links: Mỗi link 1 hàng
    with open(links_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    doc_ids = [parse_doc_id(line) for line in lines if parse_doc_id(line)]
    
    class_data = {
        "class_name": class_name.replace("-", " "),  // Chuyển dấu gạch về khoảng trắng nếu cần
        "lessons": []
    }
    
    for doc_id in doc_ids:
        json_data = process_doc_to_json(doc_id)
        if json_data:
            class_data["lessons"].append(json_data)
    
    # Lưu JSON cho lớp
    output_path = os.path.join(config["output"]["json_dir"], f"{class_name}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(class_data, f, ensure_ascii=False, indent=4)
    
    all_classes.append(class_data)
    print(f"Processed and saved {class_name}.json")

# Optional: Lưu tổng hợp tất cả lớp
all_classes_path = config["output"]["all_classes_file"]
with open(all_classes_path, "w", encoding="utf-8") as f:
    json.dump(all_classes, f, ensure_ascii=False, indent=4)
print(f"Saved all classes to {all_classes_path}")
