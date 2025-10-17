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
    logger.debug("Cleaning JSON response")
    text = text.strip()
    logger.debug(f"Raw response text: {text[:100]}...")
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
        logger.debug("Stripped ```json``` markers")
    elif text.startswith('```') and text.endswith('```'):
        text = text[3:-3].strip()
        logger.debug("Stripped ``` markers")
    try:
        parsed_json = json.loads(text)
 पर्य

System: It looks like the response was cut off, possibly due to a timeout or processing issue with the previous script execution. Based on the logs you provided, the script successfully generated and logged the PDF URL for the first Google Docs URL (`https://docs.google.com/document/d/1N-1f4Ij9DhZtmudo4ck5pDf56Tug9faH2AaicjaBXCw/edit?usp=sharing`), but it stalled during PDF processing with `pdfplumber`, as evidenced by the verbose `nextline` logs from `pdfplumber`. You’ve also requested to reduce log verbosity to avoid excessive `nextline` logs and focus on key outputs like the PDF URLs.

The updated code I provided in the previous response should address this by:
1. Suppressing `pdfplumber` debug logs (e.g., `nextline`, `xref`) by setting its logger to `ERROR`.
2. Adding a page limit to prevent hangs on large or problematic PDFs.
3. Ensuring the script continues processing all URLs even if one fails.
4. Logging PDF URLs clearly and including them in the `homework.json` output.

Since the logs you shared are from an older run (still showing `pdfplumber` debug logs), it seems you haven’t yet run the updated script. Below, I’ll provide a streamlined version of the code, ensuring it’s concise, suppresses verbose logs, and includes robust error handling to prevent stalling. I’ll also explain why the script might be stalling and how to debug it further.

### Why the Script is Stalling
The logs indicate the script hangs during the `pdfplumber.open` call while extracting text from the PDF (`temp_report.pdf`). Possible reasons include:
- **Malformed PDF**: The PDF might be corrupted or formatted in a way that `pdfplumber` struggles to process.
- **Large PDF**: The PDF could have many pages or complex elements (e.g., images, tables), causing `pdfplumber` to consume excessive memory or time.
- **Resource Constraints**: Limited memory or CPU on the system could cause the process to hang.
- **Library Issue**: A bug or incompatibility in the `pdfplumber` library version.

### Updated Code
Below is the streamlined code, incorporating all previous fixes and further optimizing for reliability and minimal logging. Key changes:
- **Log Level Set to INFO**: Reduced the default logging level to `INFO` to minimize debug output, while keeping `DEBUG` logs for critical steps (e.g., JSON parsing).
- **Page Limit in PDF Processing**: Limits processing to the first 10 pages to prevent hangs on large PDFs.
- **Timeout for PDF Processing**: Adds a timeout mechanism using `signal` to prevent `pdfplumber` from hanging indefinitely.
- **Clear PDF URL Logging**: Ensures PDF URLs are logged immediately and included in the output JSON.
- **Continue on Failure**: Skips problematic URLs and continues processing the rest.

<xaiArtifact artifact_id="ec451bd4-a5fd-45d0-b15a-eebda0bdde01" artifact_version_id="c91233bc-00d8-43d5-b483-16e0400666bb" title="process_lesson_report.py" contentType="text/python">
import json
import os
import requests
import pdfplumber
import google.generativeai as genai
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import signal

# Configuration
LOG_FILE = "class_info_log3.txt"
HOMEWORK_FILE = "homework.json"
LINK_FILE = "link.txt"
API_KEY = os.getenv("GEMINI_API_KEY")
TODAY = datetime.now(tz=ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d")
MAX_PAGES = 10  # Limit PDF processing to 10 pages
PDF_TIMEOUT = 30  # Timeout for PDF processing in seconds

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

# Timeout handler for PDF processing
def timeout_handler(signum, frame):
    raise TimeoutError("PDF processing timed out")

# Clean and validate JSON response
def clean_json_response(text):
    logger.debug("Cleaning JSON response")
    text = text.strip()
    logger.debug(f"Raw response text: {text[:100]}...")
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
        logger.debug(f"Failed JSON content: {text[:200]}...")
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
    logger.info(f"Processing Google Docs URL: {url}")
    match = re.match(r"https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)(?:/edit.*|$|/?\?.*|$)", url)
    if not match:
        logger.error(f"Invalid Google Docs URL format: {url}")
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
        logger.error("Failed to generate PDF URL, skipping processing")
        return None
    logger.info(f"PDF export URL generated: {direct_pdf_url}")

    # Download PDF from direct URL
    pdf_path = 'temp_report.pdf'
    logger.info(f"Downloading PDF to {pdf_path}")
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

    # Extract text and links from PDF with timeout and page limit
    pdf_text = ''
    pdf_links = []
    logger.info("Extracting text and links from PDF")
    try:
        # Set timeout for PDF processing
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(PDF_TIMEOUT)
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages[:MAX_PAGES]):
                try:
                    text = page.extract_text()
                    pdf_text += text or ''
                    logger.debug(f"Extracted text from page {i + 1}: {text[:100] if text else 'None'}...")
                    if page.annots:
                        for annot in page.annots:
                            if 'uri' in annot:
                                pdf_links.append(annot['uri'])
                                logger.debug(f"Found PDF link: {annot['uri']}")
                except Exception as e:
                    logger.error(f"Failed to process page {i + 1} in PDF: {str(e)}")
                    continue
        signal.alarm(0)  # Disable timeout
        logger.info(f"Extracted {len(pdf_text)} characters and {len(pdf_links)} links from PDF")
    except TimeoutError:
        logger.error(f"PDF processing timed out after {PDF_TIMEOUT} seconds")
        return None
    except Exception as e:
        logger.error(f"Failed to open or process PDF: {str(e)}")
        return None
    finally:
        if os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
                logger.info(f"Deleted temporary PDF file: {pdf_path}")
            except Exception as e:
                logger.error(f"Failed to delete temporary PDF file: {str(e)}")

    if not pdf_text:
        logger.error("No text extracted from PDF")
        return None

    # Call Gemini API
    max_attempts = 3
    extracted_data = None
    logger.info(f"Starting Gemini API calls, max attempts: {max_attempts}")
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
    logger.info(f"Current timestamp: {TODAY}")
    if not API_KEY:
        logger.error("Missing GEMINI_API_KEY, aborting")
        return

    genai.configure(api_key=API_KEY)
    logger.info("Configured Gemini API with provided API key")

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
    logger.info(f"Processed URLs: {len(processed_urls)}")
    for report_url in report_urls:
        if report_url in processed_urls:
            logger.info(f"Skipping already processed URL: {report_url}")
            continue
        logger.info(f"Processing new URL: {report_url}")
        data = process_report_link(report_url)
        if data:
            homework_data.append(data)
            logger.info(f"Processed and added report: {report_url}")
        else:
            logger.warning(f"Failed to process report: {report_url}, continuing with next URL")

    # Save to homework.json
    try:
        with open(HOMEWORK_FILE, 'w', encoding='utf-8') as f:
            json.dump(homework_data, f, ensure_ascii=False, indent=4)
        logger.info(f"Successfully saved {HOMEWORK_FILE} with {len(homework_data)} entries")
    except Exception as e:
        logger.error(f"Error saving {HOMEWORK_FILE}: {str(e)}")

if __name__ == "__main__":
    logger.info("Initializing script")
    main()
    logger.info("Script terminated")
