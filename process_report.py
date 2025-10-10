import json
import os
import requests
import pdfplumber
import google.generativeai as genai

# Lấy API key từ environment (GitHub Secrets)
API_KEY = os.getenv('GEMINI_API_KEY')
if not API_KEY:
    raise ValueError("GEMINI_API_KEY not set in environment")

genai.configure(api_key=API_KEY)

# Đọc processed2.json
with open('processed2.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

date = data.get('date', '')
class_name = data.get('class_name', '')
report_url = data.get('report_url', '')

if not report_url:
    print("No report_url found in JSON. Exiting.")
    exit(0)

# Tải PDF từ URL (lưu tạm)
pdf_path = 'temp_report.pdf'
response = requests.get(report_url)
with open(pdf_path, 'wb') as f:
    f.write(response.content)

# Extract text từ PDF
pdf_text = ''
with pdfplumber.open(pdf_path) as pdf:
    for page in pdf.pages:
        pdf_text += page.extract_text() or ''

os.remove(pdf_path)  # Xóa file tạm

if not pdf_text:
    print("No text extracted from PDF. Exiting.")
    exit(0)

# Prompt cho Gemini API để extract đúng cấu trúc JSON
system_prompt = """
You are an extractor that always outputs in strict JSON format. Do not add any extra text outside the JSON.
Extract from the given text:
{
  "new_vocabulary": []  // List of new English words/phrases (unique, lowercase, no duplicates)
  "sentence_structures": []  // List of full English sentences from the content
  "report_date": ""  // Report date in YYYY-MM-DD (if not found, empty string)
  "lesson_title": ""  // Lesson title (if not found, empty string)
  "homework": ""  // Homework description (if not found, empty string)
}
If a field has no information, use empty list [] or empty string "".
"""

model = genai.GenerativeModel('gemini-1.5-flash', system_instruction=system_prompt)
response = model.generate_content(pdf_text)
extracted_data = json.loads(response.text)  # Giả sử output là JSON sạch

# Quản lý từ vựng tổng (file vocab_total.json)
vocab_file = 'vocab_total.json'
if os.path.exists(vocab_file):
    with open(vocab_file, 'r', encoding='utf-8') as f:
        total_vocab = json.load(f).get('vocabulary', [])
else:
    total_vocab = []

# Thêm từ mới (so sánh lowercase để tránh duplicate)
new_vocab = extracted_data['new_vocabulary']
new_vocab_lower = [v.lower() for v in new_vocab]
total_vocab_lower = [v.lower() for v in total_vocab]
added_vocab = [v for v in new_vocab if v.lower() not in total_vocab_lower]
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
