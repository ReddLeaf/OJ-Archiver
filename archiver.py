#type: ignore

"""
OJ Archiver
-----------
Archives every problem in OJ as a PDF.

HOW IT WORKS
1. Run this script.
2. A Chrome window opens (controlled by the script).
3. Manually log in and navigate to a problems list page
4. Back in the terminal, press Enter when you're on that page.
5. The script scrapes the problem list, visits each problem, and saves
   it as a PDF into: OJ Archive/<Exercise Name>/<filename>.pdf

NAMING CONVENTION
- Practice N        -> Na, Nb, Nc, ...
- Lab Exercise N     -> lNa, lNb, lNc, ...
- Mock HOPE N        -> mhNa, mhNb, mhNc, ...
Edit CATEGORY_PREFIXES below if you need to add/change a category.

SETUP:
    uv sync
    .venv/Scripts/activate.ps1 (Windows) or source .venv/bin/activate (Linux)

Then run:
    py archiver.py
    yes, shiori reference
"""

import os
import re
import base64
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


# ----------------------------------------------------------------------
# CONFIG — edit these if needed
# ----------------------------------------------------------------------

# Where archives get saved. A subfolder per exercise is created inside this.
ARCHIVE_ROOT = os.path.join(os.getcwd(), "OJ Archive")

# Persistent Chrome profile so your login can survive between runs.
CHROME_PROFILE_DIR = os.path.join(os.getcwd(), ".oj_chrome_profile")

# Maps a category name (as it appears in the page title, lowercased) to the
# filename prefix used before the number+letter, e.g. "lab exercise" -> "l"
# gives filenames like l1a, l1b, ...
CATEGORY_PREFIXES = {
    "practice": "",
    "lab exercise": "l",
    "mock hope": "mh",
}

# Seconds to wait after loading a problem page before printing to PDF.
# Increase this if pages are slow to render (e.g. LaTeX/MathJax content).
PAGE_LOAD_WAIT = 2.0

# Scale factor applied when printing to PDF (1.0 = 100%, same as normal
# print). If content (wide tables, code blocks) is still getting cut off
# at the page edge, try lowering this to 0.8 or 0.7.
PDF_SCALE = 0.9


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------

def sanitize_folder_name(name: str) -> str:
    """Make a string safe to use as a folder name."""
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    return name.strip()


def parse_page_title(title: str):
    """
    Given a title like '[CS 11 25.1] Lab Exercise 1', return
    ('Lab Exercise', '1'). Falls back to (title, '') if no trailing number.
    """
    # Strip any leading bracketed course code, e.g. "[CS 11 25.1] "
    cleaned = re.sub(r'^\[.*?\]\s*', '', title).strip()
    match = re.match(r'^(.*?)\s+(\d+)\s*$', cleaned)
    if match:
        return match.group(1).strip(), match.group(2)
    return cleaned, ""


def get_prefix_for_category(category: str) -> str:
    """Look up the filename prefix for a category, case-insensitively."""
    key = category.strip().lower()
    if key in CATEGORY_PREFIXES:
        return CATEGORY_PREFIXES[key]
    # Fuzzy fallback: substring match (e.g. "Lab Exercise" vs "lab exercises")
    for cat_key, prefix in CATEGORY_PREFIXES.items():
        if cat_key in key or key in cat_key:
            return prefix
    print(f"  [!] Unknown category '{category}', no prefix configured. "
          f"Add it to CATEGORY_PREFIXES. Using '' as prefix.")
    return ""


def extract_number_letter(problem_text: str):
    """
    Given a problem row's text like 'Lab 1a - Full Names', extract the
    trailing number+letter code, e.g. ('1', 'a'). Returns (None, None) if
    no match is found.
    """
    match = re.search(r'(\d+)\s*([a-zA-Z])\s*[–\-‐‑—]', problem_text)
    if match:
        return match.group(1), match.group(2).lower()
    return None, None


