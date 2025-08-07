import re
import asyncio
from typing import Optional, Dict, Any, List
import aiohttp
from bs4 import BeautifulSoup
from cache import get_cached_result, store_result
import json
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to import Playwright, but gracefully handle if not available
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
    logger.info("Playwright is available")
except ImportError as e:
    logger.warning(f"Playwright not available: {e}. Using requests-only mode.")

# Simplified state configurations - focusing on requests-compatible sites first
STATE_CONFIGS = {
    "FL": {
        "regex": r"^CGC\d{7}$",
        "example": "CGC1524312",
        "type": "Certified General Contractor",
        "format": "Prefix CGC + 7 digits",
        "url": "https://www.myfloridalicense.com/wl11.asp",
        "method": "requests",
        "search_param": "licnbr"
    },
    "CA": {
        "regex": r"^\d{6,8}$",
        "example": "692447",
        "type": "CSLB Contractor",
        "format": "6 to 8 digits",
        "url": "https://www.cslb.ca.gov/onlineservices/checklicenseII/checklicense.aspx",
        "method": "requests_simple",
        "notes": "Simplified verification"
    },
    "TX": {
        "regex": r"\d{5,6}",
        "example": "12345",
        "type": "TDLR License",
        "format": "Five or six-digit numeric ID",
        "url": "https://www.tdlr.texas.gov/LicenseSearch/",
        "method": "requests_simple"
    },
    "NY": {
        "example": "123456",
        "type": "Municipal Licensing",
        "format": "Varies by municipality",
        "url": "https://appext20.dos.ny.gov/lcns_public/licenseesearch/lcns_public_index.cfm",
        "method": "requests_simple",
        "notes": "No statewide GC license"
    },
    "PA": {
        "regex": r"PA\d{6}",
        "example": "PA123456",
        "type": "Home Improvement Contractor",
        "format": "Prefix 'PA' + six digits",
        "url": "https://hicsearch.attorneygeneral.gov/",
        "method": "requests_simple"
    },
    "OR": {
        "regex": r"\d{6}",
        "example": "123456",
        "type": "Construction Contractors Board",
        "format": "Six-digit numeric ID",
        "url": "https://search.ccb.state.or.us/search/",
        "method": "requests_simple"
    },
    "WA": {
        "regex": r"[A-Z]{3}\d{4}",
        "example": "ABC1234",
        "type": "Contractor Registration",
        "format": "Three letters + four digits",
        "url": "https://secure.lni.wa.gov/verify/",
        "method": "requests_simple"
    },
    "AZ": {
        "regex": r"^\d{6}$",
        "example": "321456",
        "type": "ROC License",
        "format": "6 digits",
        "url": "https://azroc.my.site.com/AZRoc/s/contractor-search",
        "method": "requests_simple"
    },
    "NV": {
        "regex": r"\d{6}",
        "example": "123456",
        "type": "State Contractors Board",
        "format": "Six-digit numeric ID",
        "url": "https://app.nvcontractorsboard.com/Clients/NVSCB/Public/ContractorLicenseSearch/ContractorLicenseSearch.aspx",
        "method": "requests_simple"
    },
    "GA": {
        "url": "https://verify.sos.ga.gov/verification/Search.aspx",
        "method": "requests_simple",
        "type": "Professional License"
    }
}

def validate_license_format(state: str, license_number: str) -> Dict[str, Any]:
    """Validate license number format against state requirements"""
    state = state.upper()
    
    if state not in STATE_CONFIGS:
        return {
            "valid": False,
            "error": f"State {state} not currently supported",
            "supported_states": list(STATE_CONFIGS.keys()),
            "note": "More states will be added as the system scales"
        }
    
    config = STATE_CONFIGS[state]
    regex_pattern = config.get("regex")
    
    if not regex_pattern:
        return {
            "valid": True,
            "warning": f"No strict format validation for {state}",
            "format_info": config
        }
    
    is_valid = bool(re.match(regex_pattern, license_number))
    
    return {
        "valid": is_valid,
        "format_info": config,
        "expected_format": config["format"],
        "example": config["example"],
        "notes": config.get("notes")
    }

