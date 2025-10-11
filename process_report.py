import json
import os
import requests
import pdfplumber
import google.generativeai as genai
from urllib.parse import parse_qs, urlparse
from telegram import Bot
import asyncio
import re

# Lấy API key và Telegram credentials từ environment (GitHub Secrets)
API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

if not API_KEY:
    raise ValueError("GEMINI_API_KEY not set in environment")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment")

genai.configure(api_key=API_KEY)

# Hàm escape ký tự cho MarkdownV2
def escape_markdown_v2(text):
    special_chars = r'([_*[\](){}~`>#+=|.!-])'
    return re.sub(special_chars, r'\\\g<1>', text)

# Hàm gửi tin nhắn Telegram bất đồng bộ
async def send_telegram_message(bot, chat_id, text):
    try:
        text = escape_markdown_v2(text)
        if len(text) > 4096:
            print(f"Message too long ({len(text)} characters), splitting...")
            parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for part in parts:
                await bot.send_message(chat_id=chat_id, text=part, parse_mode='MarkdownV2')
                await asyncio.sleep(0.5)
        else:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode='MarkdownV2')
            await asyncio.sleep(0.5)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")
        raise

# Kiểm tra danh sách model khả dụng
def get_available_model(attempt=0):
    try:
        models = genai.list_models()
        available_models = [model.name for model in models if 'generateContent' in model.supported_generation_methods]
        print("Available models:", available_models)
        for model in available_models:
            if attempt == 0 and 'gemini-2.5-flash' in model:
                return model
            if attempt == 1 and 'gemini-2.5-pro' in model:
                return model
            if attempt == 2 and 'gemini-pro' in model:
                return model
        return available_models[0] if available_models else None
    except Exception as e:
        print(f"Failed to list models: {e}")
        return None

# Sửa report_date nếu sai
def fix_report_date(date_str):
    if date_str.startswith('20225'):
        return '2025' + date_str[5:]
    return date_str

# Sửa JSON không hợp lệ
def fix_invalid_json(text):
    text = text.strip()
    text = re.sub(r'^\]\s*|\]\s*$', '', text)
    if not text.startswith('{'):
        text = '{' + text
    if not text.endswith('}'):
        text = text + '}'
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        return json.dumps({
            "new_vocabulary": {"pot": "cái nồi"},
            "sentence_structures": {},
            "report_date": "cannot find info",
            "lesson_title": "cannot find info",
            "homework": "cannot find info",
            "links": [],
            "student_comments_minh_huy": "cannot find info"
        })

# Loại bỏ markdown từ response
def clean_response_text(text):
    text = text.strip()
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
    elif text.startswith('```') and text.endswith('```'):
        text = text[3:-3].strip()
    return text

# Đọc processed2.json
with open('processed2.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

date = data.get('date', 'cannot find info')
class_name = data.get('class_name', 'cannot find info')
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
    response.raise_for_status()
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
            text = page.extract_text()
            pdf_text += text or ''
            if page.annots:
                for annot in page.annots:
                    if 'uri' in annot:
                        pdf_links.append(annot['uri'])
except Exception as e:
    print(f"Failed to extract text or links from PDF: {e}")
    os.remove(pdf_path)
    exit(1)

os.remove(pdf_path)

if not pdf_text:
    print("No text extracted from PDF. Exiting.")
    exit(1)

print(f"Extracted PDF text length: {len(pdf_text)} characters")
print(f"Extracted PDF links: {pdf_links}")

# Prompt cho Gemini API
system_prompt = """
You are an AI extractor that **must** output in strict JSON format with no extra text, comments, or markdown. The output must be a valid JSON object. Do not wrap the JSON in code blocks or add any explanation. If you cannot extract information, return "cannot find info" for strings or {} or [] for objects/arrays.
Extract from the given text:
{
  "new_vocabulary": {},  // Dictionary of new English words/phrases (key: word/phrase in lowercase, value: meaning in Vietnamese, must not be empty)
  "sentence_structures": {},  // Dictionary of question-answer pairs (key: question, value: answer or list of answers if multiple, no null values)
  "report_date": "",  // Report date in YYYY-MM-DD (if not found, use date from input JSON)
  "lesson_title": "",  // Lesson title (if not found, "cannot find info")
  "homework": "",  // Homework description with any associated links (if not found, "cannot find info")
  "links": [],  // List of all URLs found in the content (e.g., homework links, YouTube videos)
  "student_comments_minh_huy": ""  // Comments about student Minh Huy (if not found, "cannot find info")
}
For new_vocabulary, provide meanings in Vietnamese (e.g., {"pen": "cái bút"}). Every word must have a non-empty meaning. For missing meanings, use a default dictionary (e.g., "pot": "cái nồi").
For sentence_structures, map questions to answers (e.g., {"What is this?": "It’s a pen."} or {"What are they?": ["They are scissors.", "They are books."]}). If no sentence structures found, return {}.
Include all URLs (e.g., YouTube, Google Drive, Quizlet) in the links field, especially those related to homework.
Use date from input JSON if report_date is not found in text.
Ensure the output is a valid JSON object with all required fields.
"""

# Thử API tối đa 3 lần, lưu response tốt nhất
max_attempts = 3
extracted_data = None
best_response = None
for attempt in range(max_attempts):
    print(f"API attempt {attempt + 1}/{max_attempts}")
    
    model_name = get_available_model(attempt)
    if not model_name:
        print("No suitable model found. Using default response.")
        break
    
    print(f"Using model: {model_name}")
    
    try:
        model = genai.GenerativeModel(model_name, system_instruction=system_prompt)
        response = model.generate_content(pdf_text)
        cleaned_text = clean_response_text(response.text)
        cleaned_text = fix_invalid_json(cleaned_text)
        print(f"API response text (attempt {attempt + 1}): {cleaned_text}")
        extracted_data = json.loads(cleaned_text)
        extracted_data['links'] = list(set(extracted_data.get('links', []) + pdf_links))
        extracted_data['report_date'] = fix_report_date(extracted_data.get('report_date', date))
        # Điền nghĩa mặc định cho từ vựng thiếu
        for word in extracted_data['new_vocabulary']:
            if not extracted_data['new_vocabulary'][word]:
                extracted_data['new_vocabulary'][word] = {
                    "pot": "cái nồi"
                }.get(word, "nghĩa không xác định")
        # Loại bỏ null trong sentence_structures
        extracted_data['sentence_structures'] = {
            k: v for k, v in extracted_data['sentence_structures'].items()
            if v is not None and (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v)))
        }
        # Cập nhật best_response nếu response hiện tại có nhiều trường hơn
        if best_response is None or len(extracted_data.get('new_vocabulary', {})) > len(best_response.get('new_vocabulary', {})):
            best_response = extracted_data
        break  # Thoát nếu thành công
    except Exception as e:
        print(f"API attempt {attempt + 1}/{max_attempts} failed: {e}")
        if attempt == max_attempts - 1:
            print("All API attempts failed. Using best response or default.")
            extracted_data = best_response or {
                "new_vocabulary": {"pot": "cái nồi"},
                "sentence_structures": {},
                "report_date": date or "cannot find info",
                "lesson_title": "cannot find info",
                "homework": "cannot find info",
                "links": pdf_links,
                "student_comments_minh_huy": "cannot find info"
            }

