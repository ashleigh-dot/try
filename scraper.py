import re
import asyncio
from typing import Optional, Dict, Any, List
import aiohttp
from bs4 import BeautifulSoup
from cache import get_cached_result, store_result
import logging
import base64

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    logger.warning("Playwright not available")

# All 50 states configuration
STATE_CONFIGS = {
    "AL": {"regex": r"^\d{5}$", "example": "55289", "type": "General Contractor", "format": "5 digits", "url": "https://genconbd.alabama.gov/DATABASE-SQL/roster.aspx", "method": "playwright"},
    "AK": {"regex": r"^\d{6}$", "example": "110401", "type": "General Contractor", "format": "6 digits", "url": "https://www.commerce.alaska.gov/cbp/main/Search/Professional", "method": "playwright"},
    "AZ": {"regex": r"^\d{6}$", "example": "321456", "type": "ROC License", "format": "6 digits", "url": "https://azroc.my.site.com/AZRoc/s/contractor-search", "method": "playwright"},
    "AR": {"regex": r"^\d{8}$", "example": "2880113", "type": "Commercial Contractor", "format": "8 digits", "url": "http://aclb2.arkansas.gov/clbsearch.php", "method": "requests"},
    "CA": {"regex": r"^\d{6,8}$", "example": "692447", "type": "CSLB Contractor", "format": "6-8 digits", "url": "https://www.cslb.ca.gov/onlineservices/checklicenseII/checklicense.aspx", "method": "playwright"},
    "CO": {"regex": r"^\d{2}-\d{6}$", "example": "08-000039", "type": "Trade License", "format": "XX-XXXXXX", "url": "https://dpo.colorado.gov/", "method": "playwright"},
    "CT": {"regex": r"^HIC\.\d{7}$", "example": "HIC.0654321", "type": "Home Improvement", "format": "HIC.XXXXXXX", "url": "https://www.elicense.ct.gov/lookup/licenselookup.aspx", "method": "playwright"},
    "DE": {"regex": r"^\d{10}$", "example": "1990000000", "type": "Business License", "format": "10 digits", "url": "https://delpros.delaware.gov/OH_VerifyLicense", "method": "playwright"},
    "FL": {"regex": r"^CGC\d{7}$", "example": "CGC1524312", "type": "General Contractor", "format": "CGC + 7 digits", "url": "https://www.myfloridalicense.com/wl11.asp", "method": "requests"},
    "GA": {"url": "https://verify.sos.ga.gov/verification/Search.aspx", "method": "playwright", "type": "Professional License"},
    "HI": {"regex": r"C-\d+", "example": "C-12345", "type": "Professional & Vocational", "format": "C-XXXXX", "url": "https://mypvl.dcca.hawaii.gov/public-license-search/", "method": "playwright"},
    "ID": {"regex": r"[A-Z]-\d{5}", "example": "E-12345", "type": "Specialty contractor", "format": "X-XXXXX", "url": "https://dbs.idaho.gov/contractors/", "method": "playwright"},
    "IL": {"regex": r"\d{7}", "example": "1234567", "type": "Professional", "format": "7 digits", "url": "https://ilesonline.idfpr.illinois.gov/DFPR/Lookup/LicenseLookup.aspx", "method": "playwright"},
    "IN": {"regex": r"PC\d{6}", "example": "PC123456", "type": "Building & Trades", "format": "PC + 6 digits", "url": "https://mylicense.in.gov/everification/Search.aspx", "method": "playwright"},
    "IA": {"regex": r"\d{5}", "example": "12345", "type": "Contractor registration", "format": "5 digits", "url": "https://laborportal.iwd.iowa.gov/iwd_portal/publicSearch/public", "method": "playwright"},
    "KS": {"regex": r"T\d{6}", "example": "T123456", "type": "Technical Professions", "format": "T + 6 digits", "url": "https://ksbiz.kansas.gov/business-starter-kit/construction/", "method": "playwright"},
    "KY": {"regex": r"HBC\d{6}", "example": "HBC123456", "type": "Building trades", "format": "HBC + 6 digits", "url": "https://ky.joportal.com/License/Search", "method": "playwright"},
    "LA": {"regex": r"\d{6}", "example": "123456", "type": "General Contractor", "format": "6 digits", "url": "https://lslbc.louisiana.gov/contractor-search/", "method": "playwright"},
    "ME": {"regex": r"\d{4,5}", "example": "1234", "type": "Electrical & Plumbing", "format": "4-5 digits", "url": "https://pfr.maine.gov/ALMSOnline/ALMSQuery/SearchIndividual.aspx", "method": "playwright"},
    "MD": {"regex": r"\d{2}-\d{6}", "example": "01-123456", "type": "Home Improvement", "format": "XX-XXXXXX", "url": "https://www.dllr.state.md.us/cgi-bin/ElectronicLicensing/OP_search/OP_search.cgi?calling_app=HIC::HIC_qselect", "method": "requests"},
    "MA": {"regex": r"CSL-\d{6}", "example": "CSL-123456", "type": "Construction Supervisor", "format": "CSL-XXXXXX", "url": "https://madpl.mylicense.com/Verification/", "method": "playwright"},
    "MI": {"regex": r"\d{7}", "example": "1234567", "type": "Construction", "format": "7 digits", "url": "https://www.michigan.gov/lara/i-need-to/find-or-verify-a-licensed-professional-or-business", "method": "playwright"},
    "MN": {"regex": r"\d{4,6}", "example": "123456", "type": "Contractor & trades", "format": "4-6 digits", "url": "https://secure.doli.state.mn.us/lookup/licensing.aspx", "method": "playwright"},
    "MS": {"regex": r"\d{5}", "example": "12345", "type": "Contractor", "format": "5 digits", "url": "http://search.msboc.us/ConsolidatedResults.cfm?ContractorType=&VarDatasource=BOC&Advanced=1", "method": "requests"},
    "MO": {"example": "20231234", "type": "Local Licensing", "format": "Varies", "url": "https://pr.mo.gov/licensee-search.asp", "method": "playwright"},
    "MT": {"regex": r"\d{5}", "example": "12345", "type": "Construction", "format": "5 digits", "url": "https://erdcontractors.mt.gov/ICCROnlineSearch/registrationlookup.jsp", "method": "playwright"},
    "NE": {"regex": r"\d{5}", "example": "12345", "type": "Contractor", "format": "5 digits", "url": "https://dol.nebraska.gov/conreg/Search", "method": "playwright"},
    "NV": {"regex": r"\d{6}", "example": "123456", "type": "Contractors Board", "format": "6 digits", "url": "https://app.nvcontractorsboard.com/Clients/NVSCB/Public/ContractorLicenseSearch/ContractorLicenseSearch.aspx", "method": "playwright"},
    "NH": {"regex": r"\d{6}", "example": "123456", "type": "Licensed trades", "format": "6 digits", "url": "https://oplc.nh.gov/license-lookup", "method": "playwright"},
    "NJ": {"regex": r"\d{7}", "example": "1234567", "type": "Home Improvement", "format": "7 digits", "url": "https://newjersey.mylicense.com/verification/Search.aspx?facility=Y", "method": "playwright"},
    "NM": {"regex": r"\d{6}", "example": "123456", "type": "Construction", "format": "6 digits", "url": "https://public.psiexams.com/search.jsp", "method": "playwright"},
    "NY": {"example": "123456", "type": "Municipal Licensing", "format": "Varies", "url": "https://appext20.dos.ny.gov/lcns_public/licenseesearch/lcns_public_index.cfm", "method": "playwright"},
    "NC": {"regex": r"\d{5}", "example": "12345", "type": "General Contractor", "format": "5 digits", "url": "https://portal.nclbgc.org/Public/Search", "method": "playwright"},
    "ND": {"regex": r"\d{5}", "example": "12345", "type": "Contractor", "format": "5 digits", "url": "https://firststop.sos.nd.gov/search/contractor", "method": "playwright"},
    "OH": {"regex": r"[A-Z]{2}\d{6}", "example": "HV123456", "type": "Commercial Trades", "format": "XX + 6 digits", "url": "https://elicense3.com.ohio.gov/", "method": "playwright"},
    "OK": {"regex": r"\d{6}", "example": "123456", "type": "Construction Board", "format": "6 digits", "url": "https://okcibv7prod.glsuite.us/GLSuiteWeb/Clients/OKCIB/Public/LicenseeSearch/LicenseeSearch.aspx", "method": "playwright"},
    "OR": {"regex": r"^\d{6}$", "example": "195480", "type": "Construction Contractors Board", "format": "6 digits", "url": "https://search.ccb.state.or.us/search/", "method": "playwright"},
    "PA": {"regex": r"PA\d{6}", "example": "PA123456", "type": "Home Improvement", "format": "PA + 6 digits", "url": "https://hicsearch.attorneygeneral.gov/", "method": "playwright"},
    "RI": {"example": "12345", "type": "Contractor Registration", "format": "5 digits", "url": "https://crb.ri.gov/consumer/search-registrantlicensee", "method": "playwright"},
    "SC": {"regex": r"CLG\d{6}", "example": "CLG123456", "type": "Licensing Board", "format": "CLG + 6 digits", "url": "https://verify.llronline.com/LicLookup/Contractors/Contractor.aspx?div=69", "method": "playwright"},
    "SD": {"regex": r"\d{5}", "example": "12345", "type": "Electrical/Plumbing", "format": "5 digits", "url": "https://sdec.portalus.thentiacloud.net/webs/portal/register/#/", "method": "playwright"},
    "TN": {"regex": r"\d{6}", "example": "123456", "type": "Contractor", "format": "6 digits", "url": "https://www.tn.gov/commerce/regboards/contractor.html", "method": "playwright"},
    "TX": {"regex": r"\d{5,6}", "example": "12345", "type": "TDLR License", "format": "5-6 digits", "url": "https://www.tdlr.texas.gov/LicenseSearch/", "method": "playwright"},
    "UT": {"regex": r"\d{6}-\d{4}", "example": "123456-5501", "type": "Contractor", "format": "XXXXXX-XXXX", "url": "https://secure.utah.gov/llv/search/index.html", "method": "playwright"},
    "VT": {"example": "456789", "type": "Registration", "format": "Varies", "url": "https://sos.vermont.gov/opr/find-a-professional/", "method": "playwright"},
    "VA": {"regex": r"2705\d{6}", "example": "2710000000", "type": "Contractor", "format": "2705 + 6 digits", "url": "https://www.dpor.virginia.gov/LicenseLookup/", "method": "playwright"},
    "WA": {"regex": r"[A-Z]{3}\d{4}", "example": "ABC1234", "type": "Registration", "format": "XXX + 4 digits", "url": "https://secure.lni.wa.gov/verify/", "method": "playwright"},
    "WV": {"regex": r"WV\d{6}", "example": "WV012345", "type": "Licensing Board", "format": "WV + 6 digits", "url": "https://wvclboard.wv.gov/verify/", "method": "playwright"},
    "WI": {"regex": r"\d{6}", "example": "123456", "type": "Dwelling Contractor", "format": "6 digits", "url": "https://dsps.wi.gov/Pages/Professions/Default.aspx", "method": "playwright"},
    "WY": {"regex": r"\d{5}", "example": "12345", "type": "Local Licensing", "format": "5 digits", "url": "https://doe.state.wy.us/lmi/licensed_occupations.htm", "method": "playwright"}
}

