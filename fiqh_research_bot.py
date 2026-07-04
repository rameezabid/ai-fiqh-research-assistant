"""
Four-School Fiqh Research Bot

A Telegram research bot that searches only the Turath database in the four
fiqh categories. It uses OpenAI only to generate Arabic search terms and to
write a source-grounded synthesis of Turath excerpts.

No vector stores or uploaded-file retrieval are used. The regular fiqh and
usul commands use Turath only. Optional /fatwa, /islamqa, and /islamweb
commands use Brave Search to find and retrieve pages from those named websites.
"""

import html
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters


# -----------------------------------------------------------------------------
# Environment and application setup
# -----------------------------------------------------------------------------

load_dotenv()


def env_int(name: str, default: int, minimum: Optional[int] = None) -> int:
    """Read an integer environment value while safely falling back to default."""
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        logging.warning("%s=%r is not an integer. Using %s.", name, raw, default)
        return default

    if minimum is not None and value < minimum:
        logging.warning("%s=%s is below %s. Using %s.", name, value, minimum, default)
        return default
    return value


def env_csv_ints(name: str) -> List[int]:
    """Read comma-separated positive Turath book IDs from an environment value."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return []

    values: List[int] = []
    seen: Set[int] = set()
    for part in raw.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            book_id = int(candidate)
        except ValueError:
            logging.warning("Ignoring invalid book ID %r in %s.", candidate, name)
            continue
        if book_id <= 0:
            logging.warning("Ignoring non-positive book ID %r in %s.", candidate, name)
            continue
        if book_id not in seen:
            values.append(book_id)
            seen.add(book_id)
    return values


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PUBLIC_BOT = os.getenv("PUBLIC_BOT", "false").strip().lower() == "true"
ALLOWED_TELEGRAM_USER_ID = env_int("ALLOWED_TELEGRAM_USER_ID", 0, minimum=0)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini").strip()
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
BRAVE_SEARCH_RESULTS_PER_QUERY = env_int("BRAVE_SEARCH_RESULTS_PER_QUERY", 5, minimum=1)
FATWA_MAX_PAGES_PER_SITE = env_int("FATWA_MAX_PAGES_PER_SITE", 4, minimum=1)
FATWA_REQUEST_TIMEOUT = env_int("FATWA_REQUEST_TIMEOUT", 25, minimum=5)
FATWA_MAX_RETRIES = env_int("FATWA_MAX_RETRIES", 3, minimum=1)
FATWA_DELAY_BETWEEN_CALLS_MS = env_int("FATWA_DELAY_BETWEEN_CALLS_MS", 500, minimum=0)

# Every one of these controls is intentionally editable from .env.
TURATH_SEARCH_QUERY_COUNT = env_int("TURATH_SEARCH_QUERY_COUNT", 4, minimum=1)
TURATH_RESULTS_PER_QUERY = env_int("TURATH_RESULTS_PER_QUERY", 5, minimum=1)
TURATH_MAX_RESULTS_PER_SCHOOL = env_int("TURATH_MAX_RESULTS_PER_SCHOOL", 10, minimum=1)
TURATH_CONTEXT_TOP_N = env_int("TURATH_CONTEXT_TOP_N", 5, minimum=0)
TURATH_CONTEXT_RADIUS = env_int("TURATH_CONTEXT_RADIUS", 3, minimum=0)
TURATH_RESULT_TEXT_CHARS = env_int("TURATH_RESULT_TEXT_CHARS", 2600, minimum=500)
TURATH_CONTEXT_PAGE_CHARS = env_int("TURATH_CONTEXT_PAGE_CHARS", 3500, minimum=500)
TURATH_REQUEST_TIMEOUT = env_int("TURATH_REQUEST_TIMEOUT", 25, minimum=5)
TURATH_MAX_RETRIES = env_int("TURATH_MAX_RETRIES", 3, minimum=1)
TURATH_BACKOFF_SECONDS = env_int("TURATH_BACKOFF_SECONDS", 2, minimum=0)
TURATH_DELAY_BETWEEN_CALLS_MS = env_int("TURATH_DELAY_BETWEEN_CALLS_MS", 350, minimum=0)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

client = OpenAI(api_key=OPENAI_API_KEY)


# -----------------------------------------------------------------------------
# Four-school configuration
# -----------------------------------------------------------------------------

TURATH_SEARCH_URL = "https://api.turath.io/search"
TURATH_PAGE_URL = "https://api.turath.io/page"
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
ISLAMWEB_DOMAIN = "https://www.islamweb.net"
ISLAMQA_DOMAIN = "https://islamqa.info"
EXTERNAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}


@dataclass(frozen=True)
class School:
    key: str
    label: str
    category_id: int
    preferred_books: Tuple[int, ...]
    command: str
    pref_command: str


SCHOOLS: Dict[str, School] = {
    "hanbali": School(
        key="hanbali",
        label="Hanbali",
        category_id=env_int("HANBALI_CATEGORY_ID", 17, minimum=1),
        preferred_books=tuple(env_csv_ints("HANBALI_PREFERRED_BOOKS")),
        command="/hanbali",
        pref_command="/hanbalipref",
    ),
    "hanafi": School(
        key="hanafi",
        label="Hanafi",
        category_id=env_int("HANAFI_CATEGORY_ID", 14, minimum=1),
        preferred_books=tuple(env_csv_ints("HANAFI_PREFERRED_BOOKS")),
        command="/hanafi",
        pref_command="/hanafipref",
    ),
    "maliki": School(
        key="maliki",
        label="Maliki",
        category_id=env_int("MALIKI_CATEGORY_ID", 15, minimum=1),
        preferred_books=tuple(env_csv_ints("MALIKI_PREFERRED_BOOKS")),
        command="/maliki",
        pref_command="/malikipref",
    ),
    "shafi": School(
        key="shafi",
        label="Shafi‘i",
        category_id=env_int("SHAFI_CATEGORY_ID", 16, minimum=1),
        preferred_books=tuple(env_csv_ints("SHAFI_PREFERRED_BOOKS")),
        command="/shafi",
        pref_command="/shafipref",
    ),
    "usool": School(
        key="usool",
        label="Usul al-Fiqh",
        category_id=env_int("USOOL_CATEGORY_ID", 11, minimum=1),
        preferred_books=tuple(env_csv_ints("USOOL_PREFERRED_BOOKS")),
        command="/usool",
        pref_command="/usoolpref",
    ),
}

ALL_SCHOOL_KEYS = ("hanbali", "hanafi", "maliki", "shafi")

# Both spellings are accepted for Shafi‘i, and underscore aliases are allowed.
NORMAL_COMMAND_TO_SCHOOL = {
    "/hanbali": "hanbali",
    "/hanafi": "hanafi",
    "/maliki": "maliki",
    "/shafi": "shafi",
    "/shafii": "shafi",
    "/usool": "usool",
}
PREFERRED_COMMAND_TO_SCHOOL = {
    "/hanbalipref": "hanbali",
    "/hanbali_pref": "hanbali",
    "/hanafipref": "hanafi",
    "/hanafi_pref": "hanafi",
    "/malikipref": "maliki",
    "/maliki_pref": "maliki",
    "/shafipref": "shafi",
    "/shafi_pref": "shafi",
    "/shafiipref": "shafi",
    "/usoolpref": "usool",
    "/usool_pref": "usool",
}


# -----------------------------------------------------------------------------
# Prompts
# -----------------------------------------------------------------------------

QUERY_GENERATION_INSTRUCTIONS = """
You create Arabic retrieval queries for a four-school fiqh and usul al-fiqh research assistant.

