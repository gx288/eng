import json
import os
import requests
import pdfplumber
import google.generativeai as genai
from urllib.parse import parse_qs, urlparse

# Lấy API key từ environment (GitHub Secrets)
API_KEY = os.getenv('GEMINI_API_KEY')
if not API_KEY:
    raise ValueError("GEMINI_API_KEY not set in environment")

genai.configure(api_key=API_KEY)

# Kiểm tra danh sách model khả dụng
def get_available_model():
    try:
        models = genai.list_models()
        available_models = [model.name for model in models if 'generateContent' in model.supported_generation_methods]
        print("Available models:", available_models)
        # Ưu tiên gemini-2.5-flash, nếu không thì gemini-pro, hoặc model đầu tiên
        for model in available_models:
            if 'gemini-2.5-flash' in model:
                return model
            if 'gemini-pro' in model:
                return model
        return available_models[0] if available_models else None
    except Exception as e:
        print(f"Failed to list models: {e}")
        return None

# Kiểm tra API response có đầy đủ không
def is_valid_response(extracted_data):
    required_fields = ['new_vocabulary', 'sentence_structures', 'report_date', 'lesson_title', 'homework', 'links', 'student_comments_minh_huy']
    if not all(field in extracted_data for field in required_fields):
        return False
    # Kiểm tra new_vocabulary: không có từ nào thiếu nghĩa
    if not extracted_data['new_vocabulary'] or any(not meaning for meaning in extracted_data['new_vocabulary'].values()):
        return False
    # Kiểm tra sentence_structures: không rỗng
    if not extracted_data['sentence_structures']:
        return False
    return True

