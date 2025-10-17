import json
import os
import requests
import pdfplumber
import google.generativeai as genai
from urllib.parse import parse_qs, urlparse
from datetime import datetime
from zoneinfo import ZoneInfo
import logging

# Configuration
LOG_FILE = "class_info_log.txt"
HOMEWORK_FILE = "homework.json"
LINK_FILE = "link.txt"
API_KEY = os.getenv("GEMINI_API_KEY")
TODAY = datetime.now(tz=ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d")

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
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error("Invalid JSON response, returning default")
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
        logger.info(f"Available models: {available_models}")
        for model in available_models:
            if attempt == 0 and 'gemini-2.5-flash' in model:
                return model
            if attempt == 1 and 'gemini-2.5-pro' in model:
                return model
            if attempt == 2 and 'gemini-pro' in model:
                return model
        return available_models[0] if available_models else None
    except Exception as e:
        logger.error(f"Failed to list models: {str(e)}")
        return None

# Process a single report link
def process_report_link(report_url):
    logger.info(f"Processing report URL: {report_url}")
    
    # Extract direct PDF URL from Google Docs link
    parsed_url = urlparse(report_url)
    query_params = parse_qs(parsed_url.query)
    direct_pdf_url = query_params.get('url', [None])[0]
    if not direct_pdf_url:
        logger.error("Could not extract direct PDF URL")
        return None

    # Download PDF
    pdf_path = 'temp_report.pdf'
    logger.info(f"Downloading PDF from {direct_pdf_url}")
    try:
        response = requests.get(direct_pdf_url, timeout=10)
        response.raise_for_status()
        content_type = response.headers.get('content-type', '')
        if 'application/pdf' not in content_type:
            logger.error(f"Downloaded file is not a PDF (Content-Type: {content_type})")
            return None
        with open(pdf_path, 'wb') as f:
            f.write(response.content)
        logger.info(f"Successfully downloaded PDF to {pdf_path}")
    except requests.RequestException as e:
        logger.error(f"Failed to download PDF: {str(e)}")
        return None

    # Extract text and links from PDF
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
        logger.info(f"Extracted {len(pdf_text)} characters and {len(pdf_links)} links from PDF")
    except Exception as e:
        logger.error(f"Failed to extract text or links from PDF: {str(e)}")
        return None
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
            logger.info(f"Deleted temporary PDF file: {pdf_path}")

    if not pdf_text:
        logger.error("No text extracted from PDF")
        return None

    # Call Gemini API
    max_attempts = 3
    extracted_data = None
    for attempt in range(max_attempts):
        logger.info(f"Gemini API attempt {attempt + 1}/{max_attempts}")
        model_name = get_gemini_model(attempt)
        if not model_name:
            logger.error("No suitable model found")
            continue
        logger.info(f"Using model: {model_name}")
        try:
            model = genai.GenerativeModel(model_name, system_instruction=SYSTEM_PROMPT)
            response = model.generate_content(pdf_text)
            cleaned_text = clean_json_response(response.text)
            extracted_data = json.loads(cleaned_text)
            extracted_data['links_all'] = list(set(extracted_data.get('links_all', []) + [{"context": "PDF link", "url": link, "type": "other"} for link in pdf_links]))
            logger.info(f"API attempt {attempt + 1} successful")
            break
        except Exception as e:
            logger.error(f"API attempt {attempt + 1}/{max_attempts} failed: {str(e)}")
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
                    "links_all": [{"context": "PDF link", "url": link, "type": "other"} for link in pdf_links]
                }

    extracted_data['report_url'] = report_url
    return extracted_data

# Main function
def main():
    logger.info("Starting report extraction")
    if not API_KEY:
        logger.error("Missing GEMINI_API_KEY, aborting")
        return

    genai.configure(api_key=API_KEY)

    # Read links from link.txt
    if not os.path.exists(LINK_FILE):
        logger.error(f"{LINK_FILE} does not exist")
        return
    with open(LINK_FILE, 'r', encoding='utf-8') as f:
        report_urls = [line.strip() for line in f if line.strip()]
    logger.info(f"Found {len(report_urls)} report URLs in {LINK_FILE}")

    # Load existing homework data
    homework_data = []
    if os.path.exists(HOMEWORK_FILE):
        try:
            with open(HOMEWORK_FILE, 'r', encoding='utf-8') as f:
                homework_data = json.load(f)
            logger.info(f"Loaded existing {HOMEWORK_FILE}: {len(homework_data)} entries")
        except Exception as e:
            logger.error(f"Error reading {HOMEWORK_FILE}: {str(e)}")

    # Process each report URL
    processed_urls = {entry.get('report_url') for entry in homework_data}
    for report_url in report_urls:
        if report_url in processed_urls:
            logger.info(f"Skipping already processed URL: {report_url}")
            continue
        data = process_report_link(report_url)
        if data:
            homework_data.append(data)
            logger.info(f"Processed report: {report_url}")

    # Save to homework.json
    try:
        with open(HOMEWORK_FILE, 'w', encoding='utf-8') as f:
            json.dump(homework_data, f, ensure_ascii=False, indent=4)
        logger.info(f"Successfully saved {HOMEWORK_FILE} with {len(homework_data)} entries")
    except Exception as e:
        logger.error(f"Error saving {HOMEWORK_FILE}: {str(e)}")

if __name__ == "__main__":
    logger.info("Starting script")
    main()
    logger.info("Script completed")
