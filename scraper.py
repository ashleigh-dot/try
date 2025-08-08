import asyncio
import csv
import random
import re
import base64
import os
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

def _resolve_state_code(state_input: str) -> Optional[str]:
    """Accept 'OR', 'or', 'Oregon', ' oregon  ' etc. and return 'OR'."""
    if not state_input:
        return None
    s = state_input.strip()
    if len(s) > 2:
        return STATE_NAME_TO_CODE.get(s.title())
    return s[:2].upper()

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

# Allow disabling PW at runtime for "safe mode"
if os.getenv("DISABLE_PLAYWRIGHT") == "1":
    PLAYWRIGHT_AVAILABLE = False

# Updated Oregon extraction function for scraper.py
# Replace the extract_oregon_business_name function with this:

def extract_oregon_business_name(html_content: str) -> str:
    """Extract business name from Oregon CCB page structure"""
    try:
        tree = html.fromstring(html_content)
        
        # Method 1: Extract from the H1 heading - "CCB License Summary: BUSINESS NAME"
        h1_elements = tree.xpath("//h1")
        for h1 in h1_elements:
            text = h1.text_content().strip()
            print(f"DEBUG: H1 text found: '{text}'")  # Debug line
            
            # Look for the pattern "CCB License Summary:" followed by business name
            if 'CCB License Summary:' in text:
                # Split on the colon and get everything after it
                parts = text.split('CCB License Summary:', 1)
                if len(parts) > 1:
                    business_name = parts[1].strip()
                    if business_name and len(business_name) > 2:
                        print(f"DEBUG: Extracted business name from H1: '{business_name}'")
                        return business_name
        
        # Method 2: Look for the business name as the next text element after the H1
        # Sometimes it's in a separate element right after the heading
        h1_summary = tree.xpath("//h1[contains(text(), 'CCB License Summary')]")
        if h1_summary:
            # Get all text nodes that follow the summary heading
            following_text = tree.xpath("//h1[contains(text(), 'CCB License Summary')]/following::text()[normalize-space()]")
            for i, text in enumerate(following_text[:5]):  # Check first 5 text nodes
                text = text.strip()
                print(f"DEBUG: Following text {i}: '{text}'")
                
                # Skip common non-business text
                if (text and len(text) > 3 and 
                    not text.lower().startswith(('about', 'license', 'status', 'first', 'unpaid', 'complaint', 'disciplinary', 'administrative')) and
                    not text.isdigit()):
                    
                    # Check if it looks like a business name
                    if (any(word in text.upper() for word in ['COMPANY', 'INC', 'LLC', 'CORP', 'CO', 'CONSTRUCTION', 'CONTRACTOR', '&']) or
                        (len(text) > 10 and all(c.isalpha() or c.isspace() or c in '&-.' for c in text))):
                        print(f"DEBUG: Found business name in following text: '{text}'")
                        return text
        
        # Method 3: Look for any text that contains business-like patterns
        all_text_nodes = tree.xpath("//text()[normalize-space()]")
        for text in all_text_nodes:
            text = text.strip()
            if (len(text) > 5 and 
                any(word in text.upper() for word in ['COMPANY INC', 'COMPANY LLC', '& COMPANY']) and
                not any(skip in text.upper() for skip in ['BOARD', 'STATE', 'OREGON', 'CCB', 'LICENSE', 'SUMMARY'])):
                print(f"DEBUG: Found business name via pattern matching: '{text}'")
                return text
        
        # Method 4: Look specifically for "B & K COMPANY INC" pattern
        business_patterns = [
            r'\b[A-Z]\s*&\s*[A-Z]\s+COMPANY\s+INC\b',
            r'\b[A-Z\s&]+COMPANY\s+INC\b',
            r'\b[A-Z][A-Z\s&]+(?:COMPANY|INC|LLC|CORP)\b'
        ]
        
        page_text = tree.text_content()
        for pattern in business_patterns:
            matches = re.findall(pattern, page_text)
            for match in matches:
                match = match.strip()
                if (len(match) > 5 and 
                    not any(skip in match.upper() for skip in ['BOARD', 'STATE', 'OREGON', 'CCB', 'LICENSE'])):
                    print(f"DEBUG: Found business name via regex: '{match}'")
                    return match
                    
        print("DEBUG: No business name found, returning fallback")
        return "License Search"  # Fallback
        
    except Exception as e:
        print(f"Oregon business name extraction error: {e}")
        return "Error extracting business name"

