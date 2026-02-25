#!/usr/bin/env python3
"""
UN Women Job Scraper
Scrapes the Oracle Cloud Candidate Experience site for UN Women job vacancies,
filters by grade/level, and outputs a valid RSS 2.0 feed.
"""

import hashlib
import logging
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.dom import minidom

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL = "https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs"
FEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unwomen_jobs.xml")
FEED_TITLE = "UN Women Job Vacancies"
FEED_LINK = BASE_URL
FEED_DESC = "List of vacancies at UN Women"
SELF_LINK = "https://cinfoposte.github.io/unwomen-jobs/unwomen_jobs.xml"
MAX_INCLUDED = 50
INITIAL_WAIT = 20  # seconds after page load

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Grade / level helpers
# ---------------------------------------------------------------------------

# Regex patterns for normalizing compact grade forms (e.g. P4 -> P-4)
COMPACT_GRADE_RE = re.compile(
    r'\b(P|D|G|SB|LSC|NO)\s*[-\u2010\u2011\u2012\u2013\u2014\u2015]?\s*(\d{1,2})\b',
    re.IGNORECASE,
)

INCLUDED_GRADES = {"P-1", "P-2", "P-3", "P-4", "P-5", "D-1", "D-2"}
EXCLUDED_PREFIXES_RE = re.compile(r'\b(G-\d{1,2}|NO-?[A-D]|SB-\d{1,2}|LSC-\d{1,2})\b', re.IGNORECASE)
CONSULTANT_RE = re.compile(r'\bconsultan(t|cy)\b', re.IGNORECASE)
INTERN_FELLOW_RE = re.compile(r'\b(internship|intern|fellowship|fellow)\b', re.IGNORECASE)


def normalize_text(text: str) -> str:
    """Normalize unicode dashes, strip whitespace, uppercase."""
    # Replace various unicode dashes with ASCII hyphen
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63\uFF0D]', '-', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def normalize_grades(text: str) -> str:
    """Expand compact grade forms: P4 -> P-4, LSC10 -> LSC-10, etc."""
    def _repl(m):
        prefix = m.group(1).upper()
        num = m.group(2)
        return f"{prefix}-{num}"
    return COMPACT_GRADE_RE.sub(_repl, text)


def detect_grades(text: str) -> set:
    """Return set of normalized grades found in text."""
    t = normalize_grades(normalize_text(text)).upper()
    found = set()
    for g in INCLUDED_GRADES:
        if g in t:
            found.add(g)
    for m in EXCLUDED_PREFIXES_RE.finditer(t):
        found.add(m.group(0).upper())
    return found


def should_include(title: str, details_text: str) -> tuple:
    """
    Apply filter rules. Returns (include: bool, reason: str).
    """
    combined = normalize_grades(normalize_text(f"{title} {details_text}")).upper()
    title_norm = normalize_grades(normalize_text(title)).upper()

    # 1) Consultant check
    if CONSULTANT_RE.search(combined):
        return False, "consultant/consultancy detected"

    # 2) Excluded grades
    for m in EXCLUDED_PREFIXES_RE.finditer(combined):
        return False, f"excluded grade: {m.group(0)}"

    # 3) Included grades
    for g in INCLUDED_GRADES:
        if g in combined:
            return True, f"included grade: {g}"

    # 4) Internship / fellowship
    if INTERN_FELLOW_RE.search(combined):
        return True, "internship/fellowship detected"

    # 5) Default exclude
    return False, "no matching grade or keyword"


# ---------------------------------------------------------------------------
# GUID generation (MD5-based, 16 digits, zero-padded)
# ---------------------------------------------------------------------------

def generate_numeric_id(url: str) -> str:
    hex_dig = hashlib.md5(url.encode()).hexdigest()
    return str(int(hex_dig[:16], 16) % 10000000000000000).zfill(16)


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def strip_xml_illegal(text: str) -> str:
    """Remove characters illegal in XML 1.0."""
    return re.sub(
        r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F]',
        '',
        text,
    )


def load_existing_feed(path: str) -> dict:
    """Parse existing RSS feed and return dict of link -> ET.Element items."""
    items = {}
    if not os.path.exists(path):
        return items
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        for item in root.iter("item"):
            link_el = item.find("link")
            if link_el is not None and link_el.text:
                items[link_el.text.strip()] = item
    except ET.ParseError:
        log.warning("Could not parse existing feed; starting fresh.")
    return items


