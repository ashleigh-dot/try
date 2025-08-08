import asyncio
import csv
import random
import re
import base64
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

import aiohttp
from lxml import html

from cache import get_cached_result, store_result

# ===== Paths & Globals =====
BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "contractor_license_verification_database.csv"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# State name to 2-letter code map (covers 50 states)
STATE_NAME_TO_CODE = {
    "Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA","Colorado":"CO","Connecticut":"CT",
    "Delaware":"DE","Florida":"FL","Georgia":"GA","Hawaii":"HI","Idaho":"ID","Illinois":"IL","Indiana":"IN",
    "Iowa":"IA","Kansas":"KS","Kentucky":"KY","Louisiana":"LA","Maine":"ME","Maryland":"MD","Massachusetts":"MA",
    "Michigan":"MI","Minnesota":"MN","Mississippi":"MS","Missouri":"MO","Montana":"MT","Nebraska":"NE","Nevada":"NV",
    "New Hampshire":"NH","New Jersey":"NJ","New Mexico":"NM","New York":"NY","North Carolina":"NC","North Dakota":"ND",
    "Ohio":"OH","Oklahoma":"OK","Oregon":"OR","Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC",
    "South Dakota":"SD","Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT","Virginia":"VA","Washington":"WA",
    "West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY"
}

# ===== CSV -> STATE_CONFIGS =====
def _to_bool(val: str) -> bool:
    if val is None:
        return False
    return str(val).strip().lower() in {"true","yes","1","y"}

def load_state_configs() -> Dict[str, Dict[str, Any]]:
    configs: Dict[str, Dict[str, Any]] = {}
    if not CSV_PATH.exists():
        return configs
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            state_raw = (row.get("STATE") or "").strip()
            # Accept either full state name or 2-letter code in CSV
            state_code = STATE_NAME_TO_CODE.get(state_raw, state_raw[:2].upper())
            configs[state_code] = {
                "STATE": state_code,
                "AGENCY_NAME": (row.get("AGENCY_NAME") or "").strip(),
                "LICENSE_TYPE": (row.get("LICENSE_TYPE") or "").strip(),
                "VERIFICATION_URL": (row.get("VERIFICATION_URL") or "").strip(),
                "LICENSE_REGEX": (row.get("LICENSE_REGEX") or "").strip(),
                "EXAMPLE_LICENSE": (row.get("EXAMPLE_LICENSE") or "").strip(),
                "FORM_METHOD": (row.get("FORM_METHOD") or "GET").strip().upper(),
                "INPUT_FIELD_NAME": (row.get("INPUT_FIELD_NAME") or "").strip(),
                "SEARCH_BUTTON_ID": (row.get("SEARCH_BUTTON_ID") or "").strip(),
                "BUSINESS_NAME_XPATH": (row.get("BUSINESS_NAME_XPATH") or "").strip(),
                "STATUS_XPATH": (row.get("STATUS_XPATH") or "").strip(),
                "EXPIRES_XPATH": (row.get("EXPIRES_XPATH") or "").strip(),
                "REQUIRES_JAVASCRIPT": _to_bool(row.get("REQUIRES_JAVASCRIPT") or "false"),
                "ANTI_BOT_MEASURES": (row.get("ANTI_BOT_MEASURES") or "").strip(),
                "SPECIAL_NOTES": (row.get("SPECIAL_NOTES") or "").strip(),
            }
    return configs

STATE_CONFIGS: Dict[str, Dict[str, Any]] = load_state_configs()

# ===== Optional Playwright =====
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

# ===== Utilities expected by main.py =====
def get_system_status() -> Dict[str, Any]:
    return {
        "status": "operational",
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "total_states": len(STATE_CONFIGS),
        "last_loaded": datetime.utcnow().isoformat() + "Z"
    }

def normalize_license_number(state: str, license_number: str) -> str:
    if not license_number:
        return license_number
    num = license_number.strip().upper()
    # keep dots/dashes if part of format; but remove spaces
    num = re.sub(r"\s+", "", num)
    return num

def get_supported_states() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for code, cfg in STATE_CONFIGS.items():
        out[code] = {
            "type": cfg.get("LICENSE_TYPE") or "Professional License",
            "format": cfg.get("LICENSE_REGEX") or "Varies",
            "example": cfg.get("EXAMPLE_LICENSE") or "N/A"
        }
    return out

def get_state_info(state: str) -> Dict[str, Any]:
    code = state.upper()
    cfg = STATE_CONFIGS.get(code)
    if not cfg:
        return {"error": f"State {state} not supported"}
    return cfg

# ===== HTTP helpers =====
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
def _headers() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive"
    }

def _sleep_if_needed(config: Dict[str, Any]):
    notes = (config.get("ANTI_BOT_MEASURES") or "").lower()
    if "delay" in notes or "sleep" in notes:
        m = re.search(r"delay\s*(\d+)", notes)
        import time as _t
        sec = int(m.group(1)) if m else random.randint(1, 3)
        _t.sleep(sec)

