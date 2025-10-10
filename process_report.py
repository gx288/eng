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
  "new_vocabulary": {},  // Dictionary of new English words/phrases (key: word/phrase in lowercase, value: meaning in Vietnamese or short explanation)
  "sentence_structures": {},  // Dictionary of question-answer pairs (key: question, value: answer or list of answers if multiple)
  "report_date": "",  // Report date in YYYY-MM-DD (if not found, empty string)
  "lesson_title": "",  // Lesson title (if not found, empty string)
  "homework": "",  // Homework description with any associated links (if not found, empty string)
  "links": [],  // List of all URLs found in the content (e.g., homework links, YouTube videos)
  "student_comments_minh_huy": ""  // Comments about student Minh Huy (if not found, empty string)
}
If a field has no information, use empty dictionary {}, empty list [], or empty string "".
For new_vocabulary, provide meanings in Vietnamese (e.g., {"pen": "cái bút"}).
For sentence_structures, map questions to answers (e.g., {"What is this?": "It’s a pen."} or {"What are they?": ["They are scissors.", "They are books."]}).
Include all URLs (e.g., YouTube, homework links) in the links field.
"""

# Chọn model
model_name = get_available_model()
if not model_name:
    print("No suitable model found. Exiting.")
    exit(1)

print(f"Using model: {model_name}")

# Thử gọi API với retry
max_retries = 3
for attempt in range(max_retries):
    try:
        model = genai.GenerativeModel(model_name, system_instruction=system_prompt)
        response = model.generate_content(pdf_text)
        print(f"API response text: {response.text}")
        extracted_data = json.loads(response.text)
        # Gộp links từ pdfplumber vào extracted_data['links']
        extracted_data['links'] = list(set(extracted_data.get('links', []) + pdf_links))
        break
    except Exception as e:
        print(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
        if attempt == max_retries - 1:
            print("Failed to process with Gemini API after retries. Exiting.")
            exit(1)

# Quản lý từ vựng tổng (file vocab_total.json)
vocab_file = 'vocab_total.json'
if os.path.exists(vocab_file):
    with open(vocab_file, 'r', encoding='utf-8') as f:
        total_vocab = json.load(f).get('vocabulary', [])
else:
    total_vocab = []

# Thêm từ mới (so sánh lowercase để tránh duplicate)
new_vocab = extracted_data['new_vocabulary']
new_vocab_lower = {k.lower(): v for k, v in new_vocab.items()}
total_vocab_lower = {item['word'].lower(): item['meaning'] for item in total_vocab}
added_vocab = [
    {"word": k, "meaning": v}
    for k, v in new_vocab.items()
    if k.lower() not in total_vocab_lower
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
