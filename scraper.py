import re
import asyncio
from typing import Optional, Dict, Any, List
import aiohttp
from bs4 import BeautifulSoup
from cache import get_cached_result, store_result
import json
import logging
import base64
from urllib.parse import urljoin, urlparse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to import Playwright
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Playwright not available: {e}")

# Complete 50-state configuration with intelligent scraping strategies
STATE_CONFIGS = {
    "AL": {
        "regex": r"^\d{5}$", "example": "55289", "type": "General Contractor", "format": "5 digits",
        "url": "https://secure.lni.wa.gov/verify/", "method": "playwright",
        "selectors": {"license_input": "input[name='licenseNumber']", "search_btn": "input[value='Search']"}
    },
    "WV": {
        "regex": r"WV\d{6}", "example": "WV012345", "type": "Contractors Licensing Board", "format": "WV + 6 digits",
        "url": "https://wvclboard.wv.gov/verify/", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "WI": {
        "regex": r"\d{6}", "example": "123456", "type": "Dwelling Contractor", "format": "6 digits",
        "url": "https://dsps.wi.gov/Pages/Professions/Default.aspx", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "WY": {
        "regex": r"\d{5}", "example": "12345", "type": "Local Licensing", "format": "5 digits",
        "url": "https://doe.state.wy.us/lmi/licensed_occupations.htm", "method": "playwright",
        "notes": "Local licensing only", "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    }
})

# Enhanced extraction patterns for common license result formats
COMMON_EXTRACTION_PATTERNS = {
    "business_name": [
        r'business name[:\s]*([^<\n\r]+)',
        r'company name[:\s]*([^<\n\r]+)', 
        r'contractor name[:\s]*([^<\n\r]+)',
        r'licensee name[:\s]*([^<\n\r]+)',
        r'<h[1-6][^>]*>([^<]*(?:LLC|INC|CORP|COMPANY|CONTRACTORS?)[^<]*)</h[1-6]>',
        r'name[:\s]*([A-Z][^<\n\r,]{5,})'
    ],
    "status": [
        r'status[:\s]*([^<\n\r]+)',
        r'license status[:\s]*([^<\n\r]+)',
        r'current status[:\s]*([^<\n\r]+)',
        r'registration status[:\s]*([^<\n\r]+)'
    ],
    "expiration": [
        r'expir[a-z]*[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        r'expires[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        r'valid through[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        r'renewal date[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        r'effective date[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})'
    ],
    "first_licensed": [
        r'first licensed[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        r'original issue[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        r'initial license[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})'
    ]
}

def smart_extract_field(html_content: str, field_type: str, state_specific_patterns: Dict = None) -> str:
    """Smart field extraction using multiple pattern matching"""
    
    # Try state-specific patterns first
    if state_specific_patterns and field_type in state_specific_patterns:
        pattern = state_specific_patterns[field_type]
        match = re.search(pattern, html_content, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    
    # Fall back to common patterns
    patterns = COMMON_EXTRACTION_PATTERNS.get(field_type, [])
    for pattern in patterns:
        match = re.search(pattern, html_content, re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            # Validate extraction quality
            if field_type == "business_name" and len(extracted) > 3 and not extracted.isdigit():
                return extracted
            elif field_type in ["status", "expiration", "first_licensed"]:
                return extracted
    
    return "Unknown"

async def adaptive_playwright_scraper(state: str, config: Dict, license_number: str) -> Dict[str, Any]:
    """Adaptive Playwright scraper that learns each state's structure"""
    
    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage', 
                    '--disable-blink-features=AutomationControlled',
                    '--disable-features=VizDisplayCompositor'
                ]
            )
            
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            # Navigate to state website
            await page.goto(config["url"], wait_until="networkidle", timeout=30000)
            
            # Try multiple input strategies
            license_filled = False
            selectors = config.get("selectors", {})
            
            # Strategy 1: Use configured selectors
            if selectors.get("license_input"):
                try:
                    await page.fill(selectors["license_input"], license_number)
                    license_filled = True
                except Exception as e:
                    logger.debug(f"Configured selector failed for {state}: {e}")
            
            # Strategy 2: Intelligent input detection
            if not license_filled:
                input_selectors = [
                    "input[name*='license']", "input[id*='license']", "input[placeholder*='license']",
                    "input[name*='number']", "input[id*='number']", "input[placeholder*='number']",
                    "input[type='text']", "input[type='search']"
                ]
                
                for selector in input_selectors:
                    try:
                        elements = await page.query_selector_all(selector)
                        for element in elements:
                            # Check if this looks like a license input
                            placeholder = await element.get_attribute("placeholder") or ""
                            name = await element.get_attribute("name") or ""
                            id_attr = await element.get_attribute("id") or ""
                            
                            if any(keyword in (placeholder + name + id_attr).lower() 
                                  for keyword in ["license", "number", "id", "search"]):
                                await element.fill(license_number)
                                license_filled = True
                                break
                        
                        if license_filled:
                            break
                    except:
                        continue
            
            # Strategy 3: Find and click search button
            search_clicked = False
            if selectors.get("search_btn"):
                try:
                    await page.click(selectors["search_btn"])
                    search_clicked = True
                except Exception as e:
                    logger.debug(f"Configured search button failed for {state}: {e}")
            
            if not search_clicked:
                button_selectors = [
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Search')", "input[value*='Search']",
                    "button:has-text('Find')", "input[value*='Find']",
                    "button:has-text('Lookup')", "input[value*='Lookup']",
                    "button", "input[type='button']"
                ]
                
                for selector in button_selectors:
                    try:
                        elements = await page.query_selector_all(selector)
                        for element in elements:
                            text_content = await element.text_content() or ""
                            value = await element.get_attribute("value") or ""
                            
                            if any(keyword in (text_content + value).lower() 
                                  for keyword in ["search", "find", "lookup", "submit", "go"]):
                                await element.click()
                                search_clicked = True
                                break
                        
                        if search_clicked:
                            break
                    except:
                        continue
            
            # Wait for results
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except:
                await asyncio.sleep(3)  # Fallback wait
            
            # Take screenshot for evidence
            screenshot_bytes = await page.screenshot(full_page=True)
            
            # Extract data intelligently
            content = await page.content()
            
            # Use state-specific patterns or common extraction
            state_patterns = config.get("result_patterns", {})
            
            extracted_data = {
                "business_name": smart_extract_field(content, "business_name", state_patterns),
                "status": smart_extract_field(content, "status", state_patterns),
                "expires": smart_extract_field(content, "expiration", state_patterns),
                "first_licensed": smart_extract_field(content, "first_licensed", state_patterns)
            }
            
            # Additional Oregon-specific extractions
            if state == "OR":
                extracted_data.update({
                    "unpaid_claims": smart_extract_field(content, "unpaid_claims", state_patterns),
                    "complaints": smart_extract_field(content, "complaints", state_patterns),
                    "unpaid_penalties": re.search(r'Unpaid Civil Penalties.*?\$([0-9,.]+)', content, re.IGNORECASE | re.DOTALL),
                    "disciplinary_history": re.search(r'civil penalties.*?suspensions\?\s*([^<\n]+)', content, re.IGNORECASE | re.DOTALL),
                    "administrative_suspensions": re.search(r'CCB ever suspended.*?bond/insurance\?\s*([^<\n]+)', content, re.IGNORECASE | re.DOTALL)
                })
            
            await browser.close()
            
            # Clean up extracted data
            for key, value in extracted_data.items():
                if hasattr(value, 'group'):  # It's a regex match object
                    extracted_data[key] = value.group(1).strip() if value else "Unknown"
                elif not value or value == "Unknown":
                    extracted_data[key] = "Not Available"
            
            return {
                **extracted_data,
                "license_number": license_number,
                "issuing_authority": f"{state} {config.get('type', 'Licensing Board')}",
                "verification_url": config["url"],
                "screenshot_data": base64.b64encode(screenshot_bytes).decode('utf-8'),
                "verified": True,
                "method_used": "adaptive_playwright",
                "extraction_success": extracted_data["status"] != "Unknown",
                "form_interaction": {"license_filled": license_filled, "search_clicked": search_clicked}
            }
            
        except Exception as e:
            if browser:
                await browser.close()
            raise Exception(f"Adaptive Playwright scraper failed for {state}: {str(e)}")

# Update the main intelligent_scraper to use the new adaptive method
async def intelligent_scraper(state: str, config: Dict, license_number: str) -> Dict[str, Any]:
    """Route to the best scraping method for each state"""
    
    method = config.get("method", "playwright")
    
    if method == "requests":
        return await scrape_with_requests(state, config, license_number)
    elif PLAYWRIGHT_AVAILABLE:
        return await adaptive_playwright_scraper(state, config, license_number)
    else:
        return await scrape_with_requests_fallback(state, config, license_number)https://genconbd.alabama.gov/DATABASE-SQL/roster.aspx", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "input[type='submit']", "status": ".status, [class*='status']"}
    },
    "AK": {
        "regex": r"^\d{6}$", "example": "110401", "type": "General Contractor", "format": "6 digits",
        "url": "https://www.commerce.alaska.gov/cbp/main/Search/Professional", "method": "playwright",
        "notes": "Geo/IP locked", "selectors": {"license_input": "#LicenseNumber", "search_btn": "input[value*='Search']"}
    },
    "AZ": {
        "regex": r"^\d{6}$", "example": "321456", "type": "ROC License", "format": "6 digits",
        "url": "https://azroc.my.site.com/AZRoc/s/contractor-search", "method": "playwright",
        "selectors": {"license_input": "input[placeholder*='license']", "search_btn": "button"}
    },
    "AR": {
        "regex": r"^\d{8}$", "example": "2880113", "type": "Commercial Contractor", "format": "8 digits",
        "url": "http://aclb2.arkansas.gov/clbsearch.php", "method": "requests",
        "form_fields": {"license_number": "license_number", "submit": "search"}
    },
    "CA": {
        "regex": r"^\d{6,8}$", "example": "692447", "type": "CSLB Contractor", "format": "6 to 8 digits",
        "url": "https://www.cslb.ca.gov/onlineservices/checklicenseII/checklicense.aspx", "method": "playwright",
        "selectors": {"license_input": "#ctl00_ContentPlaceHolder1_txtLicnum", "search_btn": "#ctl00_ContentPlaceHolder1_btnSearch"},
        "notes": "Bot detection"
    },
    "CO": {
        "regex": r"^\d{2}-\d{6}$", "example": "08-000039", "type": "Trade License", "format": "2-digit prefix + 6 digits",
        "url": "https://dpo.colorado.gov/", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button[type='submit']"}
    },
    "CT": {
        "regex": r"^HIC\.\d{7}$", "example": "HIC.0654321", "type": "Home Improvement Contractor", "format": "HIC. + 7 digits",
        "url": "https://www.elicense.ct.gov/lookup/licenselookup.aspx", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "DE": {
        "regex": r"^\d{10}$", "example": "1990000000", "type": "Business License", "format": "10 digits",
        "url": "https://delpros.delaware.gov/OH_VerifyLicense", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "input[type='submit']"}
    },
    "FL": {
        "regex": r"^CGC\d{7}$", "example": "CGC1524312", "type": "Certified General Contractor", "format": "CGC + 7 digits",
        "url": "https://www.myfloridalicense.com/wl11.asp", "method": "requests",
        "form_fields": {"licnbr": "licnbr", "Submit": "Search"}
    },
    "GA": {
        "url": "https://verify.sos.ga.gov/verification/Search.aspx", "method": "playwright", "type": "Professional License",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "HI": {
        "regex": r"C-\d+", "example": "C-12345", "type": "Professional & Vocational", "format": "C- + number",
        "url": "https://mypvl.dcca.hawaii.gov/public-license-search/", "method": "playwright",
        "selectors": {"license_input": "input[placeholder*='license']", "search_btn": "button[type='submit']"}
    },
    "ID": {
        "regex": r"[A-Z]-\d{5}", "example": "E-12345", "type": "Specialty contractor", "format": "Letter + 5 digits",
        "url": "https://dbs.idaho.gov/contractors/", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "IL": {
        "regex": r"\d{7}", "example": "1234567", "type": "Professional", "format": "7 digits",
        "url": "https://ilesonline.idfpr.illinois.gov/DFPR/Lookup/LicenseLookup.aspx", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "IN": {
        "regex": r"PC\d{6}", "example": "PC123456", "type": "Building & Trades", "format": "PC + 6 digits",
        "url": "https://mylicense.in.gov/everification/Search.aspx", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "IA": {
        "regex": r"\d{5}", "example": "12345", "type": "Contractor registration", "format": "5 digits",
        "url": "https://laborportal.iwd.iowa.gov/iwd_portal/publicSearch/public", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button[type='submit']"}
    },
    "KS": {
        "regex": r"T\d{6}", "example": "T123456", "type": "Technical Professions", "format": "T + 6 digits",
        "url": "https://ksbiz.kansas.gov/business-starter-kit/construction/", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "KY": {
        "regex": r"HBC\d{6}", "example": "HBC123456", "type": "Building/Housing trades", "format": "HBC + 6 digits",
        "url": "https://ky.joportal.com/License/Search", "method": "playwright",
        "selectors": {"license_input": "#LicenseNumber", "search_btn": "#SearchButton"}
    },
    "LA": {
        "regex": r"\d{6}", "example": "123456", "type": "General & Residential Contractors", "format": "6 digits",
        "url": "https://lslbc.louisiana.gov/contractor-search/", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button[type='submit']"}
    },
    "ME": {
        "regex": r"\d{4,5}", "example": "1234", "type": "Electrical & Plumbing", "format": "4-5 digits",
        "url": "https://pfr.maine.gov/ALMSOnline/ALMSQuery/SearchIndividual.aspx", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "MD": {
        "regex": r"\d{2}-\d{6}", "example": "01-123456", "type": "Home Improvement Commission", "format": "XX-XXXXXX",
        "url": "https://www.dllr.state.md.us/cgi-bin/ElectronicLicensing/OP_search/OP_search.cgi?calling_app=HIC::HIC_qselect", 
        "method": "requests", "form_fields": {"license_number": "license_number"}
    },
    "MA": {
        "regex": r"CSL-\d{6}", "example": "CSL-123456", "type": "Construction Supervisor", "format": "CSL-XXXXXX",
        "url": "https://madpl.mylicense.com/Verification/", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "MI": {
        "regex": r"\d{7}", "example": "1234567", "type": "Construction professionals", "format": "7 digits",
        "url": "https://www.michigan.gov/lara/i-need-to/find-or-verify-a-licensed-professional-or-business", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "MN": {
        "regex": r"\d{4,6}", "example": "123456", "type": "Residential contractor & trades", "format": "4-6 digits",
        "url": "https://secure.doli.state.mn.us/lookup/licensing.aspx", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "MS": {
        "regex": r"\d{5}", "example": "12345", "type": "Commercial & Residential Contractors", "format": "5 digits",
        "url": "http://search.msboc.us/ConsolidatedResults.cfm?ContractorType=&VarDatasource=BOC&Advanced=1", 
        "method": "requests", "form_fields": {"license_number": "license_number"}
    },
    "MO": {
        "example": "20231234", "type": "Local Contractor Licensing", "format": "Varies by city",
        "url": "https://pr.mo.gov/licensee-search.asp", "method": "playwright",
        "notes": "City/county level", "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "MT": {
        "regex": r"\d{5}", "example": "12345", "type": "Construction Contractor", "format": "5 digits",
        "url": "https://erdcontractors.mt.gov/ICCROnlineSearch/registrationlookup.jsp", "method": "playwright",
        "selectors": {"license_input": "input[name*='registration']", "search_btn": "input[type='submit']"}
    },
    "NE": {
        "regex": r"\d{5}", "example": "12345", "type": "Contractor Registration", "format": "5 digits",
        "url": "https://dol.nebraska.gov/conreg/Search", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "NV": {
        "regex": r"\d{6}", "example": "123456", "type": "State Contractors Board", "format": "6 digits",
        "url": "https://app.nvcontractorsboard.com/Clients/NVSCB/Public/ContractorLicenseSearch/ContractorLicenseSearch.aspx", 
        "method": "playwright", "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "NH": {
        "regex": r"\d{6}", "example": "123456", "type": "Licensed trades", "format": "6 digits",
        "url": "https://oplc.nh.gov/license-lookup", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "NJ": {
        "regex": r"\d{7}", "example": "1234567", "type": "Home Improvement Contractor", "format": "7 digits",
        "url": "https://newjersey.mylicense.com/verification/Search.aspx?facility=Y", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "NM": {
        "regex": r"\d{6}", "example": "123456", "type": "Construction Industries Division", "format": "6 digits",
        "url": "https://public.psiexams.com/search.jsp", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "input[type='submit']"}
    },
    "NY": {
        "example": "123456", "type": "Municipal Licensing", "format": "Varies by municipality",
        "url": "https://appext20.dos.ny.gov/lcns_public/licenseesearch/lcns_public_index.cfm", "method": "playwright",
        "notes": "No statewide license", "selectors": {"license_input": "input[name*='license']", "search_btn": "input[type='submit']"}
    },
    "NC": {
        "regex": r"\d{5}", "example": "12345", "type": "General Contractor", "format": "5 digits",
        "url": "https://portal.nclbgc.org/Public/Search", "method": "playwright",
        "selectors": {"license_input": "#LicenseNumber", "search_btn": "#SearchButton"}
    },
    "ND": {
        "regex": r"\d{5}", "example": "12345", "type": "State Contractor License", "format": "5 digits",
        "url": "https://firststop.sos.nd.gov/search/contractor", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "OH": {
        "regex": r"[A-Z]{2}\d{6}", "example": "HV123456", "type": "Commercial Trades", "format": "2 letters + 6 digits",
        "url": "https://elicense3.com.ohio.gov/", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "OK": {
        "regex": r"\d{6}", "example": "123456", "type": "Construction Industries Board", "format": "6 digits",
        "url": "https://okcibv7prod.glsuite.us/GLSuiteWeb/Clients/OKCIB/Public/LicenseeSearch/LicenseeSearch.aspx", 
        "method": "playwright", "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "OR": {
        "regex": r"^\d{6}$", "example": "195480", "type": "Construction Contractors Board", "format": "6 digits",
        "url": "https://search.ccb.state.or.us/search/", "method": "playwright",
        "selectors": {"license_input": "input[name='license_number']", "search_btn": "input[type='submit']"},
        "result_patterns": {
            "business_name": r"CCB License Summary:.*?<[^>]*>(.*?)<",
            "status": r"Status:\s*([^<\n]+)",
            "first_licensed": r"First Licensed:\s*([^<\n]+)",
            "unpaid_claims": r"Unpaid Claims.*?\$([0-9,.]+)",
            "complaints": r"Any complaints.*?contractor\?\s*([^<\n]+)"
        }
    },
    "PA": {
        "regex": r"PA\d{6}", "example": "PA123456", "type": "Home Improvement Contractor", "format": "PA + 6 digits",
        "url": "https://hicsearch.attorneygeneral.gov/", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "RI": {
        "example": "12345", "type": "Contractor Registration", "format": "5 digits",
        "url": "https://crb.ri.gov/consumer/search-registrantlicensee", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "SC": {
        "regex": r"CLG\d{6}", "example": "CLG123456", "type": "Contractor's Licensing Board", "format": "CLG + 6 digits",
        "url": "https://verify.llronline.com/LicLookup/Contractors/Contractor.aspx?div=69", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "SD": {
        "regex": r"\d{5}", "example": "12345", "type": "Electrical, Plumbing", "format": "5 digits",
        "url": "https://sdec.portalus.thentiacloud.net/webs/portal/register/#/", "method": "playwright",
        "selectors": {"license_input": "input[placeholder*='license']", "search_btn": "button"}
    },
    "TN": {
        "regex": r"\d{6}", "example": "123456", "type": "Commercial & Residential", "format": "6 digits",
        "url": "https://www.tn.gov/commerce/regboards/contractor.html", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "TX": {
        "regex": r"\d{5,6}", "example": "12345", "type": "TDLR License", "format": "5-6 digits",
        "url": "https://www.tdlr.texas.gov/LicenseSearch/", "method": "playwright",
        "selectors": {"license_input": "#LicenseNumber", "search_btn": "#SearchButton"}
    },
    "UT": {
        "regex": r"\d{6}-\d{4}", "example": "123456-5501", "type": "Contractor", "format": "XXXXXX-XXXX",
        "url": "https://secure.utah.gov/llv/search/index.html", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "VT": {
        "example": "456789", "type": "Contractor Registration", "format": "Varies",
        "url": "https://sos.vermont.gov/opr/find-a-professional/", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "VA": {
        "regex": r"2705\d{6}", "example": "2710000000", "type": "Class A/B/C Contractors", "format": "2705 + 6 digits",
        "url": "https://www.dpor.virginia.gov/LicenseLookup/", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "WA": {
        "regex": r"[A-Z]{3}\d{4}", "example": "ABC1234", "type": "Contractor Registration", "format": "3 letters + 4 digits",
        "url": "https://secure.lni.wa.gov/verify/", "method": "playwright",
        "selectors": {"license_input": "input[name='licenseNumber']", "search_btn": "input[value='Search']"}
    },
    "WV": {
        "regex": r"WV\d{6}", "example": "WV012345", "type": "Contractors Licensing Board", "format": "WV + 6 digits",
        "url": "https://wvclboard.wv.gov/verify/", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "WI": {
        "regex": r"\d{6}", "example": "123456", "type": "Dwelling Contractor", "format": "6 digits",
        "url": "https://dsps.wi.gov/Pages/Professions/Default.aspx", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "WY": {
        "regex": r"\d{5}", "example": "12345", "type": "Local Licensing", "format": "5 digits",
        "url": "https://doe.state.wy.us/lmi/licensed_occupations.htm", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    }
}

async def intelligent_scraper(state: str, config: Dict, license_number: str) -> Dict[str, Any]:
    """Intelligent scraper that adapts to each state's unique structure"""
    
    method = config.get("method", "playwright")
    
    if method == "requests":
        return await scrape_with_requests(state, config, license_number)
    else:
        return await scrape_with_playwright(state, config, license_number)

async def scrape_with_playwright(state: str, config: Dict, license_number: str) -> Dict[str, Any]:
    """Advanced Playwright scraper with state-specific logic"""
    
    if not PLAYWRIGHT_AVAILABLE:
        return await scrape_with_requests_fallback(state, config, license_number)
    
    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
            )
            
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'}
            )
            page = await context.new_page()
            
            # Navigate to state website
            await page.goto(config["url"], wait_until="networkidle", timeout=30000)
            
            # State-specific form filling logic
            selectors = config.get("selectors", {})
            
            # Find and fill license input field
            license_input_selector = selectors.get("license_input")
            if license_input_selector:
                try:
                    await page.fill(license_input_selector, license_number)
                except:
                    # Fallback: try common input selectors
                    fallback_selectors = [
                        "input[name*='license']", "input[id*='license']", 
                        "input[placeholder*='license']", "input[type='text']"
                    ]
                    for selector in fallback_selectors:
                        try:
                            await page.fill(selector, license_number)
                            break
                        except:
                            continue
            
            # Find and click search button
            search_btn_selector = selectors.get("search_btn")
            if search_btn_selector:
                try:
                    await page.click(search_btn_selector)
                except:
                    # Fallback: try common button selectors
                    fallback_buttons = [
                        "button[type='submit']", "input[type='submit']",
                        "button:has-text('Search')", "input[value*='Search']"
                    ]
                    for selector in fallback_buttons:
                        try:
                            await page.click(selector)
                            break
                        except:
                            continue
            
            # Wait for results
            await page.wait_for_load_state("networkidle", timeout=20000)
            
            # Take screenshot
            screenshot_bytes = await page.screenshot(full_page=True)
            
            # Extract data using state-specific patterns
            content = await page.content()
            extracted_data = extract_license_data(state, content, config)
            
            await browser.close()
            
            return {
                **extracted_data,
                "license_number": license_number,
                "verification_url": config["url"],
                "screenshot_data": base64.b64encode(screenshot_bytes).decode('utf-8'),
                "verified": True,
                "method_used": "playwright_intelligent",
                "state": state,
                "license_type": config.get("type", "Unknown")
            }
            
        except Exception as e:
            if browser:
                await browser.close()
            # Fallback to requests method
            logger.warning(f"Playwright failed for {state}, trying requests fallback: {e}")
            return await scrape_with_requests_fallback(state, config, license_number)

def extract_license_data(state: str, html_content: str, config: Dict) -> Dict[str, Any]:
    """Extract license data using state-specific patterns"""
    
    content_lower = html_content.lower()
    result = {
        "status": "Unknown",
        "business_name": "Unknown", 
        "expires": "Unknown"
    }
    
    # Use state-specific result patterns if available
    patterns = config.get("result_patterns", {})
    
    if state == "OR" and patterns:
        # Oregon-specific extraction
        for field, pattern in patterns.items():
            match = re.search(pattern, html_content, re.IGNORECASE | re.DOTALL)
            if match:
                result[field] = match.group(1).strip()
        
        # Set status based on Oregon's format
        if result.get("status", "").lower() == "active":
            result["status"] = "Active"
        elif "not active" in result.get("status", "").lower() or "expired" in result.get("status", "").lower():
            result["status"] = "Expired"
    
    else:
        # Generic extraction for other states
        
        # Extract business/company name
        name_patterns = [
            r'<h[1-6][^>]*>([^<]*(?:LLC|INC|CORP|COMPANY|CONTRACTORS?|CONSTRUCTION)[^<]*)</h[1-6]>',
            r'business name[:\s]*([^<\n\r]+)',
            r'company name[:\s]*([^<\n\r]+)',
            r'contractor name[:\s]*([^<\n\r]+)',
            r'name[:\s]*([A-Z][^<\n\r,]{3,})'
        ]
        
        for pattern in name_patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                if len(name) > 3 and not name.isdigit():
                    result["business_name"] = name
                    break
        
        # Extract status
        status_patterns = [
            r'status[:\s]*([^<\n\r]+)',
            r'license status[:\s]*([^<\n\r]+)',
            r'current status[:\s]*([^<\n\r]+)'
        ]
        
        for pattern in status_patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                status_text = match.group(1).strip().lower()
                if "active" in status_text or "valid" in status_text or "current" in status_text:
                    result["status"] = "Active"
                elif "expired" in status_text or "inactive" in status_text:
                    result["status"] = "Expired"
                elif "suspended" in status_text or "revoked" in status_text:
                    result["status"] = "Suspended"
                else:
                    result["status"] = match.group(1).strip()
                break
        
        # If no specific status found, use content analysis
        if result["status"] == "Unknown":
            if any(word in content_lower for word in ["active", "valid", "current", "good standing"]):
                result["status"] = "Active"
            elif any(word in content_lower for word in ["expired", "inactive", "lapsed"]):
                result["status"] = "Expired"
            elif any(word in content_lower for word in ["invalid", "not found", "no results"]):
                result["status"] = "Invalid"
            elif any(word in content_lower for word in ["suspended", "revoked"]):
                result["status"] = "Suspended"
        
        # Extract expiration date
        date_patterns = [
            r'expir[a-z]*[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
            r'expires[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
            r'valid through[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
            r'renewal date[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})'
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                result["expires"] = match.group(1)
                break
    
    return result

async def scrape_with_requests_fallback(state: str, config: Dict, license_number: str) -> Dict[str, Any]:
    """Fallback requests method for when Playwright fails"""
    
    async with aiohttp.ClientSession() as session:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9'
            }
            
            # Try different request approaches based on state
            if state == "FL":
                # Florida specific GET request
                search_url = f"https://www.myfloridalicense.com/wl11.asp?mode=0&licnbr={license_number}"
                async with session.get(search_url, headers=headers) as response:
                    html_content = await response.text()
            
            elif state == "AR" or state == "MD" or state == "MS":
                # States that use POST forms
                form_fields = config.get("form_fields", {})
                form_data = {}
                for field_name, field_key in form_fields.items():
                    if field_key == "license_number":
                        form_data[field_name] = license_number
                    else:
                        form_data[field_name] = field_key
                
                async with session.post(config["url"], data=form_data, headers=headers) as response:
                    html_content = await response.text()
            
            else:
                # Generic GET request with license parameter
                params = {'license': license_number, 'search': '1', 'q': license_number}
                async with session.get(config["url"], params=params, headers=headers) as response:
                    html_content = await response.text()
            
            # Extract data using intelligent parsing
            extracted_data = extract_license_data(state, html_content, config)
            
            return {
                **extracted_data,
                "license_number": license_number,
                "issuing_authority": f"{state} {config.get('type', 'Licensing Board')}",
                "verification_url": config["url"],
                "verified": extracted_data["status"] != "Unknown",
                "method_used": "requests_fallback",
                "state": state,
                "license_type": config.get("type", "Unknown"),
                "note": "Limited data extraction - upgrade to Playwright for full details"
            }
            
        except Exception as e:
            raise Exception(f"Requests fallback failed for {state}: {str(e)}")

async def scrape_with_requests(state: str, config: Dict, license_number: str) -> Dict[str, Any]:
    """Enhanced requests method for simple form-based sites"""
    
    async with aiohttp.ClientSession() as session:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': config["url"]
            }
            
            # State-specific request logic
            if state == "FL":
                # Florida MyFloridaLicense
                search_url = f"https://www.myfloridalicense.com/wl11.asp?mode=0&licnbr={license_number}"
                async with session.get(search_url, headers=headers) as response:
                    html_content = await response.text()
                    
                # Parse Florida's table-based results
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Look for license data in tables
                license_info = {}
                for table in soup.find_all('table'):
                    for row in table.find_all('tr'):
                        cells = row.find_all(['td', 'th'])
                        if len(cells) >= 2:
                            key = cells[0].get_text(strip=True).lower()
                            value = cells[1].get_text(strip=True)
                            if key and value:
                                license_info[key] = value
                
                # Extract Florida-specific fields
                status = license_info.get('license status', license_info.get('status', 'Unknown'))
                business_name = license_info.get('business name', license_info.get('name', 'Unknown'))
                expires = license_info.get('expiration date', license_info.get('expires', 'Unknown'))
                
                return {
                    "status": status,
                    "business_name": business_name,
                    "expires": expires,
                    "license_number": license_number,
                    "issuing_authority": "Florida Department of Business and Professional Regulation",
                    "verification_url": config["url"],
                    "verified": True,
                    "method_used": "requests_florida_specific",
                    "state": "FL",
                    "license_type": "Certified General Contractor",
                    "raw_data": license_info
                }
            
            else:
                # Generic form submission for other request-based states
                form_fields = config.get("form_fields", {})
                form_data = {}
                
                for field_name, field_value in form_fields.items():
                    if "license" in field_name.lower():
                        form_data[field_name] = license_number
                    else:
                        form_data[field_name] = field_value
                
                async with session.post(config["url"], data=form_data, headers=headers) as response:
                    html_content = await response.text()
                
                extracted_data = extract_license_data(state, html_content, config)
                
                return {
                    **extracted_data,
                    "license_number": license_number,
                    "issuing_authority": f"{state} {config.get('type', 'Licensing Board')}",
                    "verification_url": config["url"],
                    "verified": extracted_data["status"] != "Unknown",
                    "method_used": "requests_form_submission",
                    "state": state,
                    "license_type": config.get("type", "Unknown")
                }
            
        except Exception as e:
            raise Exception(f"Requests method failed for {state}: {str(e)}")

async def verify_license(state: str, license_number: Optional[str] = None, business_name: Optional[str] = None) -> Dict[str, Any]:
    """Main verification function with intelligent routing"""
    
    if not state or not license_number:
        raise Exception("State and license number are required")
    
    state = state.upper()
    
    # Normalize license number
    license_number = normalize_license_number(state, license_number)
    
    # Check cache
    cache_key = f"{state}_{license_number}"
    try:
        cached = get_cached_result(cache_key)
        if cached:
            cached["from_cache"] = True
            return cached
    except Exception as e:
        logger.warning(f"Cache error: {e}")
    
    # Validate state support
    if state not in STATE_CONFIGS:
        return {
            "status": "Unsupported",
            "message": f"State {state} not supported yet",
            "supported_states": list(STATE_CONFIGS.keys()),
            "verified": False
        }
    
    config = STATE_CONFIGS[state]
    
    # Validate format
    if config.get("regex") and not re.match(config["regex"], license_number):
        return {
            "status": "Invalid Format",
            "message": f"License '{license_number}' doesn't match {state} format: {config['format']}",
            "example": config["example"],
            "verified": False
        }
    
    try:
        # Route to appropriate scraper
        result = await intelligent_scraper(state, config, license_number)
        
        # Cache successful results
        try:
            store_result(cache_key, result)
        except Exception as e:
            logger.warning(f"Cache store failed: {e}")
        
        return result
        
    except Exception as e:
        return {
            "status": "Error",
            "message": str(e),
            "license_number": license_number,
            "state": state,
            "verified": False,
            "verification_url": config["url"]
        }

def normalize_license_number(state: str, license_number: str) -> str:
    """State-specific license number normalization"""
    state = state.upper()
    license_number = license_number.strip().upper()
    
    # State-specific prefixes and formatting
    normalizations = {
        "FL": lambda x: f"CGC{x}" if x.isdigit() and not x.startswith("CGC") else x,
        "PA": lambda x: f"PA{x}" if x.isdigit() and not x.startswith("PA") else x,
        "WV": lambda x: f"WV{x}" if x.isdigit() and not x.startswith("WV") else x,
        "CT": lambda x: f"HIC.{x}" if x.isdigit() and not x.startswith("HIC.") else x,
        "SC": lambda x: f"CLG{x}" if x.isdigit() and not x.startswith("CLG") else x,
        "KY": lambda x: f"HBC{x}" if x.isdigit() and not x.startswith("HBC") else x,
        "IN": lambda x: f"PC{x}" if x.isdigit() and not x.startswith("PC") else x,
        "KS": lambda x: f"T{x}" if x.isdigit() and not x.startswith("T") else x,
        "MA": lambda x: f"CSL-{x}" if x.isdigit() and not x.startswith("CSL-") else x,
        "VA": lambda x: f"2705{x}" if x.isdigit() and len(x) == 6 and not x.startswith("2705") else x,
        "CA": lambda x: re.sub(r'\D', '', x)  # Remove non-digits for CA
    }
    
    if state in normalizations:
        license_number = normalizations[state](license_number)
    
    return license_number

async def verify_batch(requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Efficient batch processing with smart grouping"""
    
    # Group by state and method for optimal processing
    state_groups = {}
    for i, request in enumerate(requests):
        state = request.get("state", "").upper()
        if state not in state_groups:
            state_groups[state] = []
        state_groups[state].append((i, request))
    
    results = [None] * len(requests)
    
    # Process each state group
    for state, state_requests in state_groups.items():
        config = STATE_CONFIGS.get(state, {})
        method = config.get("method", "playwright")
        
        # Adjust delay based on method and state notes
        delay = 2  # Default delay
        if config.get("notes") and "bot detection" in config["notes"].lower():
            delay = 5  # Longer delay for bot-protected sites
        elif method == "requests":
            delay = 1  # Shorter delay for simple requests
        
        for i, (original_index, request) in enumerate(state_requests):
            try:
                if i > 0:
                    await asyncio.sleep(delay)
                
                result = await verify_license(
                    request.get("state"),
                    request.get("license_number"),
                    request.get("business_name")
                )
                results[original_index] = result
                
            except Exception as e:
                results[original_index] = {
                    "status": "Error",
                    "message": str(e),
                    "license_number": request.get("license_number"),
                    "state": request.get("state"),
                    "verified": False
                }
        
        # Delay between states
        await asyncio.sleep(1)
    
    return results

def get_supported_states() -> Dict[str, Dict[str, Any]]:
    """Get all supported states with implementation status"""
    return {
        state: {
            "type": config.get("type", "Professional License"),
            "format": config.get("format", "Contact state for format"),
            "example": config.get("example", "N/A"),
            "method": config.get("method", "planned"),
            "implementation_status": "implemented" if config.get("selectors") or config.get("form_fields") else "basic",
            "notes": config.get("notes"),
            "requires_playwright": config.get("method") == "playwright"
        }
        for state, config in STATE_CONFIGS.items()
    }

def validate_license_format(state: str, license_number: str) -> Dict[str, Any]:
    """Validate license format for any state"""
    state = state.upper()
    
    if state not in STATE_CONFIGS:
        return {
            "valid": False,
            "error": f"State {state} not supported",
            "supported_states": list(STATE_CONFIGS.keys())
        }
    
    config = STATE_CONFIGS[state]
    regex_pattern = config.get("regex")
    
    if not regex_pattern:
        return {
            "valid": True,
            "warning": f"No strict format validation for {state}",
            "format_info": config.get("format", "Varies")
        }
    
    is_valid = bool(re.match(regex_pattern, license_number))
    
    return {
        "valid": is_valid,
        "expected_format": config["format"],
        "example": config["example"],
        "notes": config.get("notes")
    }

def get_state_info(state: str) -> Dict[str, Any]:
    """Get detailed state information"""
    state = state.upper()
    if state not in STATE_CONFIGS:
        return {"error": f"State {state} not supported"}
    
    config = STATE_CONFIGS[state]
    return {
        "state": state,
        "license_type": config.get("type", "Professional License"),
        "format": config.get("format", "Contact state"),
        "example": config.get("example", "N/A"),
        "verification_url": config["url"],
        "method": config.get("method", "planned"),
        "regex": config.get("regex"),
        "notes": config.get("notes"),
        "implementation_level": "full" if config.get("result_patterns") else "basic"
    }

def get_system_status() -> Dict[str, Any]:
    """Get system capabilities"""
    implemented_count = len([s for s, c in STATE_CONFIGS.items() if c.get("selectors") or c.get("form_fields")])
    
    return {
        "status": "operational",
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "total_states": len(STATE_CONFIGS),
        "implemented_states": implemented_count,
        "basic_states": len(STATE_CONFIGS) - implemented_count,
        "version": "4.2-all-states",
        "approach": "intelligent_adaptive_scraping"
    }

# Add remaining states to reach 50
STATE_CONFIGS.update({
    "AK": {
        "regex": r"^\d{6}$", "example": "110401", "type": "General Contractor", "format": "6 digits",
        "url": "https://www.commerce.alaska.gov/cbp/main/Search/Professional", "method": "playwright",
        "notes": "May require VPN", "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "AZ": {
        "regex": r"^\d{6}$", "example": "321456", "type": "ROC License", "format": "6 digits", 
        "url": "https://azroc.my.site.com/AZRoc/s/contractor-search", "method": "playwright",
        "selectors": {"license_input": "input[placeholder*='license']", "search_btn": "button"}
    },
    "CO": {
        "regex": r"^\d{2}-\d{6}$", "example": "08-000039", "type": "Trade License", "format": "XX-XXXXXX",
        "url": "https://dpo.colorado.gov/", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "HI": {
        "regex": r"C-\d+", "example": "C-12345", "type": "Professional & Vocational", "format": "C-XXXXX",
        "url": "https://mypvl.dcca.hawaii.gov/public-license-search/", "method": "playwright",
        "selectors": {"license_input": "input[placeholder*='license']", "search_btn": "button"}
    },
    "ID": {
        "regex": r"[A-Z]-\d{5}", "example": "E-12345", "type": "Specialty contractor", "format": "X-XXXXX",
        "url": "https://dbs.idaho.gov/contractors/", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "IL": {
        "regex": r"\d{7}", "example": "1234567", "type": "Professional", "format": "7 digits",
        "url": "https://ilesonline.idfpr.illinois.gov/DFPR/Lookup/LicenseLookup.aspx", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "IN": {
        "regex": r"PC\d{6}", "example": "PC123456", "type": "Building & Trades", "format": "PC + 6 digits",
        "url": "https://mylicense.in.gov/everification/Search.aspx", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "IA": {
        "regex": r"\d{5}", "example": "12345", "type": "Contractor registration", "format": "5 digits",
        "url": "https://laborportal.iwd.iowa.gov/iwd_portal/publicSearch/public", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "KS": {
        "regex": r"T\d{6}", "example": "T123456", "type": "Technical Professions", "format": "T + 6 digits",
        "url": "https://ksbiz.kansas.gov/business-starter-kit/construction/", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "KY": {
        "regex": r"HBC\d{6}", "example": "HBC123456", "type": "Building/Housing trades", "format": "HBC + 6 digits",
        "url": "https://ky.joportal.com/License/Search", "method": "playwright",
        "selectors": {"license_input": "#LicenseNumber", "search_btn": "#SearchButton"}
    },
    "LA": {
        "regex": r"\d{6}", "example": "123456", "type": "General & Residential Contractors", "format": "6 digits",
        "url": "https://lslbc.louisiana.gov/contractor-search/", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "ME": {
        "regex": r"\d{4,5}", "example": "1234", "type": "Electrical & Plumbing", "format": "4-5 digits",
        "url": "https://pfr.maine.gov/ALMSOnline/ALMSQuery/SearchIndividual.aspx", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "MA": {
        "regex": r"CSL-\d{6}", "example": "CSL-123456", "type": "Construction Supervisor", "format": "CSL-XXXXXX",
        "url": "https://madpl.mylicense.com/Verification/", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "MI": {
        "regex": r"\d{7}", "example": "1234567", "type": "Construction professionals", "format": "7 digits",
        "url": "https://www.michigan.gov/lara/i-need-to/find-or-verify-a-licensed-professional-or-business", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "MN": {
        "regex": r"\d{4,6}", "example": "123456", "type": "Residential contractor & trades", "format": "4-6 digits",
        "url": "https://secure.doli.state.mn.us/lookup/licensing.aspx", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "MO": {
        "example": "20231234", "type": "Local Contractor Licensing", "format": "Varies by city",
        "url": "https://pr.mo.gov/licensee-search.asp", "method": "playwright",
        "notes": "City/county level", "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "MT": {
        "regex": r"\d{5}", "example": "12345", "type": "Construction Contractor", "format": "5 digits",
        "url": "https://erdcontractors.mt.gov/ICCROnlineSearch/registrationlookup.jsp", "method": "playwright",
        "selectors": {"license_input": "input[name*='registration']", "search_btn": "input[type='submit']"}
    },
    "NE": {
        "regex": r"\d{5}", "example": "12345", "type": "Contractor Registration", "format": "5 digits",
        "url": "https://dol.nebraska.gov/conreg/Search", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "NV": {
        "regex": r"\d{6}", "example": "123456", "type": "State Contractors Board", "format": "6 digits",
        "url": "https://app.nvcontractorsboard.com/Clients/NVSCB/Public/ContractorLicenseSearch/ContractorLicenseSearch.aspx", 
        "method": "playwright", "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "NH": {
        "regex": r"\d{6}", "example": "123456", "type": "Licensed trades", "format": "6 digits",
        "url": "https://oplc.nh.gov/license-lookup", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "NJ": {
        "regex": r"\d{7}", "example": "1234567", "type": "Home Improvement Contractor", "format": "7 digits",
        "url": "https://newjersey.mylicense.com/verification/Search.aspx?facility=Y", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "NM": {
        "regex": r"\d{6}", "example": "123456", "type": "Construction Industries Division", "format": "6 digits",
        "url": "https://public.psiexams.com/search.jsp", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "input[type='submit']"}
    },
    "NY": {
        "example": "123456", "type": "Municipal Licensing", "format": "Varies",
        "url": "https://appext20.dos.ny.gov/lcns_public/licenseesearch/lcns_public_index.cfm", "method": "playwright",
        "notes": "No statewide license", "selectors": {"license_input": "input[name*='license']", "search_btn": "input[type='submit']"}
    },
    "NC": {
        "regex": r"\d{5}", "example": "12345", "type": "General Contractor", "format": "5 digits",
        "url": "https://portal.nclbgc.org/Public/Search", "method": "playwright",
        "selectors": {"license_input": "#LicenseNumber", "search_btn": "#SearchButton"}
    },
    "ND": {
        "regex": r"\d{5}", "example": "12345", "type": "State Contractor License", "format": "5 digits",
        "url": "https://firststop.sos.nd.gov/search/contractor", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "OH": {
        "regex": r"[A-Z]{2}\d{6}", "example": "HV123456", "type": "Commercial Trades", "format": "XX + 6 digits",
        "url": "https://elicense3.com.ohio.gov/", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "OK": {
        "regex": r"\d{6}", "example": "123456", "type": "Construction Industries Board", "format": "6 digits",
        "url": "https://okcibv7prod.glsuite.us/GLSuiteWeb/Clients/OKCIB/Public/LicenseeSearch/LicenseeSearch.aspx", 
        "method": "playwright", "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "SD": {
        "regex": r"\d{5}", "example": "12345", "type": "Electrical, Plumbing", "format": "5 digits",
        "url": "https://sdec.portalus.thentiacloud.net/webs/portal/register/#/", "method": "playwright",
        "selectors": {"license_input": "input[placeholder*='license']", "search_btn": "button"}
    },
    "TN": {
        "regex": r"\d{6}", "example": "123456", "type": "Commercial & Residential", "format": "6 digits",
        "url": "https://www.tn.gov/commerce/regboards/contractor.html", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "TX": {
        "regex": r"\d{5,6}", "example": "12345", "type": "TDLR License", "format": "5-6 digits",
        "url": "https://www.tdlr.texas.gov/LicenseSearch/", "method": "playwright",
        "selectors": {"license_input": "#LicenseNumber", "search_btn": "#SearchButton"}
    },
    "UT": {
        "regex": r"\d{6}-\d{4}", "example": "123456-5501", "type": "Contractor", "format": "XXXXXX-XXXX",
        "url": "https://secure.utah.gov/llv/search/index.html", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "VT": {
        "example": "456789", "type": "Contractor Registration", "format": "Varies",
        "url": "https://sos.vermont.gov/opr/find-a-professional/", "method": "playwright",
        "selectors": {"license_input": "input[name*='license']", "search_btn": "button"}
    },
    "VA": {
        "regex": r"2705\d{6}", "example": "2710000000", "type": "Class A/B/C Contractors", "format": "2705 + 6 digits",
        "url": "https://www.dpor.virginia.gov/LicenseLookup/", "method": "playwright",
        "selectors": {"license_input": "#txtLicenseNumber", "search_btn": "#btnSearch"}
    },
    "WA": {
        "regex": r"[A-Z]{3}\d{4}", "example": "ABC1234", "type": "Contractor Registration", "format": "XXX + 4 digits",
        "url": "