The user may write in Arabic or English. Return JSON only, with no markdown:
{
  "language": "arabic" or "english",
  "queries": ["short Arabic query", "..."]
}

Requirements:
- Produce 2 to 6 concise Arabic keyword phrases suitable for a classical fiqh database.
- Include useful legal terminology and likely Arabic equivalents of English concepts.
- Do not answer the question.
- Do not name a legal ruling or select a madhhab.
- Do not include explanations.
"""

SOURCE_GROUNDED_ANSWER_INSTRUCTIONS = """
You are a four-school fiqh and usul al-fiqh research assistant.

Answer ONLY from the Turath excerpts supplied by the user. The excerpts are the
complete approved evidence for this response.

Strict rules:
- Never use outside knowledge, assumptions, or uncited general fiqh knowledge.
- Do not issue a personal fatwa. Describe what the retrieved texts state.
- Do not call the excerpts "Source 1," "Source 2," or any other internal label.
- Never refer to a book or author that is not included in the excerpts.
- Distinguish clearly between explicit text and a cautious inference.
- Where schools differ, identify each school only when the relevant excerpt
  clearly supports the attribution.
- When an excerpt is insufficient, say so plainly.
- Do not fabricate volume, page, title, author, or link details.
- Do NOT add a "Sources used" section. The application adds a verified source
  list after your answer.
- Do not use Markdown of any kind: no asterisks, hashes, backticks, tables, or bullet markers.
- Use short headings only as plain text followed by a colon, with paragraphs beneath them.

Language:
- The required reply language is provided explicitly in the user message. Follow it strictly.
- For English questions, write the explanation in English. Arabic may appear only in short direct quotations or individual technical terms.
- For Arabic questions, write the explanation in Arabic.
- Translate technical terms carefully and include Arabic where helpful.