# Also update the _extract_with_xpath_from_text function to add debug info:
async def _extract_with_xpath_from_text(html_text: str, cfg: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Extract data with special Oregon handling"""
    
    # Special case for Oregon
    if cfg.get("STATE", "").upper() == "OR":
        print("DEBUG: Processing Oregon page")
        business_name = extract_oregon_business_name(html_text)
        print(f"DEBUG: Final Oregon business name: '{business_name}'")
        
        # Extract status and other info normally
        try:
            tree = html.fromstring(html_text)
            
            # Try multiple XPath patterns for status
            status_xpaths = [
                "//td[normalize-space(text())='Status:']/following-sibling::td[1]/text()",
                "//td[contains(text(),'Status')]/following-sibling::td[1]/text()",
                "//text()[contains(.,'Not Active') or contains(.,'Active') or contains(.,'Expired')][normalize-space()][1]"
            ]
            
            status = None
            for xpath in status_xpaths:
                try:
                    nodes = tree.xpath(xpath)
                    if nodes:
                        status = nodes[0].strip()
                        if status:
                            break
                except:
                    continue
            
            # Try to find the expiration/first licensed date
            expires_xpaths = [
                "//td[normalize-space(text())='First Licensed:']/following-sibling::td[1]/text()",
                "//td[contains(text(),'First Licensed')]/following-sibling::td[1]/text()"
            ]
            
            expires = None
            for xpath in expires_xpaths:
                try:
                    nodes = tree.xpath(xpath)
                    if nodes:
                        expires = nodes[0].strip()
                        if expires:
                            break
                except:
                    continue
            
            print(f"DEBUG: Oregon status: '{status}', expires: '{expires}'")
            
            return {
                "business_name": business_name,
                "status": status or "Unknown",
                "expires": f"First Licensed: {expires}" if expires else None
            }
        except Exception as e:
            print(f"DEBUG: Oregon extraction error: {e}")
            return {
                "business_name": business_name,
                "status": "Unknown", 
                "expires": None
            }
    def _apply_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    st = cfg.get("STATE")
    if not st or st not in OVERRIDES:
        return cfg
    out = dict(cfg)
    for k, v in OVERRIDES[st].items():
        if v is not None:
            out[k] = v
    return out
    # Standard extraction for other states (unchanged)
    try:
        tree = html.fromstring(html_text)
    except Exception:
        return {"business_name": None, "status": None, "expires": None}
    
    return {
        "business_name": _xpath_try(tree, cfg.get("BUSINESS_NAME_XPATH", "")),
        "status": _xpath_try(tree, cfg.get("STATUS_XPATH", "")),
        "expires": _xpath_try(tree, cfg.get("EXPIRES_XPATH", "")),
    }

# ---- Hard overrides for brittle portals (used if CSV is wrong) ----
# In your scraper.py, update the OVERRIDES section to force Oregon to use Playwright:

OVERRIDES: Dict[str, Dict[str, Any]] = {
    # Oregon CCB — requires click-through from search results to detail page
    "OR": {
        "REQUIRES_JAVASCRIPT": True,  # Force Playwright for Oregon
        "BUSINESS_NAME_XPATH": "//h1[contains(text(),'CCB License Summary:')]/text() | //h1/following::text()[contains(.,'COMPANY') or contains(.,'INC')][1]",
        "STATUS_XPATH": "//td[normalize-space(text())='Status:']/following-sibling::td[1]/text()",
        "EXPIRES_XPATH": "//td[normalize-space(text())='First Licensed:']/following-sibling::td[1]/text()",
    },
}
# Add this function to your scraper.py (put it after the OVERRIDES definition):

def _apply_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    st = cfg.get("STATE")
    if not st or st not in OVERRIDES:
        return cfg
    out = dict(cfg)
    for k, v in OVERRIDES[st].items():
        if v is not None:
            out[k] = v
    return out
# Also update the logic in verify_license function:
async def verify_license(state: str, license_number: Optional[str] = None, business_name: Optional[str] = None) -> Dict[str, Any]:
    if not state or not license_number:
        raise ValueError("State and license_number are required")

    state_code = _resolve_state_code(state)
    if not state_code:
        return {"status": "Unsupported", "message": "Missing or invalid state", "verified": False}

    # Defensive lookup (case-insensitive)
    cfg = (
        STATE_CONFIGS.get(state_code)
        or STATE_CONFIGS.get(state_code.lower())
        or STATE_CONFIGS.get((state_code or "").strip())
    )
    if not cfg:
        return {
            "status": "Unsupported",
            "message": f"State {state_code} not supported or CSV not loaded",
            "verified": False,
        }
# Add this near the top of your scraper.py (after the STATE_CONFIGS loading):

OVERRIDES: Dict[str, Dict[str, Any]] = {
    # Oregon CCB — requires click-through from search results to detail page
    "OR": {
        "REQUIRES_JAVASCRIPT": True,  # Force Playwright for Oregon
        "BUSINESS_NAME_XPATH": "//h1[contains(text(),'CCB License Summary:')]/text() | //h1/following::text()[contains(.,'COMPANY') or contains(.,'INC')][1]",
        "STATUS_XPATH": "//td[normalize-space(text())='Status:']/following-sibling::td[1]/text()",
        "EXPIRES_XPATH": "//td[normalize-space(text())='First Licensed:']/following-sibling::td[1]/text()",
    },
}
    # apply overrides (fix brittle states even if CSV is wrong)
    cfg = _apply_overrides(cfg)

    norm_license = normalize_license_number(state_code, license_number)

    # Format validation
    regex = cfg.get("LICENSE_REGEX") or ""
    format_valid = _validate_format_with_regex(regex, norm_license) if regex else True

    cache_key = f"{state_code}:{norm_license}"
    try:
        cached = get_cached_result(cache_key)
        if cached:
            cached["from_cache"] = True
            cached["format_valid"] = format_valid
            return cached
    except Exception:
        pass

    # FORCE PLAYWRIGHT FOR OREGON - it requires clicking through search results
    requires_js = bool(cfg.get("REQUIRES_JAVASCRIPT")) or (state_code in FORCE_JS_STATES)
    
    # Special handling for Oregon - always try Playwright first if available
    if state_code == "OR" and PLAYWRIGHT_AVAILABLE:
        print(f"DEBUG: Using Playwright for Oregon license {norm_license}")
        result = await _scrape_with_playwright(cfg, norm_license)
    elif requires_js and PLAYWRIGHT_AVAILABLE:
        result = await _scrape_with_playwright(cfg, norm_license)
    else:
        print(f"DEBUG: Using requests for {state_code} (Playwright available: {PLAYWRIGHT_AVAILABLE})")
        result = await _scrape_with_requests(cfg, norm_license)

    result["format_valid"] = format_valid

    try:
        store_result(cache_key, result)
    except Exception:
        pass

    return result

# Update the Playwright navigation function to better handle Oregon:
async def _navigate_to_detail_if_needed(page, cfg: Dict[str, Any], license_number: str):
    """
    Generic 'click into details' navigator for result lists.
    Enhanced for Oregon CCB specifically.
    """
    state = cfg.get("STATE", "").upper()
    
    try:
        # Check if already on a detail page
        detail_markers = [
            "License Summary", "License Details", "Detail", "Licensee Details",
            "CCB License Summary", "License Information", "License Number:"
        ]
        for marker in detail_markers:
            if await page.locator(f"text={marker}").count() > 0:
                print(f"DEBUG: Already on detail page (found: {marker})")
                return
    except Exception:
        pass

    print(f"DEBUG: Looking for ways to navigate to detail page for license {license_number}")

    # Oregon-specific: Look for the license number in search results
    if state == "OR":
        try:
            # Wait a moment for results to load
            await page.wait_for_timeout(2000)
            
            # Try clicking on the license number in search results
            license_selectors = [
                f"a:has-text('{license_number}')",
                f"td:has-text('{license_number}') + td a",
                f"tr:has-text('{license_number}') a",
                "table tr:first-child a",  # First result
            ]
            
            for selector in license_selectors:
                try:
                    if await page.locator(selector).count() > 0:
                        print(f"DEBUG: Clicking Oregon license link: {selector}")
                        await page.click(selector)
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        return
                except Exception as e:
                    print(f"DEBUG: Failed to click {selector}: {e}")
                    continue
                    
        except Exception as e:
            print(f"DEBUG: Oregon-specific navigation failed: {e}")

    # Generic click-through logic (existing code)
    # 1) Exact license number text/link
    try:
        sel = f"text='{license_number}'"
        if await page.locator(sel).count() > 0:
            print(f"DEBUG: Clicking exact license number: {sel}")
            await page.click(sel)
            await page.wait_for_load_state("networkidle", timeout=15000)
            return
    except Exception:
        pass

    # 2) Common "Details" actions
    for sel in ["a:has-text('Details')", "button:has-text('Details')",
                "a:has-text('More')", "button:has-text('More')",
                "a:has-text('View')", "button:has-text('View')"]:
        try:
            if await page.locator(sel).count() > 0:
                print(f"DEBUG: Clicking details button: {sel}")
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
                print(f"DEBUG: Clicking first table link: {sel}")
                await loc.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
                return
        except Exception:
            continue
            
    print("DEBUG: No clickable elements found for navigation")
# Many states show a results table first → force JS to allow clicking into details
FORCE_JS_STATES = {
    "OR","MA","NJ","NV","PA","SC","AZ","WA","UT","CT","DE","IL","IN","LA","MD","MI","MN",
    "NC","NE","NM","NY","OH","OK","TN","TX","VA","WI","GA","CA","FL","ID","IA","KS","KY",
    "ME","MO","MS","MT","NH","RI","SD","VT","WV","WY","ND","CO","AR","AL"
}

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
    return re.sub(r"\s+", "", num)  # keep dots/dashes; drop spaces

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
    code = _resolve_state_code(state) or (state or "").upper()
    cfg = STATE_CONFIGS.get(code) or STATE_CONFIGS.get((code or "").lower())
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
        return True  # bad regex in CSV → don't block

# ===== Extraction helpers =====
def _xpath_try(tree, xpath_expr: str) -> Optional[str]:
    """Support multiple XPaths separated by '||'. Return first matched text."""
    if not xpath_expr:
        return None
    for candidate in [s.strip() for s in xpath_expr.split("||") if s.strip()]:
        try:
            nodes = tree.xpath(candidate)
            if not nodes:
                continue
            node = nodes[0]
            val = node if isinstance(node, str) else node.text_content()
            val = (val or "").strip()
            if val:
                return val
        except Exception:
            continue
    return None

async def _extract_with_xpath_from_text(html_text: str, cfg: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Extract data with special Oregon handling"""
    
    # Special case for Oregon
    if cfg.get("STATE", "").upper() == "OR":
        business_name = extract_oregon_business_name(html_text)
        
        # Extract status and other info normally
        try:
            tree = html.fromstring(html_text)
            status = _xpath_try(tree, "//td[normalize-space(text())='Status:']/following-sibling::td[1]/text()")
            expires = _xpath_try(tree, "//td[normalize-space(text())='First Licensed:']/following-sibling::td[1]/text()")
            
            return {
                "business_name": business_name,
                "status": status or "Unknown",
                "expires": f"First Licensed: {expires}" if expires else None
            }
        except Exception:
            return {
                "business_name": business_name,
                "status": "Unknown", 
                "expires": None
            }
    
    # Standard extraction for other states
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
        # Warm-up for cookies
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
      - the first link inside a results table/grid
    """
    try:
        # already on a detail page?
        detail_markers = [
            "License Summary", "License Details", "Detail", "Licensee Details",
            "CCB License Summary", "License Information", "License Number:"
        ]
        for marker in detail_markers:
            if await page.locator(f"text={marker}").count() > 0:
                return
    except Exception:
        pass

    # 1) Exact license number text/link
    try:
        sel = f"text='{license_number}'"
        if await page.locator(sel).count() > 0:
            await page.click(sel)
            await page.wait_for_load_state("networkidle", timeout=15000)
            return
    except Exception:
        pass

    # 2) Common "Details" actions
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

async def _fill_and_search(page, cfg: Dict[str, Any], license_number: str):
    """Fill search form with best-effort heuristics + known state quirks."""
    field_name = cfg.get("INPUT_FIELD_NAME") or ""
    search_btn_id = cfg.get("SEARCH_BUTTON_ID") or ""

    # OR-specific hints (handle common selectors seen on CCB search)
    state = (cfg.get("STATE") or "").upper()
    or_candidates = [
        "#LicNum", "input[name='LicNum']",
        "input[name='searchString']", "#searchString"
    ] if state == "OR" else []

    filled = False

    # CSV-provided name
    if field_name:
        try:
            await page.fill(f"input[name=\"{field_name}\"]", license_number)
            filled = True
        except Exception:
            filled = False

    # State-specific fallbacks
    if not filled and or_candidates:
        for sel in or_candidates:
            try:
                if await page.locator(sel).count() > 0:
                    await page.fill(sel, license_number)
                    filled = True
                    break
            except Exception:
                continue

    # Heuristics
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

    # Click search button
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

        # Fill, submit, then click into details
        await _fill_and_search(page, cfg, license_number)

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

    state_code = _resolve_state_code(state)
    if not state_code:
        return {"status": "Unsupported", "message": "Missing or invalid state", "verified": False}

    # Defensive lookup (case-insensitive)
    cfg = (
        STATE_CONFIGS.get(state_code)
        or STATE_CONFIGS.get(state_code.lower())
        or STATE_CONFIGS.get((state_code or "").strip())
    )
    if not cfg:
        return {
            "status": "Unsupported",
            "message": f"State {state_code} not supported or CSV not loaded",
            "verified": False,
            "debug": {
                "csv_path": str(CSV_PATH),
                "csv_exists": CSV_PATH.exists(),
                "total_states_loaded": len(STATE_CONFIGS),
            },
        }

    # apply overrides (fix brittle states even if CSV is wrong)
    cfg = _apply_overrides(cfg)

    norm_license = normalize_license_number(state_code, license_number)

    # SOFT validation: warn but DO NOT block scraping
    regex = cfg.get("LICENSE_REGEX") or ""
    format_valid = _validate_format_with_regex(regex, norm_license) if regex else True

    cache_key = f"{state_code}:{norm_license}"
    try:
        cached = get_cached_result(cache_key)
        if cached:
            cached["from_cache"] = True
            cached["format_valid"] = format_valid
            return cached
    except Exception:
        pass

    # Force Playwright for result-list states (OR if CSV says JS)
    requires_js = bool(cfg.get("REQUIRES_JAVASCRIPT")) or (state_code in FORCE_JS_STATES)
    if requires_js and PLAYWRIGHT_AVAILABLE:
        result = await _scrape_with_playwright(cfg, norm_license)
    else:
        result = await _scrape_with_requests(cfg, norm_license)

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
    code = _resolve_state_code(state) or (state or "").upper()
    cfg = STATE_CONFIGS.get(code) or STATE_CONFIGS.get((code or "").lower())
    if not cfg:
        return {"valid": False, "error": f"State {code} not supported"}
    regex = cfg.get("LICENSE_REGEX") or ""
    ok = _validate_format_with_regex(regex, license_number) if regex else True
    return {
        "valid": ok,
        "expected_format": regex or "Varies",
        "example": cfg.get("EXAMPLE_LICENSE") or "N/A"
    }
