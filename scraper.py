from pathlib import Path
import json, re

project = Path("/mnt/data/project_inspect/try-main")
scraper_path = project / "scraper.fixed.py"

scraper_code = dedent("""
    import re
    import asyncio
    from datetime import datetime
    from pathlib import Path
    from typing import Dict, Any, List, Optional
    
    import csv
    from cache import get_cached_result, store_result
    
    BASE_DIR = Path(__file__).resolve().parent
    CSV_PATH = BASE_DIR / "contractor_license_verification_database.csv"
    
    # --- Load state configs from CSV once ---
    def load_state_configs() -> Dict[str, Dict[str, Any]]:
        configs: Dict[str, Dict[str, Any]] = {}
        if not CSV_PATH.exists():
            return configs
        with open(CSV_PATH, newline="", encoding="utf-8", errors="ignore") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                st = row.get("STATE", "").strip()
                if not st:
                    continue
                # Normalize keys we use frequently
                cfg: Dict[str, Any] = dict(row)
                cfg["LICENSE_REGEX"] = row.get("LICENSE_REGEX", "").strip()
                cfg["EXAMPLE_LICENSE"] = row.get("EXAMPLE_LICENSE", "").strip()
                cfg["VERIFICATION_URL"] = row.get("VERIFICATION_URL", "").strip()
                cfg["LICENSE_TYPE"] = row.get("LICENSE_TYPE", "Professional License").strip()
                cfg["REQUIRES_JAVASCRIPT"] = str(row.get("REQUIRES_JAVASCRIPT", "")).strip().upper() in {"TRUE","1","YES"}
                configs[st] = cfg
        return configs
    
    STATE_CONFIGS: Dict[str, Dict[str, Any]] = load_state_configs()
    
    # --- Helpers ---
    def normalize_license_number(state: str, license_number: Optional[str]) -> Optional[str]:
        if not license_number:
            return None
        return license_number.strip()
    
    def _compile_regex(pattern: str):
        if not pattern:
            return None
        try:
            return re.compile(pattern)
        except re.error:
            return None
    
    def validate_license_format(state: str, license_number: str) -> Dict[str, Any]:
        st = state.upper().strip()
        cfg = STATE_CONFIGS.get(st)
        if not cfg:
            return {"state": st, "valid": False, "reason": "Unsupported state"}
        pattern = cfg.get("LICENSE_REGEX", "")
        rx = _compile_regex(pattern)
        if not rx:
            return {"state": st, "valid": None, "reason": "No regex available for this state"}
        ok = bool(rx.match(license_number or ""))
        return {
            "state": st,
            "valid": ok,
            "pattern": pattern,
            "example": cfg.get("EXAMPLE_LICENSE") or None
        }
    
    def get_supported_states() -> List[str]:
        return sorted(STATE_CONFIGS.keys())
    
    def get_state_info(state: str) -> Dict[str, Any]:
        st = state.upper().strip()
        return STATE_CONFIGS.get(st, {})
    
    def get_system_status() -> Dict[str, Any]:
        return {
            "states_loaded": len(STATE_CONFIGS),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    
    # --- Core verify functions (network-light placeholder) ---
    async def verify_license(state: str, license_number: Optional[str], business_name: Optional[str] = None) -> Dict[str, Any]:
        st = state.upper().strip()
        cfg = STATE_CONFIGS.get(st)
        if not cfg:
            return {"state": st, "status": "Unsupported", "message": "State not in configuration"}
        
        # Cache lookup
        cache_key = f"{st}:{license_number or ''}:{business_name or ''}"
        cached = get_cached_result(cache_key)
        if cached:
            return cached
        
        # Format-only quick check if license provided
        fmt = None
        if license_number:
            fmt = validate_license_format(st, normalize_license_number(st, license_number))
        
        # Placeholder verification result (since real scraping per-state isn't provided here)
        result: Dict[str, Any] = {
            "state": st,
            "input": {
                "license_number": license_number,
                "business_name": business_name
            },
            "format_check": fmt,
            "status": "Unknown",
            "verification_url": cfg.get("VERIFICATION_URL"),
            "notes": "Live scraping not implemented in this build; format check returned above."
        }
        
        store_result(cache_key, result)
        return result
    
    async def verify_batch(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Constrain batch size to avoid runaway concurrency
        items = items[:10]
        tasks = [verify_license(i.get("state",""), i.get("license_number"), i.get("business_name")) for i in items]
        return await asyncio.gather(*tasks)
""")

scraper_path.write_text(scraper_code, encoding="utf-8")

print(f"Wrote fixed scraper to: {scraper_path}")
print("Lines:", len(scraper_code.splitlines()))