async def verify_with_simple_requests(state: str, config: Dict, license_number: str, business_name: Optional[str] = None) -> Dict[str, Any]:
    """Simple HTTP requests verification method"""
    
    timeout = aiohttp.ClientTimeout(total=30)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache'
            }
            
            # State-specific request handling
            if state == "FL":
                # Florida MyFloridaLicense
                search_url = f"https://www.myfloridalicense.com/wl11.asp?mode=0&licnbr={license_number}"
                async with session.get(search_url, headers=headers) as response:
                    html_content = await response.text()
                    
            elif state == "CA":
                # California - try to access the verification page
                async with session.get(config["url"], headers=headers) as response:
                    html_content = await response.text()
                    
                # Look for the license number in any form on the page
                if license_number in html_content:
                    return {
                        "status": "Page Accessed",
                        "license_number": license_number,
                        "message": f"Successfully accessed {state} verification page",
                        "verification_url": config["url"],
                        "note": "Manual verification required - page requires JavaScript interaction"
                    }
                    
            else:
                # Generic approach - try GET request with license parameter
                params = {'license': license_number, 'search': '1'}
                async with session.get(config["url"], params=params, headers=headers) as response:
                    html_content = await response.text()
            
            # Parse response for license information
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Analyze content for license status
            content_lower = html_content.lower()
            status = "Unknown"
            business_name_result = business_name or "Unknown"
            expires = "Unknown"
            verification_method = "basic_analysis"
            
            # Look for license status indicators
            if any(phrase in content_lower for phrase in [
                "license is active", "status: active", "current", "good standing", 
                "valid license", "license valid"
            ]):
                status = "Active"
                verification_method = "content_analysis"
                
            elif any(phrase in content_lower for phrase in [
                "expired", "inactive", "lapsed", "not current"
            ]):
                status = "Expired"
                verification_method = "content_analysis"
                
            elif any(phrase in content_lower for phrase in [
                "invalid", "not found", "no results", "no license", "does not exist"
            ]):
                status = "Invalid"
                verification_method = "content_analysis"
                
            elif any(phrase in content_lower for phrase in [
                "suspended", "revoked", "cancelled", "disciplinary"
            ]):
                status = "Suspended"
                verification_method = "content_analysis"
                
            elif license_number.lower() in content_lower:
                status = "Found"
                verification_method = "license_number_found"
                
            # Try to extract business/contractor name
            name_patterns = [
                r"contractor[:\s]+([^<\n\r,]+)",
                r"business name[:\s]+([^<\n\r,]+)",
                r"company[:\s]+([^<\n\r,]+)",
                r"name[:\s]+([^<\n\r,]+)"
            ]
            
            for pattern in name_patterns:
                match = re.search(pattern, content_lower)
                if match:
                    extracted_name = match.group(1).strip()
                    if len(extracted_name) > 3 and extracted_name != license_number.lower():
                        business_name_result = extracted_name.title()
                        break
            
            # Try to extract expiration date
            date_patterns = [
                r"expires?[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
                r"expiration[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
                r"valid through[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})"
            ]
            for pattern in date_patterns:
                match = re.search(pattern, content_lower)
                if match:
                    expires = match.group(1)
                    break
            
            return {
                "status": status,
                "license_number": license_number,
                "business_name": business_name_result,
                "issuing_authority": f"{state} {config.get('type', 'Licensing Board')}",
                "expires": expires,
                "verified": verification_method in ["content_analysis", "license_number_found"],
                "verification_url": config["url"],
                "method_used": "http_requests",
                "verification_method": verification_method,
                "format_valid": validate_license_format(state, license_number)["valid"],
                "playwright_available": PLAYWRIGHT_AVAILABLE
            }
            
        except asyncio.TimeoutError:
            raise Exception(f"Timeout accessing {state} verification website")
        except Exception as e:
            raise Exception(f"Error accessing {state} verification website: {str(e)}")

