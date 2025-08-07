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

def _text_or_none(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None

def _validate_format_with_regex(regex: str, license_number: str) -> bool:
    if not regex:
        return True
    try:
        return re.match(regex, license_number) is not None
    except re.error:
        # bad regex in CSV; don't block user
        return True

async def _extract_with_xpath_from_text(html_text: str, cfg: Dict[str, Any]) -> Dict[str, Optional[str]]:
    try:
        tree = html.fromstring(html_text)
    except Exception:
        return {"business_name": None, "status": None, "expires": None}

    def xp(xp_expr: str) -> Optional[str]:
       