async def scrape_with_playwright(state: str, config: Dict, license_number: str) -> Dict[str, Any]:
    """Playwright scraper for JavaScript sites"""
    if not PLAYWRIGHT_AVAILABLE:
        return await scrape_with_requests_fallback(state, config, license_number)
    
    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            page = await browser.new_page()
            await page.goto(config["url"], wait_until="networkidle", timeout=30000)
            
            # Find license input and search button intelligently
            inputs = await page.query_selector_all("input[type='text'], input[name*='license'], input[id*='license']")
            for input_elem in inputs:
                try:
                    await input_elem.fill(license_number)
                    break
                except:
                    continue
            
            buttons = await page.query_selector_all("button, input[type='submit'], input[value*='Search']")
            for button in buttons:
                try:
                    await button.click()
                    break
                except:
                    continue
            
            await page.wait_for_load_state("networkidle", timeout=15000)
            screenshot_bytes = await page.screenshot(full_page=True)
            content = await page.content()
            
            # Extract data
            extracted = extract_license_data(state, content)
            await browser.close()
            
            return {
                **extracted,
                "license_number": license_number,
                "verification_url": config["url"],
                "screenshot_data": base64.b64encode(screenshot_bytes).decode('utf-8'),
                "verified": True,
                "state": state,
                "license_type": config.get("type", "Unknown")
            }
            
        except Exception as e:
            if browser:
                await browser.close()
            return await scrape_with_requests_fallback(state, config, license_number)

