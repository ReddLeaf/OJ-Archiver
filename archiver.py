#type: ignore

"""
OJ Archiver
-----------
Archives every problem in a UP Diliman CS Online Judge exercise page as PDFs,
then merges them into a single PDF per exercise with an auto-generated
table of contents as the first page.

HOW IT WORKS
1. Run this script.
2. A Chrome window opens (controlled by the script).
3. YOU manually log in and navigate to the exercise list page
   (the page that looks like the "[CS 11 25.1] Lab Exercise 1" table).
4. Back in the terminal, press Enter when you're on that page.
5. The script scrapes the problem list, visits each problem, and saves
   it as an individual PDF into: OJ Archive/<Exercise Name>/<Problem Name>.pdf
6. It then merges all of those, in problem order (a, b, c, ...), into a
   single combined PDF -- with a table of contents as page 1 -- saved as:
   OJ Archive/<Exercise Name>.pdf

SETUP (run once in a terminal):
    pip install selenium webdriver-manager pypdf reportlab

Then run:
    python oj_archiver.py
"""

import os
import re
import io
import base64
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from reportlab.lib.pagesizes import letter as LETTER_PAGESIZE
from reportlab.pdfgen import canvas as reportlab_canvas


# ----------------------------------------------------------------------
# CONFIG — edit these if needed
# ----------------------------------------------------------------------

# Where archives get saved. A subfolder per exercise is created inside this
# for the individual problem PDFs; the merged PDF is saved alongside it.
ARCHIVE_ROOT = os.path.join(os.getcwd(), "OJ Archive")

# If True (default): saves each problem as its own PDF in
# "OJ Archive/<Exercise Name>/", AND creates the merged PDF.
# If False: individual problem PDFs are never written to disk at all --
# they're merged directly from memory, and only the final merged PDF
# ("OJ Archive/<Exercise Name>.pdf") is saved.
KEEP_INDIVIDUAL_PDFS = False

# Persistent Chrome profile so your login can survive between runs.
CHROME_PROFILE_DIR = os.path.join(os.getcwd(), ".oj_chrome_profile")

# Seconds to wait after loading a problem page before printing to PDF.
# Increase this if pages are slow to render (e.g. LaTeX/MathJax content).
PAGE_LOAD_WAIT = 2.0

# Scale factor applied when printing to PDF (1.0 = 100%, same as normal
# print). If content (wide tables, code blocks) is still getting cut off
# at the page edge, try lowering this to 0.8 or 0.7.
PDF_SCALE = 0.9

# CSS selectors for elements you want EXCLUDED from the saved PDF, e.g.
# navigation bars, sidebars, footers. Add as many as you need -- each one
# gets hidden (display: none) right before printing.
HIDE_SELECTORS = [
    "#navigation",
    "#contest-info",
    "footer"
]

# --- Table of contents layout settings ---
TOC_FONT = "Helvetica"
TOC_FONT_SIZE = 12
TOC_TITLE_FONT_SIZE = 20
TOC_LINE_HEIGHT = 18
TOC_MARGIN = 50
TOC_PAGE_NUM_COLUMN_WIDTH = 50  # reserved space on the right for page numbers


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    """Make a string safe to use as a file/folder name."""
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    return name.strip()

def extract_display_name(problem_text: str) -> str:
    """
    Given a problem row's raw text like '[CS 11 25.1] Lab 1a – Full Names',
    strip the leading course-code bracket and return the rest, e.g.
    'Lab 1a – Full Names'. This is used as both the saved filename and the
    table-of-contents entry, so it should be the full, human-readable name.
    """
    cleaned = re.sub(r'^\[.*?\]\s*', '', problem_text).strip()
    return cleaned if cleaned else problem_text.strip()


def setup_driver():
    options = Options()
    options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--start-maximized")
    # NOTE: not headless -- you need to see the window to log in / navigate.
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def hide_elements_for_print(driver):
    """Injects CSS to hide any elements listed in HIDE_SELECTORS."""
    if not HIDE_SELECTORS:
        return
    css_rules = ", ".join(HIDE_SELECTORS)
    script = f"""
        var style = document.createElement('style');
        style.id = 'oj-archiver-hide-style';
        style.innerHTML = `{css_rules} {{ display: none !important; }}`;
        document.head.appendChild(style);
    """
    driver.execute_script(script)


def get_pdf_bytes_for_current_page(driver) -> bytes:
    """Use Chrome DevTools Protocol to print the current page to PDF, and
    return the raw PDF bytes (without writing anything to disk)."""
    driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": "print"})
    hide_elements_for_print(driver)

    result = driver.execute_cdp_cmd("Page.printToPDF", {
        "printBackground": True,
        "paperWidth": 8.5,
        "paperHeight": 11,
        "marginTop": 0.4,
        "marginBottom": 0.4,
        "marginLeft": 0.4,
        "marginRight": 0.4,
        "scale": PDF_SCALE,
        "preferCSSPageSize": False,
    })
    return base64.b64decode(result["data"])


# ----------------------------------------------------------------------
# TABLE OF CONTENTS + MERGE
# ----------------------------------------------------------------------