Keep the answer focused, readable, and evidence-based.
"""


# -----------------------------------------------------------------------------
# General helpers
# -----------------------------------------------------------------------------


def is_allowed(update: Update) -> bool:
    if PUBLIC_BOT:
        return True
    user = update.effective_user
    return bool(user and user.id == ALLOWED_TELEGRAM_USER_ID)


def contains_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))

def detect_user_language(text: str) -> str:
    """Use the wording of the original user message, never the model's guess.

    A question containing any meaningful amount of Latin-script prose is treated
    as English even when it includes Arabic legal terms or quotations.
    """
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    arabic = len(re.findall(r"[\u0600-\u06FF]", text or ""))
    if latin >= 3:
        return "english"
    return "arabic" if arabic else "english"

def clean_telegram_text(text: str) -> str:
    """Remove common Markdown artifacts while preserving readable paragraphs."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```(?:[A-Za-z0-9_+-]+)?\s*", "", text)
    text = text.replace("```", "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"(?<!\w)(\*\*|__)(.+?)(\1)", r"\2", text)
    text = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (TypeError, json.JSONDecodeError):
        pass

    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def strip_html(raw: Optional[str]) -> str:
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    return html.unescape(soup.get_text(" ", strip=True))


def truncate_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def telegram_safe_chunks(text: str, limit: int = 3900) -> List[str]:
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current = ""
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        addition = paragraph + "\n"
        if len(current) + len(addition) <= limit:
            current += addition
            continue

        if current.strip():
            chunks.append(current.strip())
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph + "\n"

    if current.strip():
        chunks.append(current.strip())
    return chunks or [text[:limit]]


def unique_preserving_order(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    output: List[str] = []
    for item in items:
        if item not in seen:
            output.append(item)
            seen.add(item)
    return output


def format_book_ids(book_ids: Sequence[int]) -> str:
    return ",".join(str(book_id) for book_id in dict.fromkeys(book_ids))


def source_key(result: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        result.get("school_key"),
        result.get("book_id"),
        result.get("page_id"),
        result.get("book_name"),
    )


# -----------------------------------------------------------------------------
# OpenAI query generation
# -----------------------------------------------------------------------------


def generate_search_queries(question: str) -> Tuple[str, List[str]]:
    """Return (language, Arabic search queries), with a safe local fallback."""
    fallback_language = detect_user_language(question)

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=QUERY_GENERATION_INSTRUCTIONS,
            input=f"User research question:\n{question}",
        )
        parsed = safe_json_loads(response.output_text)
    except Exception:
        logging.exception("OpenAI query-generation request failed")
        parsed = None

    if not parsed:
        return fallback_language, [question]

    # Response language follows the actual user wording, not model classification.
    language = detect_user_language(question)
    raw_queries = parsed.get("queries", [])
    queries = [item.strip() for item in raw_queries if isinstance(item, str) and item.strip()]
    return language, unique_preserving_order(queries)[:TURATH_SEARCH_QUERY_COUNT] or [question]


# -----------------------------------------------------------------------------
# Turath API and context expansion
# -----------------------------------------------------------------------------


def turath_request_json(url: str, params: Dict[str, Any], label: str) -> Optional[Dict[str, Any]]:
    for attempt in range(1, TURATH_MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=TURATH_REQUEST_TIMEOUT)
            if response.status_code == 200:
                time.sleep(TURATH_DELAY_BETWEEN_CALLS_MS / 1000)
                payload = response.json()
                return payload if isinstance(payload, dict) else None

            logging.warning("%s returned HTTP %s on attempt %s/%s: %s", label, response.status_code, attempt, TURATH_MAX_RETRIES, response.url)
            if response.status_code not in {429, 500, 502, 503, 504}:
                return None
        except Exception as exc:
            logging.warning("%s failed on attempt %s/%s: %s", label, attempt, TURATH_MAX_RETRIES, exc)

        if attempt < TURATH_MAX_RETRIES:
            time.sleep(TURATH_BACKOFF_SECONDS * attempt)

    logging.error("%s failed after %s attempts.", label, TURATH_MAX_RETRIES)
    return None


def parse_turath_meta(raw_meta: Any) -> Dict[str, Any]:
    if isinstance(raw_meta, dict):
        return raw_meta
    if not isinstance(raw_meta, str):
        return {}
    try:
        parsed = json.loads(raw_meta)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def search_turath(
    query: str,
    school: School,
    source_scope: str,
    book_ids: Optional[Sequence[int]] = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "ver": 3,
        "q": query,
        "non_author": 1,
        "precision": 2,
    }
    if book_ids:
        params["book"] = format_book_ids(book_ids)
    else:
        params["cat"] = school.category_id

    payload = turath_request_json(TURATH_SEARCH_URL, params, "Turath search API")
    if not payload:
        return []

    results: List[Dict[str, Any]] = []
    for item in payload.get("data", [])[:TURATH_RESULTS_PER_QUERY]:
        if not isinstance(item, dict):
            continue
        meta = parse_turath_meta(item.get("meta"))
        book_id = item.get("book_id")
        page_id = meta.get("page_id")
        try:
            numeric_book_id = int(book_id)
            numeric_page_id = int(page_id)
        except (TypeError, ValueError):
            numeric_book_id = None
            numeric_page_id = None

        url = None
        if numeric_book_id and numeric_page_id:
            url = f"https://app.turath.io/book/{numeric_book_id}?page={numeric_page_id}"

        results.append(
            {
                "source_type": "turath",
                "school_key": school.key,
                "school_label": school.label,
                "query": query,
                "source_scope": source_scope,
                "book_id": numeric_book_id,
                "page_id": numeric_page_id,
                "category_id": item.get("cat_id", school.category_id),
                "book_name": str(meta.get("book_name", "") or "Unknown book"),
                "author_name": str(meta.get("author_name", "") or "Unknown author"),
                "vol": str(meta.get("vol", "") or ""),
                "page": str(meta.get("page", "") or ""),
                "headings": meta.get("headings", []) or [],
                "snip": strip_html(item.get("snip", "")),
                "text": strip_html(item.get("text", "")),
                "url": url,
            }
        )
    return results


def fetch_turath_page(book_id: int, page_id: int) -> Optional[Dict[str, Any]]:
    if book_id <= 0 or page_id <= 0:
        return None

    payload = turath_request_json(
        TURATH_PAGE_URL,
        {"book_id": book_id, "pg": page_id},
        "Turath page API",
    )
    if not payload:
        return None

    text = strip_html(payload.get("text", ""))
    if not text:
        return None

    meta = parse_turath_meta(payload.get("meta"))
    return {
        "book_id": book_id,
        "page_id": page_id,
        "printed_page": str(meta.get("page", "") or ""),
        "vol": str(meta.get("vol", "") or ""),
        "book_name": str(meta.get("book_name", "") or ""),
        "author_name": str(meta.get("author_name", "") or ""),
        "url": f"https://app.turath.io/book/{book_id}?page={page_id}",
        "text": text,
    }


def expand_turath_context(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Expand the top configured search hits with nearby Turath pages.

    TURATH_CONTEXT_TOP_N=0 expands every retrieved result. Radius 0 disables
    page expansion while leaving the original result text untouched.
    """
    if TURATH_CONTEXT_RADIUS == 0 or not results:
        return results

    top_n = len(results) if TURATH_CONTEXT_TOP_N == 0 else min(TURATH_CONTEXT_TOP_N, len(results))
    cache: Dict[Tuple[int, int], Optional[Dict[str, Any]]] = {}
    expanded: List[Dict[str, Any]] = []

    for index, result in enumerate(results):
        if index >= top_n:
            expanded.append(result)
            continue

        book_id = result.get("book_id")
        center_page_id = result.get("page_id")
        if not isinstance(book_id, int) or not isinstance(center_page_id, int):
            expanded.append(result)
            continue

        pages: List[Dict[str, Any]] = []
        for page_id in range(max(1, center_page_id - TURATH_CONTEXT_RADIUS), center_page_id + TURATH_CONTEXT_RADIUS + 1):
            cache_key = (book_id, page_id)
            if cache_key not in cache:
                cache[cache_key] = fetch_turath_page(book_id, page_id)
            page_data = cache[cache_key]
            if page_data:
                pages.append(page_data)

        if not pages:
            expanded.append(result)
            continue

        page_texts = []
        for page in pages:
            page_texts.append(
                f"[Turath page ID {page['page_id']} | printed page {page['printed_page']} | volume {page['vol']}]\n"
                f"{truncate_text(page['text'], TURATH_CONTEXT_PAGE_CHARS)}"
            )

        changed = dict(result)
        changed["expanded_context"] = True
        changed["expanded_pages"] = pages
        changed["text"] = "\n\n".join(page_texts)
        first_page = pages[0]
        changed["book_name"] = result["book_name"] or first_page["book_name"]
        changed["author_name"] = result["author_name"] or first_page["author_name"]
        expanded.append(changed)

    return expanded


# -----------------------------------------------------------------------------
# Four-school search modes
# -----------------------------------------------------------------------------


def add_unique_results(destination: List[Dict[str, Any]], candidates: Sequence[Dict[str, Any]], max_total: int) -> None:
    seen = {source_key(item) for item in destination}
    for candidate in candidates:
        key = source_key(candidate)
        if key in seen:
            continue
        destination.append(candidate)
        seen.add(key)
        if len(destination) >= max_total:
            return


def search_school_layer(school: School, queries: Sequence[str], source_scope: str, book_ids: Optional[Sequence[int]] = None) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    for query in queries:
        found = search_turath(query, school, source_scope=source_scope, book_ids=book_ids)
        add_unique_results(collected, found, TURATH_MAX_RESULTS_PER_SCHOOL)
        if len(collected) >= TURATH_MAX_RESULTS_PER_SCHOOL:
            break
    return collected


def search_school_with_preferred_fallback(school: School, queries: Sequence[str]) -> List[Dict[str, Any]]:
    """Return preferred-book results first, then non-preferred category results.

    A normal command deliberately searches both layers. Category results from a
    preferred book are filtered out so that the second layer genuinely represents
    other books in that category.
    """
    preferred_results: List[Dict[str, Any]] = []
    if school.preferred_books:
        preferred_results = search_school_layer(
            school,
            queries,
            source_scope="preferred_books",
            book_ids=school.preferred_books,
        )

    category_results = search_school_layer(school, queries, source_scope="other_books_in_category")
    preferred_ids = set(school.preferred_books)
    other_results = [
        result for result in category_results
        if result.get("book_id") not in preferred_ids
    ]

    combined: List[Dict[str, Any]] = []
    add_unique_results(combined, preferred_results, TURATH_MAX_RESULTS_PER_SCHOOL)
    add_unique_results(combined, other_results, TURATH_MAX_RESULTS_PER_SCHOOL * 2)
    return combined


def search_preferred_only(schools: Sequence[School], queries: Sequence[str]) -> Tuple[List[Dict[str, Any]], List[School]]:
    configured = [school for school in schools if school.preferred_books]
    results: List[Dict[str, Any]] = []
    for school in configured:
        school_results = search_school_layer(
            school,
            queries,
            source_scope="preferred_books_only",
            book_ids=school.preferred_books,
        )
        add_unique_results(results, school_results, max_total=10_000)
    return results, configured


def collect_results(schools: Sequence[School], queries: Sequence[str], preferred_only: bool) -> Tuple[List[Dict[str, Any]], List[School]]:
    if preferred_only:
        return search_preferred_only(schools, queries)

    results: List[Dict[str, Any]] = []
    for school in schools:
        school_results = search_school_with_preferred_fallback(school, queries)
        add_unique_results(results, school_results, max_total=10_000)
    return results, list(schools)


# -----------------------------------------------------------------------------
# Source packet, answer generation, and verified source list
# -----------------------------------------------------------------------------


def build_source_packet(results: Sequence[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for index, result in enumerate(results, start=1):
        headings = result.get("headings", [])
        headings_text = " > ".join(str(item) for item in headings) if isinstance(headings, list) else ""
        parts.append(
            f"""EXCERPT {index}
School search scope: {result.get('school_label')}
Retrieval layer: {result.get('source_scope')}
Book: {result.get('book_name')}
Author: {result.get('author_name')}
Volume: {result.get('vol')}
Printed page: {result.get('page')}
Turath page ID: {result.get('page_id')}
Link: {result.get('url')}
Headings: {headings_text}

Snippet:
{truncate_text(result.get('snip', ''), 800)}

Text:
{truncate_text(result.get('text', ''), TURATH_RESULT_TEXT_CHARS)}"""
        )
    return "\n\n---\n\n".join(parts)


def format_verified_sources(results: Sequence[Dict[str, Any]]) -> str:
    """Create the citation list deterministically, rather than asking the model."""
    seen: Set[Tuple[Any, ...]] = set()
    lines: List[str] = []

    for result in results:
        key = (
            result.get("book_id"),
            result.get("page_id"),
            result.get("book_name"),
            result.get("school_key"),
        )
        if key in seen:
            continue
        seen.add(key)

        book_name = result.get("book_name") or "Unknown book"
        author = result.get("author_name") or "Unknown author"
        volume = result.get("vol")
        page = result.get("page")
        school = result.get("school_label") or ""
        url = result.get("url") or ""

        details = [f"{book_name} — {author}"]
        if school:
            details.append(f"{school} search")
        if volume:
            details.append(f"vol. {volume}")
        if page:
            details.append(f"p. {page}")
        if url:
            details.append(url)
        lines.append(f"{len(lines) + 1}. " + " | ".join(details))

    return "Sources used:\n" + ("\n".join(lines) if lines else "No verified Turath excerpts were retrieved.")


def response_looks_wrong_language(text: str, required_language: str) -> bool:
    arabic_count = len(re.findall(r"[\u0600-\u06FF]", text or ""))
    latin_count = len(re.findall(r"[A-Za-z]", text or ""))
    if required_language == "english":
        # A few Arabic terms or quotations are normal in an English research answer.
        return arabic_count > max(250, latin_count * 2)
    return latin_count > max(250, arabic_count * 2)


def enforce_answer_language(answer: str, required_language: str) -> str:
    """Make one focused corrective pass only when the answer is plainly mismatched."""
    answer = clean_telegram_text(answer)
    if not answer or not response_looks_wrong_language(answer, required_language):
        return answer
    language_name = "English" if required_language == "english" else "Arabic"
    try:
        correction = client.responses.create(
            model=OPENAI_MODEL,
            instructions=(
                f"Rewrite the supplied answer strictly in {language_name}. Preserve its meaning and do not add facts. "
                "Use plain text only: no Markdown, asterisks, bullets, or code formatting. "
                "Arabic quotations and individual technical terms may remain Arabic in an English answer."
            ),
            input=answer,
        )
        return clean_telegram_text(correction.output_text) or answer
    except Exception:
        logging.exception("Language-correction pass failed")
        return answer


def answer_from_sources(question: str, language: str, schools: Sequence[School], results: Sequence[Dict[str, Any]]) -> str:
    if not results:
        if language == "arabic":
            return "لم أجد نصوصًا كافية في نتائج التراث المسترجعة للإجابة عن هذا السؤال."
        return "I could not find enough relevant text in the retrieved Turath results to answer this question."

    school_names = ", ".join(school.label for school in schools)
    source_packet = build_source_packet(results)
    user_message = f"""User question:
{question}

Schools searched:
{school_names}

Required reply language: {language}

Approved Turath excerpts:
{source_packet}

Write an answer using only the approved excerpts. When results include the
retrieval layer "preferred_books", present those findings first and explicitly
introduce them as the configured preferred books. When results include the
retrieval layer "other_books_in_category", discuss them separately as books
outside the preferred list in that category. Do not claim a layer contains
findings when no excerpt from that layer was retrieved."""

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=SOURCE_GROUNDED_ANSWER_INSTRUCTIONS,
            input=user_message,
        )
        answer = enforce_answer_language(response.output_text, language)
        if answer:
            return answer
    except Exception:
        logging.exception("OpenAI source-grounded answer request failed")

    if language == "arabic":
        return "حدث خطأ أثناء توليد الجواب من النصوص المسترجعة. حاول مرة أخرى."
    return "There was an error generating an answer from the retrieved excerpts. Please try again."


# -----------------------------------------------------------------------------
# Telegram command parsing and processing
# -----------------------------------------------------------------------------


def strip_bot_username(token: str) -> str:
    return token.split("@", 1)[0].lower()


def parse_school_prefixes(first_command: str, remaining_tokens: Sequence[str]) -> Tuple[List[School], bool, str]:
    """
    Parse commands at the start of a request.

    Examples:
        /shafi /maliki question
        /hanbalipref /hanafipref question
    """
    command_map = {**NORMAL_COMMAND_TO_SCHOOL, **PREFERRED_COMMAND_TO_SCHOOL}
    tokens = [strip_bot_username(first_command)] + [strip_bot_username(token) for token in remaining_tokens]

    school_keys: List[str] = []
    modes: List[bool] = []  # True means preferred-only.
    consumed = 0

    for token in tokens:
        if token in NORMAL_COMMAND_TO_SCHOOL:
            school_keys.append(NORMAL_COMMAND_TO_SCHOOL[token])
            modes.append(False)
            consumed += 1
        elif token in PREFERRED_COMMAND_TO_SCHOOL:
            school_keys.append(PREFERRED_COMMAND_TO_SCHOOL[token])
            modes.append(True)
            consumed += 1
        else:
            break

    if not school_keys:
        return [], False, ""
    if len(set(modes)) > 1:
        raise ValueError("Do not mix normal school commands with preferred-only school commands in the same request.")

    question_tokens = list(remaining_tokens[consumed - 1:])
    schools = [SCHOOLS[key] for key in unique_preserving_order(school_keys)]
    return schools, modes[0], " ".join(question_tokens).strip()


async def reply_in_chunks(update: Update, text: str) -> None:
    for chunk in telegram_safe_chunks(text):
        await update.message.reply_text(chunk)


async def process_research_question(update: Update, question: str, schools: Sequence[School], preferred_only: bool) -> None:
    if not is_allowed(update):
        await update.message.reply_text("This bot is private.")
        return

    question = question.strip()
    if not question:
        await update.message.reply_text("Please include a fiqh research question after the command.")
        return

    if preferred_only:
        missing = [school for school in schools if not school.preferred_books]
        if len(schools) == 1 and missing:
            school = missing[0]
            await update.message.reply_text(
                f"No preferred books are listed for the {school.label} school. "
                f"Add comma-separated Turath book IDs to {school.key.upper()}_PREFERRED_BOOKS in your .env file."
            )
            return
        if len(missing) == len(schools):
            await update.message.reply_text(
                "No preferred books are listed for the selected schools. Add Turath book IDs to the relevant *_PREFERRED_BOOKS values in .env."
            )
            return

    await update.message.chat.send_action(action=ChatAction.TYPING)
    scope = ", ".join(school.label for school in schools)
    status = f"Searching Turath fiqh sources: {scope}"
    if preferred_only:
        status += " (preferred books only)"
    status += "..."
    await update.message.reply_text(status)

    language, queries = generate_search_queries(question)
    results, active_schools = collect_results(schools, queries, preferred_only=preferred_only)
    results = expand_turath_context(results)

    answer = answer_from_sources(question, language, active_schools, results)
    final_text = answer.rstrip() + "\n\n" + format_verified_sources(results)
    await reply_in_chunks(update, final_text)


async def school_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("This bot is private.")
        return

    first_command = (update.message.text or "").split(maxsplit=1)[0]
    try:
        schools, preferred_only, question = parse_school_prefixes(first_command, context.args)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    if not schools:
        await update.message.reply_text("Use a command such as /hanbali, /hanafi, /maliki, /shafi, or /usool.")
        return
    await process_research_question(update, question, schools, preferred_only)


async def all_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = " ".join(context.args).strip()
    await process_research_question(update, question, [SCHOOLS[key] for key in ALL_SCHOOL_KEYS], preferred_only=False)


async def all_pref_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = " ".join(context.args).strip()
    await process_research_question(update, question, [SCHOOLS[key] for key in ALL_SCHOOL_KEYS], preferred_only=True)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("This bot is private.")
        return
    await help_command(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("This bot is private.")
        return

    msg = """Four-School Fiqh Research Bot

This bot searches Turath for fiqh and usul research. It also has optional Brave-powered IslamQA and IslamWeb fatwa search commands when configured.

Commands:
/hanbali [question] — Hanbali category, with configured preferred books searched first.
/hanafi [question] — Hanafi category, with configured preferred books searched first.
/maliki [question] — Maliki category, with configured preferred books searched first.
/shafi [question] — Shafi‘i category, with configured preferred books searched first.
/usool [question] — Usul al-Fiqh category 11, with configured preferred books searched first.

/all [question] — Search all four fiqh-school categories (it does not include /usool).
/book BOOK_ID [question] — Search one specified Turath book only.
/fatwa [question] — Search IslamQA and IslamWeb (requires BRAVE_SEARCH_API_KEY).
/islamqa [question] — Search IslamQA only (requires BRAVE_SEARCH_API_KEY).
/islamweb [question] — Search IslamWeb only (requires BRAVE_SEARCH_API_KEY).

Search selected schools together by placing school commands first:
/shafi /maliki What invalidates wudu?
/hanafi /hanbali حكم الجمع في السفر

Preferred-books-only commands:
/hanbalipref [question]
/hanafipref [question]
/malikipref [question]
/shafipref [question]
/usoolpref [question]
/allpref [question]

Preferred-only commands search only book IDs listed in the relevant *_PREFERRED_BOOKS .env value. They return an explanatory message when no preferred books are configured.

You may also send a plain-text question. Plain-text questions use /all behavior.

Use /help to show this menu again."""
    await update.message.reply_text(msg)


async def plain_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("This bot is private.")
        return
    question = (update.message.text or "").strip()
    await process_research_question(update, question, [SCHOOLS[key] for key in ALL_SCHOOL_KEYS], preferred_only=False)


# -----------------------------------------------------------------------------
# Single-book search and optional Brave fatwa search
# -----------------------------------------------------------------------------

BOOK_SEARCH_SCOPE = School(
    key="single_book",
    label="Single Turath book",
    category_id=1,
    preferred_books=(),
    command="/book",
    pref_command="",
)

FATWA_QUERY_INSTRUCTIONS = """
Generate 2 to 4 concise Arabic search queries for Islamic fatwa websites.
Return JSON only: {"queries":["...", "..."]}
Do not answer the user's question.
"""

FATWA_ANSWER_INSTRUCTIONS = """
You are an Islamic fatwa research assistant. Answer only from the retrieved
IslamQA and/or IslamWeb excerpts. Do not use outside knowledge and do not issue
a new fatwa. Clearly say what the retrieved fatwas state. Do not refer to
internal labels such as Source 1. Do not add a Sources used section because the
application adds verified source links. Use plain text only: no Markdown,
asterisks, bullets, tables, or code formatting. Reply strictly in the same
language as the user's question. English questions require English explanatory
text; Arabic quotations may remain Arabic.
"""


def search_single_book(book_id: int, queries: Sequence[str]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for query in queries:
        found = search_turath(query, BOOK_SEARCH_SCOPE, "single_book_only", book_ids=[book_id])
        add_unique_results(results, found, TURATH_MAX_RESULTS_PER_SCHOOL)
        if len(results) >= TURATH_MAX_RESULTS_PER_SCHOOL:
            break
    return results


async def book_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("This bot is private.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Use: /book BOOK_ID your question here\nExample: /book 21731 What is the ruling on fasting while traveling?")
        return
    try:
        book_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("The first value after /book must be a numeric Turath book ID.")
        return
    if book_id <= 0:
        await update.message.reply_text("The Turath book ID must be a positive number.")
        return
    question = " ".join(context.args[1:]).strip()
    if not question:
        await update.message.reply_text("Use: /book BOOK_ID your question here")
        return
    await update.message.chat.send_action(action=ChatAction.TYPING)
    await update.message.reply_text(f"Searching Turath book {book_id} only...")
    language, queries = generate_search_queries(question)
    results = expand_turath_context(search_single_book(book_id, queries))
    answer = answer_from_sources(question, language, [BOOK_SEARCH_SCOPE], results)
    await reply_in_chunks(update, clean_telegram_text(answer).rstrip() + "\n\n" + format_verified_sources(results))


def external_get(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[str]:
    for attempt in range(1, FATWA_MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=EXTERNAL_HEADERS, timeout=FATWA_REQUEST_TIMEOUT)
            if response.status_code == 200:
                time.sleep(FATWA_DELAY_BETWEEN_CALLS_MS / 1000)
                return response.text
            if response.status_code not in {429, 500, 502, 503, 504}:
                return None
        except Exception as exc:
            logging.warning("External request failed (%s/%s): %s", attempt, FATWA_MAX_RETRIES, exc)
        if attempt < FATWA_MAX_RETRIES:
            time.sleep(attempt)
    return None


def normalize_fatwa_url(url: str, site: str) -> Optional[str]:
    url = html.unescape((url or "").strip())
    if url.startswith("//"):
        url = "https:" + url
    if site == "islamweb":
        if url.startswith("/"):
            url = ISLAMWEB_DOMAIN + url
        if "islamweb.net" in url and "/ar/fatwa/" in url:
            return url.split("?", 1)[0]
    if site == "islamqa":
        if url.startswith("/"):
            url = ISLAMQA_DOMAIN + url
        if "islamqa.info" in url and "/ar/answers/" in url:
            return url.split("?", 1)[0]
    return None


def brave_search_site(query: str, site: str) -> List[Dict[str, str]]:
    if not BRAVE_SEARCH_API_KEY:
        return []
    scope = "site:islamweb.net/ar/fatwa/" if site == "islamweb" else "site:islamqa.info/ar/answers/"
    try:
        response = requests.get(
            BRAVE_SEARCH_URL,
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_SEARCH_API_KEY},
            params={"q": f"{scope} {query}", "count": min(BRAVE_SEARCH_RESULTS_PER_QUERY, 20), "search_lang": "ar", "safesearch": "moderate"},
            timeout=FATWA_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logging.exception("Brave Search request failed")
        return []
    output: List[Dict[str, str]] = []
    for item in payload.get("web", {}).get("results", []):
        url = normalize_fatwa_url(str(item.get("url", "")), site)
        if not url:
            continue
        output.append({"site": site, "url": url, "title": strip_html(str(item.get("title", ""))), "query": query})
        if len(output) >= BRAVE_SEARCH_RESULTS_PER_QUERY:
            break
    return output


def extract_external_text(html_content: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "button", "form"]):
        tag.decompose()
    title_tag = soup.find("h1") or soup.find("title")
    title = strip_html(str(title_tag)) if title_tag else ""
    candidates = []
    for selector in ["article", "main", ".single_fatwa__answer", ".answer", ".content", "[class*='answer']"]:
        for tag in soup.select(selector):
            value = html.unescape(tag.get_text("\n", strip=True))
            value = re.sub(r"\n{3,}", "\n\n", value).strip()
            if len(value) >= 150:
                candidates.append(value)
    text = max(candidates, key=len) if candidates else soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()
    return title, text[:10000]


def fetch_fatwa(site: str, url: str, matched_query: str) -> Optional[Dict[str, str]]:
    html_content = external_get(url)
    if not html_content:
        return None
    title, text = extract_external_text(html_content)
    if len(text) < 120:
        return None
    number_match = re.search(r"/(?:fatwa|answers)/(\d+)", url)
    return {"site": site, "site_name": "IslamWeb" if site == "islamweb" else "IslamQA", "title": title or ("IslamWeb fatwa" if site == "islamweb" else "IslamQA answer"), "number": number_match.group(1) if number_match else "", "url": url, "text": text, "query": matched_query}


def generate_fatwa_queries(question: str) -> List[str]:
    try:
        response = client.responses.create(model=OPENAI_MODEL, instructions=FATWA_QUERY_INSTRUCTIONS, input=f"User question:\n{question}")
        parsed = safe_json_loads(response.output_text) or {}
        values = [str(q).strip() for q in parsed.get("queries", []) if isinstance(q, str) and q.strip()]
        return unique_preserving_order(values)[:4] or [question]
    except Exception:
        logging.exception("Fatwa query generation failed")
        return [question]


def format_fatwa_sources(fatwas: Sequence[Dict[str, str]]) -> str:
    lines = []
    seen: Set[str] = set()
    for fatwa in fatwas:
        if fatwa["url"] in seen:
            continue
        seen.add(fatwa["url"])
        details = [fatwa["site_name"], fatwa["title"]]
        if fatwa.get("number"):
            details.append("No. " + fatwa["number"])
        details.append(fatwa["url"])
        lines.append(f"{len(lines)+1}. " + " | ".join(details))
    return "Sources used:\n" + ("\n".join(lines) if lines else "No verified fatwa pages were retrieved.")


def answer_from_fatwas(question: str, language: str, fatwas: Sequence[Dict[str, str]]) -> str:
    if not fatwas:
        return "لم أجد فتاوى مناسبة في المصادر المحددة." if language == "arabic" else "I could not find suitable fatwas in the selected sources."
    packet = "\n\n---\n\n".join(
        f"Fatwa website: {f['site_name']}\nTitle: {f['title']}\nNumber: {f['number']}\nURL: {f['url']}\nText:\n{truncate_text(f['text'], 6000)}" for f in fatwas
    )
    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=FATWA_ANSWER_INSTRUCTIONS,
            input=f"User question:\n{question}\n\nRequired reply language: {language}\n\nApproved fatwa excerpts:\n{packet}",
        )
        answer = enforce_answer_language(response.output_text, language)
        if answer:
            return answer
    except Exception:
        logging.exception("Fatwa answer generation failed")
    return "حدث خطأ أثناء توليد الجواب." if language == "arabic" else "There was an error generating the answer. Please try again."


async def fatwa_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("This bot is private.")
        return
    command = strip_bot_username((update.message.text or "").split(maxsplit=1)[0])
    sites = ["islamqa"] if command == "/islamqa" else (["islamweb"] if command == "/islamweb" else ["islamqa", "islamweb"])
    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text(f"Use: {command} your question here")
        return
    if not BRAVE_SEARCH_API_KEY:
        await update.message.reply_text("This optional feature requires a Brave Search API key. Obtain one, add BRAVE_SEARCH_API_KEY to your .env file, and restart the bot.")
        return
    await update.message.chat.send_action(action=ChatAction.TYPING)
    await update.message.reply_text("Searching the selected fatwa websites...")
    queries = generate_fatwa_queries(question)
    discovered: List[Dict[str, str]] = []
    seen_urls: Set[str] = set()
    for site in sites:
        count = 0
        for query in queries:
            for result in brave_search_site(query, site):
                if result["url"] not in seen_urls:
                    discovered.append(result)
                    seen_urls.add(result["url"])
                    count += 1
                    if count >= FATWA_MAX_PAGES_PER_SITE:
                        break
            if count >= FATWA_MAX_PAGES_PER_SITE:
                break
    fatwas: List[Dict[str, str]] = []
    for item in discovered:
        fatwa = fetch_fatwa(item["site"], item["url"], item["query"])
        if fatwa:
            fatwas.append(fatwa)
    language = detect_user_language(question)
    answer = answer_from_fatwas(question, language, fatwas)
    await reply_in_chunks(update, clean_telegram_text(answer).rstrip() + "\n\n" + format_fatwa_sources(fatwas))


# -----------------------------------------------------------------------------
# App entry point
# -----------------------------------------------------------------------------


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")
    if not OPENAI_API_KEY:
        raise RuntimeError("Missing OPENAI_API_KEY in .env")
    if not PUBLIC_BOT and not ALLOWED_TELEGRAM_USER_ID:
        raise RuntimeError("Set ALLOWED_TELEGRAM_USER_ID in .env or set PUBLIC_BOT=true.")
    if not BRAVE_SEARCH_API_KEY:
        print("Optional Brave search is disabled. Add BRAVE_SEARCH_API_KEY to .env to enable /fatwa, /islamqa, and /islamweb.")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("all", all_command))
    app.add_handler(CommandHandler("allpref", all_pref_command))
    app.add_handler(CommandHandler("all_pref", all_pref_command))
    app.add_handler(CommandHandler("book", book_command))
    app.add_handler(CommandHandler(["fatwa", "islamqa", "islamweb"], fatwa_command))
    app.add_handler(
        CommandHandler(
            [
                "hanbali", "hanafi", "maliki", "shafi", "shafii", "usool",
                "hanbalipref", "hanafipref", "malikipref", "shafipref", "shafiipref", "usoolpref",
                "hanbali_pref", "hanafi_pref", "maliki_pref", "shafi_pref", "usool_pref",
            ],
            school_command,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_message))

    print("Four-School Fiqh Research Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