def build_rss(items_data: list, existing_items: dict) -> str:
    """
    Build RSS 2.0 XML string.
    items_data: list of dicts with keys: title, link, description, pubDate
    existing_items: dict of link -> raw XML strings from previous feed
    """
    ATOM_NS = "http://www.w3.org/2005/Atom"
    DC_NS = "http://purl.org/dc/elements/1.1/"

    # Register prefixes so ElementTree uses "atom" / "dc" instead of "ns0" / "ns1"
    ET.register_namespace("atom", ATOM_NS)
    ET.register_namespace("dc", DC_NS)

    rss = ET.Element("rss", version="2.0")
    rss.set("xmlns:dc", DC_NS)
    # Don't manually set xmlns:atom — register_namespace + QName handles it

    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = FEED_TITLE
    ET.SubElement(channel, "link").text = FEED_LINK
    ET.SubElement(channel, "description").text = FEED_DESC
    ET.SubElement(channel, "language").text = "en"

    atom_link = ET.SubElement(channel, ET.QName(ATOM_NS, "link"))
    atom_link.set("href", SELF_LINK)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    ET.SubElement(channel, "pubDate").text = format_datetime(datetime.now(timezone.utc))

    # Collect all links from new items
    new_links = {d["link"] for d in items_data}

    # Add existing items that are NOT being replaced by new data
    for link, item_el in existing_items.items():
        if link not in new_links:
            channel.append(item_el)

    # Add new items
    for d in items_data:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = strip_xml_illegal(d["title"])
        ET.SubElement(item, "link").text = d["link"]
        # description placeholder — will be replaced with CDATA below
        desc_el = ET.SubElement(item, "description")
        desc_el.text = "__CDATA_PLACEHOLDER__"
        desc_el.set("__cdata__", strip_xml_illegal(d["description"]))
        guid_el = ET.SubElement(item, "guid")
        guid_el.set("isPermaLink", "false")
        guid_el.text = generate_numeric_id(d["link"])
        ET.SubElement(item, "pubDate").text = d.get("pubDate", format_datetime(datetime.now(timezone.utc)))
        source_el = ET.SubElement(item, "source")
        source_el.set("url", FEED_LINK)
        source_el.text = FEED_TITLE

    # Convert to string via minidom for pretty printing, then inject CDATA
    rough = ET.tostring(rss, encoding="unicode", xml_declaration=False)
    # Inject CDATA sections
    dom = minidom.parseString(rough)
    for desc_node in dom.getElementsByTagName("description"):
        cdata_val = desc_node.getAttribute("__cdata__")
        if cdata_val:
            # Clear existing children
            while desc_node.firstChild:
                desc_node.removeChild(desc_node.firstChild)
            desc_node.appendChild(dom.createCDATASection(cdata_val))
            desc_node.removeAttribute("__cdata__")
            # Remove the placeholder text attribute if set
        # Remove __cdata__ attribute from all description elements
        if desc_node.hasAttribute("__cdata__"):
            desc_node.removeAttribute("__cdata__")

    xml_str = dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    # Remove extra blank lines minidom adds
    xml_str = re.sub(r'\n\s*\n', '\n', xml_str)
    return xml_str


# ---------------------------------------------------------------------------
# Selenium scraper
# ---------------------------------------------------------------------------

def create_driver():
    """Create headless Chrome WebDriver."""
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=en-US")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    return driver


def scroll_and_load_more(driver, max_attempts=30):
    """
    Handle pagination: click 'Show More' / 'Load More' buttons or scroll
    to trigger infinite-scroll loading.
    """
    loaded_attempts = 0
    last_count = 0

    while loaded_attempts < max_attempts:
        # Try clicking any "Show More" / "Load More" button
        try:
            buttons = driver.find_elements(By.XPATH,
                "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
                "'abcdefghijklmnopqrstuvwxyz'), 'more') or "
                "contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
                "'abcdefghijklmnopqrstuvwxyz'), 'load')]"
            )
            clicked = False
            for btn in buttons:
                if btn.is_displayed() and btn.is_enabled():
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                        time.sleep(0.5)
                        btn.click()
                        log.info("Clicked 'Load More' / 'Show More' button.")
                        time.sleep(3)
                        clicked = True
                        break
                    except Exception:
                        pass
            if not clicked:
                # Scroll to bottom
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
        except Exception:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

        # Count current job cards
        cards = find_job_elements(driver)
        current_count = len(cards)
        log.info(f"Pagination attempt {loaded_attempts + 1}: {current_count} job cards found.")

        if current_count == last_count:
            # No new cards loaded, try one more scroll
            loaded_attempts += 1
            if loaded_attempts >= 3 and current_count == last_count:
                log.info("No new jobs loading; stopping pagination.")
                break
        else:
            loaded_attempts = 0  # Reset counter when new content appears
            last_count = current_count