async def scrape_with_requests_fallback(state: str, config: Dict, license_number: str) -> Dict[str, Any]:
    """Fallback method using requests"""
    async with aiohttp.ClientSession() as session:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            
            if state == "FL":
                url = f"https://www.myfloridalicense.com/wl11.asp?mode=0&licnbr={license_number}"
                async with session.get(url, headers=headers) as response:
                    content = await response.text()
            else:
                params = {'license': license_number}
                async with session.get(config["url"], params=params, headers=headers) as response:
                    content = await response.text()
            
            extracted = extract_license_data(state, content)
            
            return {
                **extracted,
                "license_number": license_number,
                "verification_url": config["url"],
                "verified": extracted["status"] != "Unknown",
                "method_used": "requests_fallback",
                "state": state,
                "license_type": config.get("type", "Unknown")
            }
            
        except Exception as e:
            raise Exception(f"Error verifying {state}: {str(e)}")

def extract_license_data(state: str, html_content: str) -> Dict[str, Any]:
    """Extract license data from HTML content"""
    content_lower = html_content.lower()
    
    # Extract business name
    name_patterns = [
        r'<h[1-6][^>]*>([^<]*(?:LLC|INC|CORP|COMPANY)[^<]*)</h[1-6]>',
        r'business name[:\s]*([^<\n\r]+)',
        r'company[:\s]*([^<\n\r]+)',
        r'contractor[:\s]*([A-Z][^<\n\r,]{5,})'
    ]
    business_name = "Unknown"
    for pattern in name_patterns:
        match = re.search(pattern, html_content, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) > 3 and not name.isdigit():
                business_name = name
                break
    
    # Extract status
    status = "Unknown"
    if any(word in content_lower for word in ["active", "valid", "current", "good standing"]):
        status = "Active"
    elif any(word in content_lower for word in ["expired", "inactive", "lapsed"]):
        status = "Expired"
    elif any(word in content_lower for word in ["invalid", "not found", "no results"]):
        status = "Invalid"
    elif any(word in content_lower for word in ["suspended", "revoked"]):
        status = "Suspended"
    
    # Extract expiration
    expires = "Unknown"
    date_patterns = [r'expir[a-z]*[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})', r'expires[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})']
    for pattern in date_patterns:
        match = re.search(pattern, html_content, re.IGNORECASE)
        if match:
            expires = match.group(1)
            break
    
    return {"status": status, "business_name": business_name, "expires": expires}