def _validate_format_with_regex(regex: str, license_number: str) -> bool:
    if not regex:
        return True
    try:
        return re.match(regex, license_number) is not None
    except re.error:
        # bad regex in CSV; don't block user
        return True

# ===== Extraction helpers =====
def _xpath_try(tree, xpath_expr: str) -> Optional[str]:
    """Support multiple XPaths separated by '||'. Return first match text."""
    if not xpath_expr:
        return None
    for candidate in [s.strip() for s in xpath_expr.split("||") if s.strip()]:
        try:
            nodes = tree.xpath(candidate)
            if not nodes:
                continue
            node = nodes[0]
            if isinstance(node, str):
                val = node
            else:
                val = node.text_content()
            val = (val or "").strip()
            if val:
                return val
        except Exception:
            continue
    return None

async def _extract_with_xpath_from_text(html_text: str, cfg: Dict[str, Any]) -> Dict[str, Optional[str]]:
    try:
        tree = html.fromstring(html_text)
    except Exception:
        return {"business_name": None, "status": None, "expires": None}
    return {
        "business_name": _xpath_try(tree, cfg.get("BUSINESS_NAME_XPATH", "")),
        "status": _xpath_try(tree, cfg.get("STATUS_XPATH", "")),
        "expires": _xpath_try(tree, cfg.get("EXPIRES_XPATH", "")),
    }

# ===== Requests path for non-JS states =====
async def _scrape_with_requests(cfg: Dict[str, Any], license_number: str) -> Dict[str, Any]:
    url = cfg.get("VERIFICATION_URL")
    if not url:
        return {"status": "Unsupported", "message": "No verification URL", "verified": False}

    params = {}
    data = {}
    method = (cfg.get("FORM_METHOD") or "GET").upper()
    field = cfg.get("INPUT_FIELD_NAME") or ""

    if method == "GET":
        if field:
            params[field] = license_number
        else:
            params["license"] = license_number
    else:
        if field:
            data[field] = license_number
        else:
            data["license"] = license_number

    async with aiohttp.ClientSession(headers=_headers()) as session:
        # warm-up for cookies
        try:
            async with session.get(url, timeout=30):
                pass
        except Exception:
            pass

        _sleep_if_needed(cfg)

        try:
            if method == "GET":
                async with session.get(url, params=params, timeout=45) as resp:
                    text = await resp.text()
            else:
                async with session.post(url, data=data, timeout=45) as resp:
                    text = await resp.text()
        except Exception as e:
            return {"status": "Error", "message": f"HTTP error: {e}", "verified": False}

    extracted = await _extract_with_xpath_from_text(text, cfg)

    status = extracted.get("status") or "Unknown"
    business_name = extracted.get("business_name") or "Unknown"
    expires = extracted.get("expires") or None
    verified = (business_name not in (None, "", "Unknown")) or (status not in (None, "", "Unknown"))

    return {
        "state": cfg.get("STATE"),
        "license_number": license_number,
        "license_type": cfg.get("LICENSE_TYPE") or "Professional License",
        "verification_url": url,
        "business_name": business_name,
        "status": status,
        "expires": expires,
        "verified": verified,
        "method_used": "requests"
    }

# ===== Playwright path for JS states =====
async def _navigate_to_detail_if_needed(page, cfg: Dict[str, Any], license_number: str):
    """
    Generic 'click into details' navigator for result lists.
    Tries several common patterns:
      - link/text that exactly matches the license number
      - buttons/links labeled 'Details', 'More', 'View'
      - first link inside a results table/grid
    Safe to call multiple times; it no-ops if already on detail page.
    """
    try:
        # If detail markers already present (common phrases), skip
        detail_markers = [
            "License Summary", "License Details", "Detail", "Licensee Details",
            "CCB License Summary", "License Information", "License Number:"
        ]
        for marker in detail_markers:
            if await page.locator(f"text={marker}").count() > 0:
                return
    except Exception:
        pass

    # 1) Exact license number link
    try:
        sel = f"text='{license_number}'"
        if await page.locator(sel).count() > 0:
            await page.click(sel)
            await page.wait_for_load_state("networkidle", timeout=15000)
            return
    except Exception:
        pass

    # 2) Common 'Details' buttons/links
    for sel in ["a:has-text('Details')", "button:has-text('Details')",
                "a:has-text('More')", "button:has-text('More')",
                "a:has-text('View')", "button:has-text('View')"]:
        try:
            if await page.locator(sel).count() > 0:
                await page.click(sel)
                await page.wait_for_load_state("networkidle", timeout=15000)
                return
        except Exception:
            continue

    # 3) First link in a grid/table of results
    for sel in ["table a", "table tbody tr a", "[role='grid'] a", ".results a", ".table a"]:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
                return
        except Exception:
            continue