def find_job_elements(driver):
    """
    Strategy A/B/C: Find job card elements using multiple fallback selectors.
    Returns list of web elements representing job listings.
    """
    selectors = [
        # Strategy A: Common Oracle HCM job card selectors
        "a[href*='/jobs/']",
        "a[href*='requisitionId']",
        # Strategy B: Job list item containers
        ".job-list-item",
        "[data-job-id]",
        "li[class*='job']",
        # Strategy C: Oracle-specific patterns
        ".x1bt",  # Oracle HCM job card class
        "[class*='requisition']",
        "[class*='JobCard']",
        "[class*='job-card']",
        # Broader: links within list items that look like job listings
        "div[role='list'] a",
        "ul[role='list'] a",
        "section a[href]",
    ]

    for sel in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            if elements:
                log.info(f"Found {len(elements)} elements with selector: {sel}")
                return elements
        except Exception:
            continue

    # Fallback: look for any anchor with long text that could be a job title
    try:
        anchors = driver.find_elements(By.TAG_NAME, "a")
        job_links = [a for a in anchors if len(a.text.strip()) > 10 and a.get_attribute("href")]
        if job_links:
            log.info(f"Fallback: found {len(job_links)} potential job links by text length.")
            return job_links
    except Exception:
        pass

    return []


def extract_job_url(element, driver) -> str:
    """Extract the job detail URL from a job element."""
    href = element.get_attribute("href")
    if href and ("jobs" in href or "requisition" in href.lower()):
        return href
    # Try to find a child link
    try:
        child_links = element.find_elements(By.TAG_NAME, "a")
        for link in child_links:
            h = link.get_attribute("href")
            if h and ("jobs" in h or "requisition" in h.lower()):
                return h
    except Exception:
        pass
    return href or ""


def extract_detail_text(driver, url: str, retries: int = 2) -> dict:
    """
    Navigate to a job detail page, extract metadata.
    Returns dict with: title, location, details_text, posting_date, closing_date, grade.
    """
    info = {
        "title": "",
        "location": "",
        "details_text": "",
        "posting_date": "",
        "closing_date": "",
        "grade": "",
    }

    for attempt in range(retries + 1):
        try:
            driver.get(url)
            time.sleep(5)

            # Wait for page content
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            # Title - try multiple selectors
            title = ""
            title_selectors = [
                "h1",
                "h2",
                "[class*='title']",
                "[class*='Title']",
                "[data-test*='title']",
            ]
            for sel in title_selectors:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in els:
                        t = el.text.strip()
                        if len(t) > 5:
                            title = t
                            break
                    if title:
                        break
                except Exception:
                    continue
            info["title"] = title

            # Full page text for grade detection and description
            body_text = driver.find_element(By.TAG_NAME, "body").text
            info["details_text"] = body_text

            # Try to find location
            try:
                loc_patterns = [
                    "//span[contains(text(),'Location')]/following-sibling::*",
                    "//div[contains(text(),'Location')]/following-sibling::*",
                    "//*[contains(@class,'location')]",
                    "//*[contains(@class,'Location')]",
                ]
                for pat in loc_patterns:
                    locs = driver.find_elements(By.XPATH, pat)
                    for loc in locs:
                        lt = loc.text.strip()
                        if lt and len(lt) > 2:
                            info["location"] = lt
                            break
                    if info["location"]:
                        break
            except Exception:
                pass

            # If location not found via selectors, try regex on body text
            if not info["location"]:
                loc_match = re.search(r'Location[:\s]+([^\n]+)', body_text)
                if loc_match:
                    info["location"] = loc_match.group(1).strip()

            # Extract grade from body text
            grades = detect_grades(body_text)
            info["grade"] = ", ".join(sorted(grades)) if grades else ""

            # Dates
            date_match = re.search(r'(?:Posted|Posting)\s*(?:Date)?[:\s]+([^\n]+)', body_text, re.IGNORECASE)
            if date_match:
                info["posting_date"] = date_match.group(1).strip()

            close_match = re.search(r'(?:Closing|Close|End)\s*(?:Date)?[:\s]+([^\n]+)', body_text, re.IGNORECASE)
            if close_match:
                info["closing_date"] = close_match.group(1).strip()

            return info

        except Exception as e:
            log.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
            if attempt < retries:
                time.sleep(3)

    return info