# Đọc processed2.json
with open('processed2.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

date = data.get('date', '')
class_name = data.get('class_name', '')
report_url = data.get('report_url', '')

if not report_url:
    print("No report_url found in JSON. Exiting.")
    exit(1)

# Xử lý Google Docs viewer URL
parsed_url = urlparse(report_url)
query_params = parse_qs(parsed_url.query)
direct_pdf_url = query_params.get('url', [None])[0]

if not direct_pdf_url:
    print("Could not extract direct PDF URL from Google Docs viewer. Exiting.")
    exit(1)

# Tải PDF từ URL trực tiếp
pdf_path = 'temp_report.pdf'
try:
    response = requests.get(direct_pdf_url, timeout=10)
    response.raise_for_status()  # Kiểm tra lỗi HTTP
    content_type = response.headers.get('content-type', '')
    if 'application/pdf' not in content_type:
        print(f"Downloaded file is not a PDF (Content-Type: {content_type}). Exiting.")
        exit(1)
    with open(pdf_path, 'wb') as f:
        f.write(response.content)
except requests.RequestException as e:
    print(f"Failed to download PDF: {e}")
    exit(1)

# Extract text và links từ PDF
pdf_text = ''
pdf_links = []
try:
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Extract text
            text = page.extract_text()
            pdf_text += text or ''
            # Extract hyperlinks từ annotations
            if page.annots:
                for annot in page.annots:
                    if 'uri' in annot:
                        pdf_links.append(annot['uri'])
except Exception as e:
    print(f"Failed to extract text or links from PDF: {e}")
    os.remove(pdf_path)
    exit(1)

os.remove(pdf_path)  # Xóa file tạm

if not pdf_text:
    print("No text extracted from PDF. Exiting.")
    exit(1)

print(f"Extracted PDF text length: {len(pdf_text)} characters")
print(f"Extracted PDF links: {pdf_links}")

# Prompt cho Gemini API để extract đúng cấu trúc JSON
system_prompt = """
You are an AI extractor that **must** output in strict JSON format with no extra text, comments, or markdown. The output must be a valid JSON object. Do not wrap the JSON in code blocks or add any explanation. If you cannot extract information, return empty values as specified.
Extract from the given text:
{
  "new_vocabulary": {},  // Dictionary of new English words/phrases (key: word/phrase in lowercase, value: meaning in Vietnamese, must not be empty)
  "sentence_structures": {},  // Dictionary of question-answer pairs (key: question, value: answer or list of answers if multiple)
  "report_date": "",  // Report date in YYYY-MM-DD (if not found, empty string)
  "lesson_title": "",  // Lesson title (if not found, empty string)
  "homework": "",  // Homework description with any associated links (if not found, empty string)
  "links": [],  // List of all URLs found in the content (e.g., homework links, YouTube videos)
  "student_comments_minh_huy": ""  // Comments about student Minh Huy (if not found, empty string)
}
For new_vocabulary, provide meanings in Vietnamese (e.g., {"pen": "cái bút"}). Every word must have a non-empty meaning.
For sentence_structures, map questions to answers (e.g., {"What is this?": "It’s a pen."} or {"What are they?": ["They are scissors.", "They are books."]}).
Include all URLs (e.g., YouTube, Google Drive, Quizlet) in the links field, especially those related to homework.
"""

# Chọn model
model_name = get_available_model()
if not model_name:
    print("No suitable model found. Exiting.")
    exit(1)

print(f"Using model: {model_name}")

# Lặp lại toàn bộ script tối đa 3 lần
max_script_retries = 3
for script_attempt in range(max_script_retries):
    print(f"Script attempt {script_attempt + 1}/{max_script_retries}")
    
    # Thử gọi API với retry nội bộ
    max_api_retries = 3
    extracted_data = None
    for api_attempt in range(max_api_retries):
        try:
            model = genai.GenerativeModel(model_name, system_instruction=system_prompt)
            response = model.generate_content(pdf_text)
            print(f"API response text (attempt {api_attempt + 1}): {response.text}")
            extracted_data = json.loads(response.text)
            # Gộp links từ pdfplumber vào extracted_data['links']
            extracted_data['links'] = list(set(extracted_data.get('links', []) + pdf_links))
            if is_valid_response(extracted_data):
                break
            else:
                print(f"API response invalid (attempt {api_attempt + 1}): missing fields or empty meanings")
        except Exception as e:
            print(f"API attempt {api_attempt + 1}/{max_api_retries} failed: {e}")
        if api_attempt == max_api_retries - 1 and not extracted_data:
            print("Failed to process with Gemini API after retries. Exiting.")
            exit(1)
    
    if extracted_data and is_valid_response(extracted_data):
        break
    elif script_attempt == max_script_retries - 1:
        print("Failed to get valid API response after all script retries. Using last response.")
        if not extracted_data:
            print("No valid response obtained. Exiting.")
            exit(1)

# Quản lý từ vựng tổng (file vocab_total.json)
vocab_file = 'vocab_total.json'
if os.path.exists(vocab_file):
    try:
        with open(vocab_file, 'r', encoding='utf-8') as f:
            vocab_data = json.load(f)
            # Kiểm tra định dạng vocab_total.json
            if isinstance(vocab_data, dict) and 'vocabulary' in vocab_data:
                total_vocab = vocab_data['vocabulary']
            elif isinstance(vocab_data, list) and all(isinstance(item, str) for item in vocab_data):
                # Chuyển từ danh sách chuỗi sang danh sách dictionary với meaning rỗng
                total_vocab = [{"word": word, "meaning": ""} for word in vocab_data]
            else:
                total_vocab = []
    except Exception as e:
        print(f"Error reading vocab_total.json: {e}. Starting with empty vocab.")
        total_vocab = []
else:
    total_vocab = []

# Thêm từ mới (so sánh lowercase để tránh duplicate)
new_vocab = extracted_data['new_vocabulary']
new_vocab_lower = {k.lower(): v for k, v in new_vocab.items()}
total_vocab_lower = {item['word'].lower(): item['meaning'] for item in total_vocab if isinstance(item, dict)}
added_vocab = [
    {"word": k, "meaning": v}
    for k, v in new_vocab.items()
    if k.lower() not in total_vocab_lower or total_vocab_lower.get(k.lower(), '') == ''
]
total_vocab.extend(added_vocab)

# Lưu vocab_total.json
with open(vocab_file, 'w', encoding='utf-8') as f:
    json.dump({'vocabulary': total_vocab}, f, ensure_ascii=False, indent=4)

# Tạo thư mục Report nếu chưa có
os.makedirs('Report', exist_ok=True)

# Tên file kết quả: date + lesson_title (thay space bằng _)
title = extracted_data['lesson_title'].replace(' ', '_') if extracted_data['lesson_title'] else 'unknown'
result_filename = f"Report/{date}_{title}.json"

# Nội dung file kết quả: Toàn bộ extracted + class_name + total_vocab
result_data = {
    **extracted_data,
    'class_name': class_name,
    'total_vocabulary': total_vocab  # Danh sách từ vựng tổng
}

with open(result_filename, 'w', encoding='utf-8') as f:
    json.dump(result_data, f, ensure_ascii=False, indent=4)

print(f"Processed and saved: {result_filename}")