async def _scrape_with_playwright(cfg: Dict[str, Any], license_number: str) -> Dict[str, Any]:
    if not PLAYWRIGHT_AVAILABLE:
        return await _scrape_with_requests(cfg, license_number)

    from playwright.async_api import async_playwright

    url = cfg.get("VERIFICATION_URL")
    screenshot_file = SCREENSHOT_DIR / f"{cfg.get('STATE')}_{license_number}_{int(datetime.utcnow().timestamp())}.png"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
        context = await browser.new_context(user_agent=random.choice(USER_AGENTS))
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            await browser.close()
            return {"status": "Error", "message": f"Failed to open page: {e}", "verified": False}

        # Optional delay (anti-bot)
        notes = (cfg.get("ANTI_BOT_MEASURES") or "").lower()
        if "delay" in notes or "sleep" in notes:
            m = re.search(r"delay\s*(\d+)", notes)
            ms = (int(m.group(1)) if m else random.randint(1,3)) * 1000
            await page.wait_for_timeout(ms)

        # Fill input if provided
        field_name = cfg.get("INPUT_FIELD_NAME") or ""
        search_btn_id = cfg.get("SEARCH_BUTTON_ID") or ""

        filled = False
        if field_name:
            try:
                await page.fill(f"input[name=\"{field_name}\"]", license_number)
                filled = True
            except Exception:
                filled = False

        if not filled:
            for sel in ["input[name*=license i]", "input[id*=license i]", "input[name*=lic i]", "input[type='text']"]:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0:
                        await loc.fill(license_number)
                        filled = True
                        break
                except Exception:
                    continue

        # Submit
        clicked = False
        if search_btn_id:
            try:
                await page.click(f"#{search_btn_id}")
                clicked = True
            except Exception:
                clicked = False
        if not clicked:
            for sel in ["button:has-text('Search')", "input[type='submit']", "button[type='submit']", "button:has-text('Go')"]:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.click(sel)
                        clicked = True
                        break
                except Exception:
                    continue

        # Generic "click into details" for ALL states (if needed)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await _navigate_to_detail_if_needed(page, cfg, license_number)

        # Screenshot
        screenshot_b64 = None
        try:
            await page.screenshot(path=str(screenshot_file), full_page=True)
            with open(screenshot_file, "rb") as f:
                screenshot_b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            screenshot_b64 = None

        # Extract via page content + XPath
        content = await page.content()
        extracted = await _extract_with_xpath_from_text(content, cfg)
        await browser.close()

        business_name = extracted.get("business_name") or "Unknown"
        status = extracted.get("status") or "Unknown"
        expires = extracted.get("expires") or None
        verified = (business_name not in ("Unknown", None, "")) or (status not in ("Unknown", None, ""))

        return {
            "state": cfg.get("STATE"),
            "license_number": license_number,
            "license_type": cfg.get("LICENSE_TYPE") or "Professional License",
            "verification_url": url,
            "business_name": business_name,
            "status": status,
            "expires": expires,
            "verified": verified,
            "screenshot_data": screenshot_b64,
            "screenshot_path": str(screenshot_file),
            "method_used": "playwright"
        }

# ===== Public API (used by FastAPI) =====
async def verify_license(state: str, license_number: Optional[str] = None, business_name: Optional[str] = None) -> Dict[str, Any]:
    if not state or not license_number:
        raise ValueError("State and license_number are required")

    state_code = state.upper()
    cfg = STATE_CONFIGS.get(state_code)
    if not cfg:
        return {"status": "Unsupported", "message": f"State {state_code} not supported", "verified": False}

    norm_license = normalize_license_number(state_code, license_number)

    # SOFT validation: warn but DO NOT block scraping
    regex = cfg.get("LICENSE_REGEX") or ""
    format_valid = _validate_format_with_regex(regex, norm_license) if regex else True

    cache_key = f"{state_code}:{norm_license}"
    try:
        cached = get_cached_result(cache_key)
        if cached:
            cached["from_cache"] = True
            # preserve format_valid hint
            cached["format_valid"] = format_valid
            return cached
    except Exception:
        pass

    if cfg.get("REQUIRES_JAVASCRIPT"):
        result = await _scrape_with_playwright(cfg, norm_license)
    else:
        result = await _scrape_with_requests(cfg, norm_license)

    # include soft validation flag
    result["format_valid"] = format_valid

    try:
        store_result(cache_key, result)
    except Exception:
        pass

    return result

async def verify_batch(requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for i, req in enumerate(requests):
        if i > 0:
            await asyncio.sleep(2)
        try:
            res = await verify_license(req.get("state"), req.get("license_number"), req.get("business_name"))
            results.append(res)
        except Exception as e:
            results.append({"status": "Error", "message": str(e), "verified": False})
    return results

def validate_license_format(state: str, license_number: str) -> Dict[str, Any]:
    # Expose "soft" validation result (never blocks)
    code = state.upper()
    cfg = STATE_CONFIGS.get(code)
    if not cfg:
        return {"valid": False, "error": f"State {code} not supported"}
    regex = cfg.get("LICENSE_REGEX") or ""
    ok = _validate_format_with_regex(regex, license_number) if regex else True
    return {
        "valid": ok,
        "expected_format": regex or "Varies",
        "example": cfg.get("EXAMPLE_LICENSE") or "N/A"
    }