def scrape_jobs() -> list:
    """
    Main scraping function. Returns list of dicts for included jobs.
    """
    driver = create_driver()
    included_jobs = []
    stats = {"found_cards": 0, "opened_details": 0, "included": 0, "excluded": 0, "reasons": {}}

    try:
        log.info(f"Loading job listing page: {BASE_URL}")
        driver.get(BASE_URL)

        log.info(f"Waiting {INITIAL_WAIT}s for JS rendering...")
        time.sleep(INITIAL_WAIT)

        # Wait for job elements to appear
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/jobs/'], h1, h2"))
            )
        except Exception:
            log.warning("Timed out waiting for job elements; proceeding anyway.")

        # Handle pagination / load more
        scroll_and_load_more(driver, max_attempts=15)

        # Find job elements
        elements = find_job_elements(driver)
        stats["found_cards"] = len(elements)
        log.info(f"Total job elements found: {len(elements)}")

        # Collect unique job URLs
        job_urls = []
        seen_urls = set()
        for el in elements:
            url = extract_job_url(el, driver)
            if url and url not in seen_urls and url != BASE_URL:
                seen_urls.add(url)
                job_urls.append(url)

        log.info(f"Unique job URLs collected: {len(job_urls)}")

        # Load existing feed to check for duplicates
        existing_items = load_existing_feed(FEED_FILE)
        existing_links = set(existing_items.keys())
        log.info(f"Existing items in feed: {len(existing_links)}")

        # Visit each job detail page
        for i, url in enumerate(job_urls):
            if len(included_jobs) >= MAX_INCLUDED:
                log.info(f"Reached max {MAX_INCLUDED} included jobs. Stopping.")
                break

            log.info(f"Processing job {i + 1}/{len(job_urls)}: {url}")
            stats["opened_details"] += 1

            info = extract_detail_text(driver, url)
            title = info["title"]
            if not title or len(title) < 5:
                log.warning(f"Skipping job with short/empty title: '{title}'")
                continue

            details_text = info["details_text"]
            include, reason = should_include(title, details_text)

            if include:
                log.info(f"  INCLUDE: {title} ({reason})")
                stats["included"] += 1

                # Build description
                desc_parts = [f"UN Women has a vacancy for the position of {title}."]
                if info["location"]:
                    desc_parts.append(f"Location: {info['location']}.")
                if info["grade"]:
                    desc_parts.append(f"Level: {info['grade']}.")
                if info["closing_date"]:
                    desc_parts.append(f"Closing date: {info['closing_date']}.")
                description = " ".join(desc_parts)

                included_jobs.append({
                    "title": title,
                    "link": url,
                    "description": description,
                    "pubDate": format_datetime(datetime.now(timezone.utc)),
                })
            else:
                log.info(f"  EXCLUDE: {title} ({reason})")
                stats["excluded"] += 1
                stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1

    except Exception as e:
        log.error(f"Fatal error during scraping: {e}", exc_info=True)
    finally:
        driver.quit()

    log.info("=" * 60)
    log.info(f"Scraping summary:")
    log.info(f"  Cards found:     {stats['found_cards']}")
    log.info(f"  Details opened:  {stats['opened_details']}")
    log.info(f"  Included:        {stats['included']}")
    log.info(f"  Excluded:        {stats['excluded']}")
    for reason, count in sorted(stats["reasons"].items()):
        log.info(f"    - {reason}: {count}")
    log.info("=" * 60)

    return included_jobs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("Starting UN Women job scraper...")

    # Load existing feed items
    existing_items = load_existing_feed(FEED_FILE)
    existing_links = set(existing_items.keys())
    log.info(f"Loaded {len(existing_links)} existing items from feed.")

    # Scrape new jobs
    new_jobs = scrape_jobs()

    # Filter out jobs whose link is already in the feed
    truly_new = [j for j in new_jobs if j["link"] not in existing_links]
    log.info(f"New jobs to add: {len(truly_new)} (skipped {len(new_jobs) - len(truly_new)} duplicates)")

    if not truly_new and not existing_items:
        log.info("No jobs found and no existing feed. Writing empty scaffold.")
        xml_str = build_rss([], {})
    elif not truly_new:
        log.info("No new jobs to add. Updating pubDate only.")
        xml_str = build_rss([], existing_items)
    else:
        xml_str = build_rss(truly_new, existing_items)

    with open(FEED_FILE, "w", encoding="utf-8") as f:
        f.write(xml_str)

    log.info(f"Feed written to {FEED_FILE}")
    log.info("Done.")


if __name__ == "__main__":
    main()