async def verify_license(state: str, license_number: Optional[str] = None, business_name: Optional[str] = None) -> Dict[str, Any]:
    """Main license verification function - simplified for Render deployment"""
    
    # Input validation
    if not state:
        raise Exception("State is required")
    
    if not license_number and not business_name:
        raise Exception("Either license number or business name is required")
    
    state = state.upper()
    
    # Check cache first
    cache_key = f"{state}_{license_number}_{business_name}"
    try:
        cached = get_cached_result(cache_key)
        if cached:
            cached["from_cache"] = True
            return cached
    except Exception as e:
        logger.warning(f"Cache error: {e}")
    
    # Check if state is supported
    if state not in STATE_CONFIGS:
        return {
            "status": "Unsupported",
            "message": f"State {state} not currently supported in this version",
            "supported_states": list(STATE_CONFIGS.keys()),
            "verified": False,
            "note": "More states being added - this is a limited beta version"
        }
    
    config = STATE_CONFIGS[state]
    
    # Normalize and validate license format if provided
    if license_number:
        license_number = normalize_license_number(state, license_number)
        format_validation = validate_license_format(state, license_number)
        
        # For now, allow format validation warnings to proceed
        if not format_validation["valid"] and "warning" not in format_validation:
            return {
                "status": "Invalid Format",
                "message": f"License number '{license_number}' does not match expected format for {state}",
                "expected_format": format_validation["format_info"]["format"],
                "example": format_validation["format_info"]["example"],
                "verified": False
            }
    
    try:
        # Use simplified requests method for all states initially
        result = await verify_with_simple_requests(state, config, license_number, business_name)
        
        # Add state-specific information
        result["state"] = state
        result["license_type"] = config.get("type", "Unknown")
        result["notes"] = config.get("notes")
        
        # Try to cache the result
        try:
            store_result(cache_key, result)
        except Exception as e:
            logger.warning(f"Failed to cache result: {e}")
        
        return result
        
    except Exception as e:
        error_result = {
            "status": "Error",
            "message": str(e),
            "license_number": license_number,
            "state": state,
            "verified": False,
            "verification_url": config["url"],
            "playwright_available": PLAYWRIGHT_AVAILABLE,
            "note": "Service may be experiencing high load or the state website may be temporarily unavailable"
        }
        
        return error_result

async def verify_batch(requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Verify multiple licenses in batch with rate limiting"""
    
    results = []
    
    for i, request in enumerate(requests):
        try:
            # Add delay between requests to be respectful to state servers
            if i > 0:
                await asyncio.sleep(2)
            
            result = await verify_license(
                request.get("state"),
                request.get("license_number"),
                request.get("business_name")
            )
            results.append(result)
            
        except Exception as e:
            results.append({
                "status": "Error",
                "message": str(e),
                "license_number": request.get("license_number"),
                "state": request.get("state"),
                "verified": False
            })
    
    return results

def get_supported_states() -> Dict[str, Dict[str, Any]]:
    """Get list of supported states with their configurations"""
    return {
        state: {
            "type": config.get("type", "Unknown"),
            "format": config.get("format", "Unknown"),
            "example": config.get("example", "Unknown"),
            "notes": config.get("notes"),
            "method": config.get("method")
        }
        for state, config in STATE_CONFIGS.items()
    }

def normalize_license_number(state: str, license_number: str) -> str:
    """Normalize license number format for a given state"""
    state = state.upper()
    license_number = license_number.strip().upper()
    
    # State-specific normalization
    if state == "CA":
        # Remove any non-digit characters for CA
        license_number = re.sub(r'\D', '', license_number)
    elif state == "FL" and not license_number.startswith("CGC"):
        # Add CGC prefix if missing for Florida
        if license_number.isdigit():
            license_number = f"CGC{license_number}"
    elif state == "PA" and not license_number.startswith("PA"):
        # Add PA prefix if missing
        if license_number.isdigit():
            license_number = f"PA{license_number}"
    
    return license_number

def get_state_info(state: str) -> Dict[str, Any]:
    """Get detailed information about a state's licensing system"""
    state = state.upper()
    if state not in STATE_CONFIGS:
        return {"error": f"State {state} not currently supported"}
    
    config = STATE_CONFIGS[state]
    return {
        "state": state,
        "license_type": config.get("type", "Unknown"),
        "format": config.get("format", "Unknown"),
        "example": config.get("example", "Unknown"),
        "regex": config.get("regex"),
        "verification_url": config["url"],
        "method": config["method"],
        "notes": config.get("notes"),
        "playwright_available": PLAYWRIGHT_AVAILABLE
    }

def get_system_status() -> Dict[str, Any]:
    """Get system status and capabilities"""
    return {
        "status": "operational",
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "supported_states": len(STATE_CONFIGS),
        "version": "4.0-simplified",
        "deployment": "render-optimized",
        "note": "This is a simplified version optimized for Render.com deployment"
    }
