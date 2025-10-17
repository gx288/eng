import json
import os
import requests
import pdfplumber
import google.generativeai as genai
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import time
import random

# Configuration
LOG_FILE = "class_info_log3.txt"
HOMEWORK_FILE = "homework.json"
LINK_FILE = "link.txt"
API_KEY = os.getenv("GEMINI_API_KEY")
TODAY = datetime.now(tz=ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d")
MAX_PAGES = 10  # Limit PDF processing to 10 pages

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Suppress verbose pdfplumber logs
logging.getLogger('pdfplumber').setLevel(logging.ERROR)

# System prompt for Gemini API
SYSTEM_PROMPT = """
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

# Clean and validate JSON response
def clean_json_response(text):
    text = text.strip()
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
    elif text.startswith('```') and text.endswith('```'):
        text = text[3:-3].strip()
    try:
        parsed_json = json.loads(text)
        logger.info("Parsed JSON response")
        return parsed_json
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON response: {str(e)}")
        return {
            "class_name": "cannot find info",
            "lesson_unit": "cannot find info",
            "lesson_date": TODAY,
            "learning_objectives": {
                "vocabulary_review": {"theme": "", "words": [], "count": 0},
                "vocabulary_new": {"theme": "", "words": [], "count": 0},
                "pronunciation": {"sound": "", "words": [], "count": 0},
                "phonics": ""
            },
            "warm_up": {"description": "", "videos": []},
            "homework_check": "cannot find info",
            "running_content": {"theme": "", "review_vocabulary": {"link": "", "words": [], "structure": "", "examples": [], "activities": ""}},
            "new_vocabulary": {"theme": "", "words": [], "link": "", "activities": ""},
            "phonics": {"letter": "", "words": [], "link": "", "activities": "", "videos": []},
            "homework": [],
            "links_all": []
        }

# Get available Gemini model
def get_gemini_model(attempt=0):
    try:
        models = genai.list_models()
        available_models = [model.name for model in models if 'generateContent' in model.supported_generation_methods]
        model_priority = [
            'gemini-2.5-flash',
            'gemini-2.0-flash-lite',
            'gemini-2.5-pro',
            'gemini-pro'
        ]
        for priority in model_priority[attempt:]:
            for model in available_models:
                if priority in model:
                    logger.info(f"Selected model: {model}")
                    return model
        return available_models[0] if available_models else None
    except Exception as e:
        logger.error(f"Failed to list models: {str(e)}")
        return None

# Clean Google Docs URL and convert to PDF export URL
def clean_google_docs_url(url):
    logger.info(f"Processing URL: {url}")
    match = re.match(r"https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)(?:/edit.*|$|/?\?.*|$)", url)
    if not match:
        logger.error(f"Invalid Google Docs URL format: {url}")
        return None
    doc_id = match.group(1)
    pdf_url = f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"
    logger.info(f"PDF export URL: {pdf_url}")
    return pdf_url

# Process a single report link
def process_report_link(report_url):
    logger.info(f"Processing report: {report_url}")
    
    # Clean and convert URL to PDF export
    direct_pdf_url = clean_google_docs_url(report_url)
    if not direct_pdf_url:
        logger.error("Failed to generate PDF URL, skipping")
        return None

    # Download PDF from direct URL
    pdf_path = 'temp_report.pdf'
    logger.info(f"Downloading PDF to {pdf_path}")
    try:
        response = requests.get(direct_pdf_url, timeout=10)
        response.raise_for_status()
        content_type = response.headers.get('content-type', '')
        if 'application/pdf' not in content_type:
            logger.error(f"Downloaded file is not a PDF (Content-Type: {content_type})")
            return None
        with open(pdf_path, 'wb') as f:
            f.write(response.content)
        logger.info(f"Downloaded PDF to {pdf_path}")
    except requests.RequestException as e:
        logger.error(f"Failed to download PDF: {str(e)}")
        return None

    # Extract text and links from PDF
    pdf_text = ''
    pdf_links = []
    logger.info("Extracting text and links from PDF")
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages[:MAX_PAGES]):
                try:
                    text = page.extract_text()
                    pdf_text += text or ''
                    if page.annots:
                        for annot in page.annots:
                            if 'uri' in annot:
                                pdf_links.append(annot['uri'])
                except Exception as e:
                    logger.error(f"Failed to process page {i + 1}: {str(e)}")
                    continue
        logger.info(f"Extracted {len(pdf_text)} characters and {len(pdf_links)} links from PDF")
    except Exception as e:
        logger.error(f"Failed to process PDF: {str(e)}")
        return None
    finally:
        if os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
                logger.info(f"Deleted PDF file: {pdf_path}")
            except Exception as e:
                logger.error(f"Failed to delete PDF file: {str(e)}")

    if not pdf_text:
        logger.error("No text extracted from PDF")
        return None

    # Call Gemini API with retry on quota errors
    max_attempts = 3
    extracted_data = None
    for attempt in range(max_attempts):
        logger.info(f"Gemini API attempt {attempt + 1}/{max_attempts}")
        model_name = get_gemini_model(attempt)
        if not model_name:
            logger.error("No suitable model found")
            continue
        try:
            model = genai.GenerativeModel(model_name, system_instruction=SYSTEM_PROMPT)
            response = model.generate_content(pdf_text)
            cleaned_text = clean_json_response(response.text)
            extracted_data = json.loads(cleaned_text)
            extracted_data['links_all'] = list(set(extracted_data.get('links_all', []) + [{"context": "PDF link", "url": link, "type": "other"} for link in pdf_links]))
            extracted_data['report_url'] = report_url
            extracted_data['pdf_export_url'] = direct_pdf_url
            logger.info(f"API attempt {attempt + 1} successful")
            break
        except Exception as e:
            if '429' in str(e):
                if attempt < max_attempts - 1:
                    logger.warning(f"Quota exceeded for {model_name}, trying next model")
                    continue
                retry_delay = 40 + random.uniform(0, 5)
                logger.warning(f"Quota exceeded, retrying in {retry_delay:.2f} seconds")
                time.sleep(retry_delay)
            else:
                logger.error(f"API attempt {attempt + 1} failed: {str(e)}")
            if attempt == max_attempts - 1:
                logger.error("All API attempts failed, using default response")
                extracted_data = {
                    "class_name": "cannot find info",
                    "lesson_unit": "cannot find info",
                    "lesson_date": TODAY,
                    "learning_objectives": {
                        "vocabulary_review": {"theme": "", "words": [], "count": 0},
                        "vocabulary_new": {"theme": "", "words": [], "count": 0},
                        "pronunciation": {"sound": "", "words": [], "count": 0},
                        "phonics": ""
                    },
                    "warm_up": {"description": "", "videos": []},
                    "homework_check": "cannot find info",
                    "running_content": {"theme": "", "review_vocabulary": {"link": "", "words": [], "structure": "", "examples": [], "activities": ""}},
                    "new_vocabulary": {"theme": "", "words": [], "link": "", "activities": ""},
                    "phonics": {"letter": "", "words": [], "link": "", "activities": "", "videos": []},
                    "homework": [],
                    "links_all": [{"context": "PDF link", "url": link, "type": "other"} for link in pdf_links],
                    "report_url": report_url,
                    "pdf_export_url": direct_pdf_url
                }

    logger.info(f"Completed processing for {report_url}")
    return extracted_data

# Main function
def main():
    logger.info("Starting report extraction")
    if not API_KEY:
        logger.error("Missing GEMINI_API_KEY, aborting")
        return

    genai.configure(api_key=API_KEY)
    logger.info("Configured Gemini API")

    # Read links from link.txt
    if not os.path.exists(LINK_FILE):
        logger.error(f"{LINK_FILE} does not exist")
        return
    with open(LINK_FILE, 'r', encoding='utf-8') as f:
        report_urls = [line.strip() for line in f if line.strip()]
    logger.info(f"Found {len(report_urls)} URLs")

    # Load existing homework data
    homework_data = []
    if os.path.exists(HOMEWORK_FILE):
        try:
            with open(HOMEWORK_FILE, 'r', encoding='utf-8') as f:
                homework_data = json.load(f)
            logger.info(f"Loaded {len(homework_data)} entries from {HOMEWORK_FILE}")
        except Exception as e:
            logger.error(f"Error reading {HOMEWORK_FILE}: {str(e)}")

    # Process each report URL
    processed_urls = {entry.get('report_url') for entry in homework_data}
    for report_url in report_urls:
        if report_url in processed_urls:
            logger.info(f"Skipping processed URL: {report_url}")
            continue
        logger.info(f"Processing URL: {report_url}")
        data = process_report_link(report_url)
        if data:
            homework_data.append(data)
            logger.info(f"Processed report: {report_url}")
        else:
            logger.warning(f"Failed to process report: {report_url}, continuing")

    # Save to homework.json
    try:
        with open(HOMEWORK_FILE, 'w', encoding='utf-8') as f:
            json.dump(homework_data, f, ensure_ascii=False, indent=4)
        logger.info(f"Saved {HOMEWORK_FILE} with {len(homework_data)} entries")
    except Exception as e:
        logger.error(f"Error saving {HOMEWORK_FILE}: {str(e)}")

if __name__ == "__main__":
    logger.info("Initializing script")
    main()
    logger.info("Script terminated")