def wrap_text(text, font_name, font_size, max_width, c):
    """Wrap text to fit within max_width, using the canvas to measure it."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if c.stringWidth(trial, font_name, font_size) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def generate_toc_pdf(entries, output_path, exercise_title):
    """
    entries: list of (display_name, page_num) in final display order.
    Writes a table-of-contents PDF to output_path. Returns
    (total_pages, link_boxes), where link_boxes is a list of
    (toc_page_index, rect, target_page_num) -- one entry per drawn line,
    describing a clickable rectangle on a given TOC page that should link
    to target_page_num in the final merged document.
    """
    page_width, page_height = LETTER_PAGESIZE
    max_text_width = page_width - 2 * TOC_MARGIN - TOC_PAGE_NUM_COLUMN_WIDTH

    c = reportlab_canvas.Canvas(output_path, pagesize=LETTER_PAGESIZE)

    # Pre-wrap all entry titles -- this determines pagination, and is done
    # once up front so page numbers (added later) don't affect line wrapping.
    wrapped_entries = []
    for display_name, page_num in entries:
        lines = wrap_text(display_name, TOC_FONT, TOC_FONT_SIZE,
                           max_text_width, c)
        wrapped_entries.append((lines, page_num))

    usable_height_first_page = page_height - TOC_MARGIN - 60
    usable_height_other_page = page_height - 2 * TOC_MARGIN
    lines_per_first_page = max(1, int(usable_height_first_page // TOC_LINE_HEIGHT))
    lines_per_other_page = max(1, int(usable_height_other_page // TOC_LINE_HEIGHT))

    def start_page(is_first):
        if is_first:
            c.setFont("Helvetica-Bold", TOC_TITLE_FONT_SIZE)
            c.drawString(TOC_MARGIN, page_height - TOC_MARGIN, exercise_title)
            c.setFont(TOC_FONT, TOC_FONT_SIZE)
            c.setFont("Helvetica-Bold", TOC_FONT_SIZE)
            c.drawString(TOC_MARGIN, page_height - TOC_MARGIN - 30, "Table of Contents")
            c.setFont(TOC_FONT, TOC_FONT_SIZE)
            return page_height - TOC_MARGIN - 60
        else:
            c.setFont(TOC_FONT, TOC_FONT_SIZE)
            return page_height - TOC_MARGIN

    y = start_page(True)
    lines_on_page = 0
    max_lines_this_page = lines_per_first_page
    total_pages = 1
    toc_page_index = 0
    link_boxes = []

    for lines, page_num in wrapped_entries:
        for i, line_text in enumerate(lines):
            if lines_on_page >= max_lines_this_page:
                c.showPage()
                total_pages += 1
                toc_page_index += 1
                y = start_page(False)
                max_lines_this_page = lines_per_other_page
                lines_on_page = 0
            c.drawString(TOC_MARGIN, y, line_text)
            if i == 0:
                c.drawRightString(page_width - TOC_MARGIN, y, str(page_num))
            # Clickable rect covering the full row (text + page number column)
            rect = (TOC_MARGIN - 4, y - 4, page_width - TOC_MARGIN + 4, y + TOC_FONT_SIZE + 3)
            link_boxes.append((toc_page_index, rect, page_num))
            y -= TOC_LINE_HEIGHT
            lines_on_page += 1

    c.save()
    return total_pages, link_boxes


def open_pdf_reader(source):
    """source is either a filepath (str) or raw PDF bytes -- return a
    PdfReader either way, so callers don't need to care which."""
    if isinstance(source, (bytes, bytearray)):
        return PdfReader(io.BytesIO(source))
    return PdfReader(source)


def merge_with_toc(ordered_problems, exercise_title, output_path):
    """
    ordered_problems: list of (display_name, source) in final order, where
    source is either a filepath (str) or raw PDF bytes.
    Builds a table of contents, then merges TOC + all problem PDFs into
    a single file at output_path.
    """
    # Get each problem PDF's page count.
    page_counts = []
    for display_name, source in ordered_problems:
        try:
            page_counts.append(len(open_pdf_reader(source).pages))
        except Exception as e:
            print(f"    [!] Could not read '{display_name}' ({e}); assuming 1 page.")
            page_counts.append(1)

    toc_temp_path = output_path + ".toc_temp.pdf"

    # Pass 1: generate a throwaway TOC (page numbers as placeholders) just
    # to find out how many pages the TOC itself will occupy.
    placeholder_entries = [(name, 0) for name, _ in ordered_problems]
    toc_page_count, _ = generate_toc_pdf(placeholder_entries, toc_temp_path, exercise_title)

    # Now compute each problem's real starting page number, offset by the
    # number of TOC pages.
    real_entries = []
    running_page = toc_page_count + 1
    for (display_name, _), count in zip(ordered_problems, page_counts):
        real_entries.append((display_name, running_page))
        running_page += count

    # Pass 2: regenerate the TOC with correct page numbers (same wrapping/
    # pagination as pass 1, since only the title text affects that) and
    # grab the clickable-rect info this time.
    _, link_boxes = generate_toc_pdf(real_entries, toc_temp_path, exercise_title)

    # Merge: TOC pages, then each problem's pages in order.
    writer = PdfWriter()
    for page in PdfReader(toc_temp_path).pages:
        writer.add_page(page)
    for display_name, source in ordered_problems:
        for page in open_pdf_reader(source).pages:
            writer.add_page(page)

    # Attach a clickable link annotation over each TOC row, pointing to the
    # corresponding problem's first page in the merged document.
    for toc_page_index, rect, target_page_num in link_boxes:
        link = Link(
            rect=rect,
            target_page_index=target_page_num - 1,  # pypdf pages are 0-indexed
        )
        writer.add_annotation(page_number=toc_page_index, annotation=link)

    with open(output_path, "wb") as f:
        writer.write(f)

    os.remove(toc_temp_path)