async def verify_license(state: str, license_number: Optional[str] = None, business_name: Optional[str] = None) -> Dict[str, Any]:
    """Main verification function"""
    if not state or not license_number:
        raise Exception("State and license number required")
    
    state = state.upper()
    license_number = normalize_license_number(state, license_number)
    
    cache_key = f"{state}_{license_number}"
    try:
        cached = get_cached_result(cache_key)
        if cached:
            cached["from_cache"] = True
            return cached
    except:
        pass
    
    if state not in STATE_CONFIGS:
        return {"status": "Unsupported", "message": f"State {state} not supported", "supported_states": list(STATE_CONFIGS.keys()), "verified": False}
    
    config = STATE_CONFIGS[state]
    
    # Validate format
    if config.get("regex") and not re.match(config["regex"], license_number):
        return {"status": "Invalid Format", "message": f"Invalid format for {state}", "example": config.get("example"), "verified": False}
    
    try:
        if config["method"] == "playwright":
            result = await scrape_with_playwright(state, config, license_number)
        else:
            result = await scrape_with_requests_fallback(state, config, license_number)
        
        try:
            store_result(cache_key, result)
        except:
            pass
        
        return result
        
    except Exception as e:
        return {"status": "Error", "message": str(e), "license_number": license_number, "state": state, "verified": False}

def normalize_license_number(state: str, license_number: str) -> str:
    """Normalize license numbers with state prefixes"""
    state = state.upper()
    license_number = license_number.strip().upper()
    
    prefixes = {
        "FL": lambda x: f"CGC{x}" if x.isdigit() and not x.startswith("CGC") else x,
        "PA": lambda x: f"PA{x}" if x.isdigit() and not x.startswith("PA") else x,
        "WV": lambda x: f"WV{x}" if x.isdigit() and not x.startswith("WV") else x,
        "CT": lambda x: f"HIC.{x}" if x.isdigit() and not x.startswith("HIC.") else x,
        "SC": lambda x: f"CLG{x}" if x.isdigit() and not x.startswith("CLG") else x,
        "CA": lambda x: re.sub(r'\D', '', x)
    }
    
    return prefixes.get(state, lambda x: x)(license_number)

async def verify_batch(requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Batch verification with delays"""
    results = []
    for i, request in enumerate(requests):
        if i > 0:
            await asyncio.sleep(2)
        try:
            result = await verify_license(request.get("state"), request.get("license_number"))
            results.append(result)
        except Exception as e:
            results.append({"status": "Error", "message": str(e), "verified": False})
    return results

def validate_license_format(state: str, license_number: str) -> Dict[str, Any]:
    """Validate format"""
    state = state.upper()
    if state not in STATE_CONFIGS:
        return {"valid": False, "error": f"State {state} not supported"}
    
    config = STATE_CONFIGS[state]
    regex = config.get("regex")
    if not regex:
        return {"valid": True, "warning": "No format validation"}
    
    return {"valid": bool(re.match(regex, license_number)), "expected_format": config["format"], "example": config["example"]}

def get_supported_states() -> Dict[str, Dict[str, Any]]:
    """Get supported states"""
    return {state: {"type": config.get("type"), "format": config.get("format"), "example": config.get("example")} for state, config in STATE_CONFIGS.items()}

def get_state_info(state: str) -> Dict[str, Any]:
    """Get state info"""
    state = state.upper()
    if state not in STATE_CONFIGS:
        return {"error": f"State {state} not supported"}
    return STATE_CONFIGS[state]

def get_system_status() -> Dict[str, Any]:
    """System status"""
    return {"status": "operational", "playwright_available": PLAYWRIGHT_AVAILABLE, "total_states": len(STATE_CONFIGS), "version": "4.3-complete"}
