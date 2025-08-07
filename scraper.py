import asyncio
import csv
import os
import random
import time
from pathlib import Path

import requests
from lxml import html
from playwright.async_api import async_playwright

CACHE_DIR = Path("cache")
SCREENSHOT_DIR = Path("screenshots")
CACHE_DIR.mkdir(exist_ok=True)
SCREENSHOT_DIR.mkdir(exist_ok=True)

CSV_FILE = "contractor_license_verification_database.csv"
STATE_CONFIGS = {}

# Load CSV into STATE_CONFIGS dict
with open(CSV_FILE, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        STATE_CONFIGS[row["STATE"].strip().lower()] = row


def cache_get(key: str):
    cache_file = CACHE_DIR / f"{key}.txt"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    return None


def cache_set(key: str, value: str):
    cache_file = CACHE_DIR / f"{key}.txt"
    cache_file.write_text(value, encoding="utf-8")


async def fetch_with_playwright(config, license_number):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=random_user_agent())

        await page.goto(config["VERIFICATION_URL"], timeout=60000)

        # Fill form
        if config["INPUT_FIELD_NAME"] != "N/A":
            await page.fill(f'[name="{config["INPUT_FIELD_NAME"]}"]', license_number)
        if config["SEARCH_BUTTON_ID"] != "N/A":
            await page.click(f'#{config["SEARCH_BUTTON_ID"]}')

        await page.wait_for_timeout(3000)  # Let page load results

        # Extract data via XPath
        content = await page.content()
        tree = html.fromstring(content)

        business_name = extract_xpath(tree, config["BUSINESS_NAME_XPATH"])
        status = extract_xpath(tree, config["STATUS_XPATH"])
        expires = extract_xpath(tree, config["EXPIRES_XPATH"])

        # Screenshot
        screenshot_path = SCREENSHOT_DIR / f"{config['STATE']}_{license_number}.png"
        await page.screenshot(path=str(screenshot_path))

        await browser.close()

        return {
            "business_name": business_name,
            "status": status,
            "expiration": expires,
            "screenshot": str(screenshot_path)
        }


def fetch_with_requests(config, license_number):
    headers = {
        "User-Agent": random_user_agent(),
        "Accept-Language": "en-US,en;q=0.9",
    }
    session = requests.Session()
    session.headers.update(headers)

    if config["FORM_METHOD"].upper() == "GET":
        resp = session.get(config["VERIFICATION_URL"], params={config["INPUT_FIELD_NAME"]: license_number}, timeout=30)
    else:
        resp = session.post(config["VERIFICATION_URL"], data={config["INPUT_FIELD_NAME"]: license_number}, timeout=30)

    tree = html.fromstring(resp.text)

    business_name = extract_xpath(tree, config["BUSINESS_NAME_XPATH"])
    status = extract_xpath(tree, config["STATUS_XPATH"])
    expires = extract_xpath(tree, config["EXPIRES_XPATH"])

    screenshot_path = SCREENSHOT_DIR / f"{config['STATE']}_{license_number}.png"
    screenshot_path.write_text("Requests mode: No visual screenshot available", encoding="utf-8")

    return {
        "business_name": business_name,
        "status": status,
        "expiration": expires,
        "screenshot": str(screenshot_path)
    }


def extract_xpath(tree, xpath_expr):
    try:
        el = tree.xpath(xpath_expr)
        if not el:
            return None
        if isinstance(el, list):
            el = el[0]
        return el.strip() if hasattr(el, 'strip') else str(el)
    except Exception:
        return None


def random_user_agent():
    uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/117.0",
    ]
    return random.choice(uas)


async def verify_license(state: str, license_number: str):
    state_key = state.lower().strip()
    if state_key not in STATE_CONFIGS:
        return {"error": f"State '{state}' not found in database"}

    config = STATE_CONFIGS[state_key]

    cache_key = f"{state_key}_{license_number}"
    cached = cache_get(cache_key)
    if cached:
        return eval(cached)  # safe enough here, since we wrote it

    # Random delay to mimic human
    time.sleep(random.uniform(1.0, 3.0))

    if config["REQUIRES_JAVASCRIPT"].strip().lower() == "true":
        result = await fetch_with_playwright(config, license_number)
    else:
        result = fetch_with_requests(config, license_number)

    cache_set(cache_key, str(result))
    return result