# Quản lý từ vựng tổng
vocab_file = 'vocab_total.json'
if os.path.exists(vocab_file):
    try:
        with open(vocab_file, 'r', encoding='utf-8') as f:
            vocab_data = json.load(f)
            if isinstance(vocab_data, dict) and 'vocabulary' in vocab_data:
                total_vocab = vocab_data['vocabulary']
            elif isinstance(vocab_data, list) and all(isinstance(item, str) for item in vocab_data):
                total_vocab = [{"word": word, "meaning": ""} for word in vocab_data]
            else:
                total_vocab = []
    except Exception as e:
        print(f"Error reading vocab_total.json: {e}. Starting with empty vocab.")
        total_vocab = []
else:
    total_vocab = []

# Thêm từ mới
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

# Tạo thư mục Report
os.makedirs('Report', exist_ok=True)

# Tên file kết quả
title = extracted_data['lesson_title'].replace(' ', '_') if extracted_data['lesson_title'] else 'unknown'
result_filename = f"Report/{date}_{title}.json"

# Lưu file kết quả
result_data = {
    **extracted_data,
    'class_name': class_name,
    'total_vocabulary': total_vocab
}

with open(result_filename, 'w', encoding='utf-8') as f:
    json.dump(result_data, f, ensure_ascii=False, indent=4)

print(f"Processed and saved: {result_filename}")

# Gửi tin nhắn Telegram
async def send_report_to_telegram():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        general_info = (
            f"*BÁO CÁO BÀI HỌC - {result_data['report_date']}*\n"
            f"📅 *Ngày*: {result_data['report_date']}\n"
            f"📚 *Tiêu đề*: {result_data['lesson_title']}\n"
            f"🏫 *Lớp*: {result_data['class_name']}"
        )
        await send_telegram_message(bot, TELEGRAM_CHAT_ID, general_info)
        
        vocab_text = f"*TỪ VỰNG MỚI - {result_data['report_date']}*\n" + "\n".join(
            f"• `{k}`: {v}" for k, v in result_data['new_vocabulary'].items()
        )
        if result_data['new_vocabulary']:
            await send_telegram_message(bot, TELEGRAM_CHAT_ID, vocab_text)
        
        sentence_text = f"*CẤU TRÚC CÂU - {result_data['report_date']}*\n" + "\n".join(
            f"• *{k}*: {v if isinstance(v, str) else ', '.join(v)}"
            for k, v in result_data['sentence_structures'].items()
            if v is not None
        )
        if result_data['sentence_structures']:
            await send_telegram_message(bot, TELEGRAM_CHAT_ID, sentence_text)
        
        homework_text = f"*BÀI TẬP VỀ NHÀ - {result_data['report_date']}*\n{result_data['homework']}"
        if result_data['homework'] and result_data['homework'] != "cannot find info":
            await send_telegram_message(bot, TELEGRAM_CHAT_ID, homework_text)
        
        comments_text = f"*NHẬN XÉT VỀ MINH HUY - {result_data['report_date']}*\n{result_data['student_comments_minh_huy'] or 'Không có nhận xét'}"
        if result_data['student_comments_minh_huy'] and result_data['student_comments_minh_huy'] != "cannot find info":
            await send_telegram_message(bot, TELEGRAM_CHAT_ID, comments_text)
    except Exception as e:
        print(f"Failed to send Telegram messages: {e}")
        exit(1)

asyncio.run(send_report_to_telegram())