# ----------------------------------------------------------------------
# MAIN SCRAPING LOGIC
# ----------------------------------------------------------------------

def scrape_problem_list(driver):
    """
    Scrapes the currently loaded exercise list page.
    Returns (exercise_title, [(letter, problem_url, problem_title), ...])

    exercise_title is used as-is (not split into category/number), since
    some exercises share a category+number but differ by suffix, e.g.
    "HOPE 2 (Session A)" vs "HOPE 2 (Session C)" -- these need to stay
    distinct rather than both collapsing into "HOPE 2".
    """
    page_title = ""
    try:
        heading = driver.find_element("css selector", "div.page-title h2")
        page_title = heading.text.strip()
    except Exception:
        page_title = driver.title.strip()
        if not page_title:
            try:
                heading = driver.find_element("css selector", "h1, h2")
                page_title = heading.text.strip()
            except Exception:
                page_title = ""

    # Strip a trailing "5/8 solved" (or similar "X/Y solved") if present.
    page_title = re.sub(r'\s*\d+/\d+\s*solved\s*$', '', page_title,
                         flags=re.IGNORECASE).strip()
    
    # Uncomment this and comment the other one to strip 
    # the leading bracketed course code, e.g. "[CS 11 25.1] ".
    # exercise_title = re.sub(r'^\[.*?\]\s*', '', page_title).strip()
    exercise_title = page_title

    links = driver.find_elements("css selector", "td.problem a")

    problems = []
    seen_urls = set()
    for link in links:
        text = link.text.strip()
        href = link.get_attribute("href")
        if not text or not href:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)
        problems.append((href, text))
 
    return exercise_title, problems


def main():
    os.makedirs(ARCHIVE_ROOT, exist_ok=True)

    print("Launching Chrome...")
    driver = setup_driver()

    print("\nA Chrome window has opened.")
    print("1. Log in if needed.")
    print("2. Navigate to the exercise list page (e.g. 'Lab Exercise 1').")
    input("3. Once you're on that page, press Enter here to continue...\n")

    exercise_title, problems = scrape_problem_list(driver)

    if not problems:
        print("No problems found on this page. The CSS selector in "
              "scrape_problem_list() may need adjusting for this site's "
              "HTML structure. Aborting.")
        driver.quit()
        return

    if not exercise_title:
        print(f"Could not read an exercise title from this page "
              f"(tab title was '{driver.title}').")
        exercise_title = input("Enter the exercise name manually "
                                "(e.g. 'HOPE 2 (Session A)'): ").strip()

    folder_name = sanitize_filename(exercise_title)

    if KEEP_INDIVIDUAL_PDFS:
        exercise_dir = os.path.join(ARCHIVE_ROOT, folder_name)
        os.makedirs(exercise_dir, exist_ok=True)
        print(f"\nFound {len(problems)} problem(s) under '{exercise_title}'.")
        print(f"Saving individual PDFs into: {exercise_dir}\n")
    else:
        print(f"\nFound {len(problems)} problem(s) under '{exercise_title}'.")
        print("KEEP_INDIVIDUAL_PDFS is False -- problems will be merged "
              "directly from memory; no individual PDFs will be saved.\n")

    ordered_problems = []  # (display_name, filepath-or-bytes), in a/b/c order

    for url, raw_title in problems:
        display_name = extract_display_name(raw_title)

        print(f"  - {display_name}")
        driver.get(url)
        time.sleep(PAGE_LOAD_WAIT)

        try:
            pdf_bytes = get_pdf_bytes_for_current_page(driver)
            if KEEP_INDIVIDUAL_PDFS:
                filename = sanitize_filename(display_name) + ".pdf"
                filepath = os.path.join(exercise_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(pdf_bytes)
                ordered_problems.append((display_name, filepath))
            else:
                ordered_problems.append((display_name, pdf_bytes))
        except Exception as e:
            print(f"    [!] Failed to save '{display_name}': {e}")

    driver.quit()

    if not ordered_problems:
        print("\nNo PDFs were saved successfully; skipping merge.")
        return

    print(f"\nMerging {len(ordered_problems)} PDF(s) with a table of contents...")
    merged_path = os.path.join(ARCHIVE_ROOT, folder_name + ".pdf")
    merge_with_toc(ordered_problems, exercise_title, merged_path)

    print(f"\nDone! Merged PDF saved to: {merged_path}")


if __name__ == "__main__":
    main()