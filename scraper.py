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
        print(f"WARNING: CSV file not found at {CSV_PATH}")
        return configs
    
    try:
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
        print(f"Loaded {len(configs)} state configurations")
    except Exception as e:
        print(f"Error loading CSV: {e}")
    
    return configs

STATE_CONFIGS: Dict[str, Dict[str, Any]] = load_state_configs()

# Hard overrides for brittle states
OVERRIDES: Dict[str, Dict[str, Any]] = {
    "OR": {
        "REQUIRES_JAVASCRIPT": True,
        "BUSINESS_NAME_XPATH": "//h1[contains(text(),'CCB License Summary')]/following::text()[normalize-space()][1]",
        "STATUS_XPATH": "//td[normalize-space(text())='Status:']/following-sibling::td[1]/text()",