def setup_driver():
    options = Options()
    options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--start-maximized")
    # NOTE: not headless -- you need to see the window to log in / navigate.
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def save_page_as_pdf(driver, filepath: str):
    """Use Chrome DevTools Protocol to print the current page to PDF."""
    # Force Chrome to render using @media print rules, same as the real
    # Ctrl+P dialog would -- this alone fixes a lot of "cut off" issues,
    # since without it some pages render using screen (not print) CSS.
    driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": "print"})

    result = driver.execute_cdp_cmd("Page.printToPDF", {
        "printBackground": True,
        # Explicit US Letter size (in inches) instead of letting the page's
        # own CSS decide -- prevents wide tables/code blocks from getting
        # sliced at the page edge.
        "paperWidth": 8.5,
        "paperHeight": 11,
        "marginTop": 0.4,
        "marginBottom": 0.4,
        "marginLeft": 0.4,
        "marginRight": 0.4,
        # Shrinks content slightly so wide elements (tables, code) are more
        # likely to fit within the page width instead of being clipped.
        # Lower this (e.g. 0.7) if things are still getting cut off.
        "scale": PDF_SCALE,
        "preferCSSPageSize": False,
    })
    data = base64.b64decode(result["data"])
    with open(filepath, "wb") as f:
        f.write(data)


# ----------------------------------------------------------------------
# MAIN SCRAPING LOGIC
# ----------------------------------------------------------------------

def scrape_problem_list(driver):
    """
    Scrapes the currently loaded exercise list page.
    Returns (category, number, [(letter, problem_url, problem_title), ...])

    ASSUMPTIONS (adjust the selectors below if your site differs):
    - The page <title> or an <h1>/<h2>-like heading contains something like
      "[CS 11 25.1] Lab Exercise 1"
    - Each problem is a hyperlink (<a>) somewhere in a table, whose visible
      text contains the problem code, e.g. "[CS 11 25.1] Lab 1a - Full Names"
    """
    # --- Get the exercise title ---
    # Try the page <title> tag first; fall back to the first heading found.
    page_title = ""
    try:
        heading = driver.find_element("css selector", "div.page-title h2")
        page_title = heading.text.strip()
    except Exception:
        # Fallback: browser tab title, or any h1/h2 on the page.
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

    category, number = parse_page_title(page_title)

    # --- Find all problem links ---
    # This grabs every <a> tag on the page and filters down to the ones
    # that look like problem rows (i.e. contain a number+letter code).
    # ADJUST THIS SELECTOR if problems live in a specific table, e.g.:
    #   driver.find_elements("css selector", "table.problem-list a")
    links = driver.find_elements("css selector", "a")

    problems = []
    seen_urls = set()
    for link in links:
        text = link.text.strip()
        href = link.get_attribute("href")
        if not text or not href:
            continue
        num, letter = extract_number_letter(text)
        if num is None:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)
        problems.append((letter, href, text))

    # Sort by letter so files are processed in order (a, b, c, ...)
    problems.sort(key=lambda p: p[0])

    return category, number, problems


def main():
    os.makedirs(ARCHIVE_ROOT, exist_ok=True)

    print("Launching Chrome...")
    driver = setup_driver()

    print("\nA Chrome window has opened.")
    print("1. Log in if needed.")
    print("2. Navigate to the exercise list page (e.g. 'Lab Exercise 1').")
    input("3. Once you're on that page, press Enter here to continue...\n")

    category, number, problems = scrape_problem_list(driver)

    if not problems:
        print("No problems found on this page. The CSS selector in "
              "scrape_problem_list() may need adjusting for this site's "
              "HTML structure. Aborting.")
        driver.quit()
        return

    if not category or not number:
        print(f"Could not confidently parse category/number from page "
              f"title ('{driver.title}').")
        category = input("Enter the category manually (e.g. 'Lab Exercise'): ").strip()
        number = input("Enter the exercise number (e.g. '1'): ").strip()

    prefix = get_prefix_for_category(category)
    folder_name = sanitize_folder_name(f"{category} {number}")
    exercise_dir = os.path.join(ARCHIVE_ROOT, folder_name)
    os.makedirs(exercise_dir, exist_ok=True)

    print(f"\nFound {len(problems)} problem(s) under '{category} {number}'.")
    print(f"Saving into: {exercise_dir}\n")

    for letter, url, title in problems:
        filename = f"{prefix}{number}{letter}.pdf"
        filepath = os.path.join(exercise_dir, filename)

        print(f"  - {title}  ->  {filename}")
        driver.get(url)
        time.sleep(PAGE_LOAD_WAIT)

        try:
            save_page_as_pdf(driver, filepath)
        except Exception as e:
            print(f"    [!] Failed to save '{title}': {e}")

    print("\nDone! Closing browser.")
    driver.quit()


if __name__ == "__main__":
    main()
