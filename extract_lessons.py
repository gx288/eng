import json
import os
import requests
import pdfplumber
import google.generativeai as genai
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import logging

# Configuration
LOG_FILE = "class_info_log3.txt"
HOMEWORK_FILE = "homework.json"
LINK_FILE = "link.txt"
API_KEY = os.getenv("GEMINI_API_KEY")
TODAY = datetime.now(tz=ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d")

# Logging setup
logging.basicConfig(
    level=logging.DEBUG,
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
    logger.debug("Cleaning JSON response")
    text = text.strip()
    logger.debug(f"Raw response text: {text[:100]}...")  # Log first 100 chars for brevity
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
        logger.debug("Stripped ```json``` markers")
    elif text.startswith('```') and text.endswith('```'):
        text = text[3:-3].strip()
        logger.debug("Stripped ``` markers")
    try:
        parsed_json = json.loads(text)
        logger.info("Successfully parsed JSON response")
        return parsed_json
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON response: {str(e)}")
        logger.debug(f"Failed JSON content: {text[:200]}...")  # Log first 200 chars
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
    logger.debug(f"Fetching Gemini model, attempt {attempt + 1}")
    try:
        models = genai.list_models()
        available_models = [model.name for model in models if 'generateContent' in model.supported_generation_methods]
        logger.info(f"Available models: {available_models}")
        for model in available_models:
            if attempt == 0 and 'gemini-2.5-flash' in model:
                logger.debug(f"Selected model: {model}")
                return model
            if attempt == 1 and 'gemini-2.5-pro' in model:
                logger.debug(f"Selected model: {model}")
                return model
            if attempt == 2 and 'gemini-pro' in model:
                logger.debug(f"Selected model: {model}")
                return model
        selected_model = available_models[0] if available_models else None
        logger.info(f"Selected default model: {selected_model}")
        return selected_model
    except Exception as e:
        logger.error(f"Failed to list models: {str(e)}")
        return None

# Clean Google Docs URL and convert to PDF export URL
def clean_google_docs_url(url):
    logger.debug(f"Processing Google Docs URL: {url}")
    # Updated regex to handle various URL formats
    match = re.match(r"https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)(?:/edit.*|$|/?\?.*|$)", url)
    if not match:
        logger.error(f"Invalid Google Docs URL format: {url}")
        logger.debug(f"Regex pattern used: https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)(?:/edit.*|$|/?\?.*|$)")
        logger.debug("Regex match failed")
        return None
    doc_id = match.group(1)
    logger.info(f"Extracted document ID: {doc_id}")
    pdf_url = f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"
    logger.info(f"Generated PDF export URL: {pdf_url}")
    return pdf_url

# Process a single report link
def process_report_link(report_url):
    logger.info(f"Starting processing for report URL: {report_url}")
    
    # Clean and convert URL to PDF export
    direct_pdf_url = clean_google_docs_url(report_url)
    if not direct_pdf_url:
        logger.error("Failed to generate PDF URL, aborting processing")
        return None
    logger.debug(f"PDF export URL: {direct_pdf_url}")

    # Download PDF from direct URL
    pdf_path = 'temp_report.pdf'
    logger.debug(f"Attempting to download PDF to {pdf_path}")
    try:
        response = requests.get(direct_pdf_url, timeout=10)
        response.raise_for_status()
        content_type = response.headers.get('content-type', '')
        logger.debug(f"Response content-type: {content_type}")
        if 'application/pdf' not in content_type:
            logger.error(f"Downloaded file is not a PDF (Content-Type: {content_type})")
            return None
        with open(pdf_path, 'wb') as f:
            f.write(response.content)
        logger.info(f"Successfully downloaded PDF to {pdf_path}")
    except requests.RequestException as e:
        logger.error(f"Failed to download PDF from {direct_pdf_url}: {str(e)}")
        return None

    # Extract text and links from PDF
    pdf_text = ''
    pdf_links = []
    logger.debug("Extracting text and links from PDF")
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                pdf_text += text or ''
                logger.debug(f"Extracted text from page: {text[:100] if text else 'None'}...")
                if page.annots:
                    for annot in page.annots:
                        if 'uri' in annot:
                            pdf_links.append(annot['uri'])
                            logger.debug(f"Found PDF link: {annot['uri']}")
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
    logger.debug(f"Starting Gemini API calls, max attempts: {max_attempts}")
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
            logger.debug(f"Gemini API response: {response.text[:100]}...")
            cleaned_text = clean_json_response(response.text)
            extracted_data = json.loads(cleaned_text)
            extracted_data['links_all'] = list(set(extracted_data.get('links_all', []) + [{"context": "PDF link", "url": link, "type": "other"} for link in pdf_links]))
            extracted_data['report_url'] = report_url
            extracted_data['pdf_export_url'] = direct_pdf_url
            logger.info(f"API attempt {attempt + 1} successful, extracted data: {json.dumps(extracted_data, ensure_ascii=False)[:200]}...")
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
                    "links_all": [{"context": "PDF link", "url": link, "type": "other"} for link in pdf_links],
                    "report_url": report_url,
                    "pdf_export_url": direct_pdf_url
                }

    logger.info(f"Completed processing for {report_url}")
    return extracted_data

# Main function
def main():
    logger.info("Starting report extraction")
    logger.debug(f"Current timestamp: {TODAY}")
    if not API_KEY:
        logger.error("Missing GEMINI_API_KEY, aborting")
        return

    genai.configure(api_key=API_KEY)
    logger.debug("Configured Gemini API with provided API key")

    # Read links from link.txt
    if not os.path.exists(LINK_FILE):
        logger.error(f"{LINK_FILE} does not exist")
        return
    with open(LINK_FILE, 'r', encoding='utf-8') as f:
        report_urls = [line.strip() for line in f if line.strip()]
    logger.info(f"Found {len(report_urls)} report URLs in {LINK_FILE}")
    logger.debug(f"Report URLs: {report_urls}")

    # Load existing homework data
    homework_data = []
    if os.path.exists(HOMEWORK_FILE):
        try:
            with open(HOMEWORK_FILE, 'r', encoding='utf-8') as f:
                homework_data = json.load(f)
            logger.info(f"Loaded existing {HOMEWORK_FILE}: {len(homework_data)} entries")
            logger.debug(f"Existing homework data: {json.dumps(homework_data, ensure_ascii=False)[:200]}...")
        except Exception as e:
            logger.error(f"Error reading {HOMEWORK_FILE}: {str(e)}")

    # Process each report URL
    processed_urls = {entry.get('report_url') for entry in homework_data}
    logger.debug(f"Processed URLs: {processed_urls}")
    for report_url in report_urls:
        if report_url in processed_urls:
            logger.info(f"Skipping already processed URL: {report_url}")
            continue
        logger.debug(f"Processing new URL: {report_url}")
        data = process_report_link(report_url)
        if data:
            homework_data.append(data)
            logger.info(f"Processed and added report: {report_url}")
            logger.debug(f"Added data: {json.dumps(data, ensure_ascii=False)[:200]}...")

    # Save to homework.json
    try:
        with open(HOMEWORK_FILE, 'w', encoding='utf-8') as f:
            json.dump(homework_data, f, ensure_ascii=False, indent=4)
        logger.info(f"Successfully saved {HOMEWORK_FILE} with {len(homework_data)} entries")
    except Exception as e:
        logger.error(f"Error saving {HOMEWORK_FILE}: {str(e)}")

if __name__ == "__main__":
    logger.debug("Initializing script")
    main()
    logger.debug("Script terminated")
