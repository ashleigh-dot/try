import re
import asyncio
from typing import Optional, Dict, Any, List
import base64
import aiohttp
from bs4 import BeautifulSoup
from cache import get_cached_result, store_result

# Try to import Playwright, but continue without it if not available
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("Warning: Playwright not available, using requests fallback for all states")

# Complete state configurations with license formats and verification URLs
STATE_CONFIGS = {
    "AL": {
        "regex": r"^\d{5}$",
        "example": "55289",
        "type": "General Contractor",
        "format": "5 digits",
        "url": "https://genconbd.alabama.gov/DATABASE-SQL/roster.aspx",
        "method": "playwright",
        "fallback_url": "https://genconbd.alabama.gov/DATABASE-SQL/roster.aspx"
    },
    "AK": {
        "regex": r"^\d{6}$",
        "example": "110401",
        "type": "General Contractor",
        "format": "6 digits",
        "url": "https://www.commerce.alaska.gov/cbp/main/Search/Professional",
        "method": "playwright",
        "notes": "Geo/IP locked; VPN may be required"
    },
    "AZ": {
        "regex": r"^\d{6}$",
        "example": "321456",
        "type": "ROC License",
        "format": "6 digits",
        "url": "https://azroc.my.site.com/AZRoc/s/contractor-search",
        "method": "playwright"
    },
    "AR": {
        "regex": r"^\d{8}$",
        "example": "2880113",
        "type": "Commercial Contractor",
        "format": "8 digits, leading zeros allowed",
        "url": "http://aclb2.arkansas.gov/clbsearch.php",
        "method": "requests",
        "notes": "Session ID stripped from link"
    },
    "CA": {
        "regex": r"^\d{6,8}$",
        "example": "927123",
        "type": "CSLB Contractor",
        "format": "6 to 8 digits",
        "url": "https://www.cslb.ca.gov/onlineservices/checklicenseII/checklicense.aspx",
        "method": "playwright",
        "notes": "Bot detection; use ModHeader with Referrer spoof"
    },
    "CO": {
        "regex": r"^\d{2}-\d{6}$",
        "example": "08-000039",
        "type": "Trade License",
        "format": "2-digit prefix + 6-digit number",
        "url": "https://dpo.colorado.gov/",
        "method": "playwright",
        "notes": "State licenses only for specific trades"
    },
    "CT": {
        "regex": r"^HIC\.\d{7}$",
        "example": "HIC.0654321",
        "type": "Home Improvement Contractor",
        "format": "Prefix 'HIC.' + 7 digits",
        "url": "https://www.elicense.ct.gov/lookup/licenselookup.aspx",
        "method": "playwright",
        "notes": "General contractors register, not licensed"
    },
    "DE": {
        "regex": r"^\d{10}$",
        "example": "1990000000",
        "type": "Business License",
        "format": "10 digits",
        "url": "https://delpros.delaware.gov/OH_VerifyLicense",
        "method": "playwright"
    },
    "FL": {
        "regex": r"^CGC\d{7}$",
        "example": "CGC1524312",
        "type": "Certified General Contractor",
        "format": "Prefix CGC + 7 digits",
        "url": "https://www.myfloridalicense.com/wl11.asp",
        "method": "requests",
        "notes": "Session-based link"
    },
    "GA": {
        "url": "https://verify.sos.ga.gov/verification/Search.aspx",
        "method": "playwright",
        "type": "Professional License"
    },
    "HI": {
        "regex": r"C-\d+",
        "example": "C-12345",
        "type": "Professional & Vocational",
        "format": "Letter + number",
        "url": "https://mypvl.dcca.hawaii.gov/public-license-search/",
        "method": "playwright"
    },
    "ID": {
        "regex": r"[A-Z]-\d{5}",
        "example": "E-12345",
        "type": "Specialty contractor",
        "format": "Letter prefix + 5 digits",
        "url": "https://dbs.idaho.gov/contractors/",
        "method": "playwright"
    },
    "IL": {
        "regex": r"\d{7}",
        "example": "1234567",
        "type": "Professional",
        "format": "Seven-digit ID",
        "url": "https://ilesonline.idfpr.illinois.gov/DFPR/Lookup/LicenseLookup.aspx",
        "method": "playwright"
    },
    "IN": {
        "regex": r"PC\d{6}",
        "example": "PC123456",
        "type": "Building & Trades",
        "format": "Prefix + six digits",
        "url": "https://mylicense.in.gov/everification/Search.aspx",
        "method": "playwright"
    },
    "IA": {
        "regex": r"\d{5}",
        "example": "12345",
        "type": "Contractor registration",
        "format": "Five-digit number",
        "url": "https://laborportal.iwd.iowa.gov/iwd_portal/publicSearch/public",
        "method": "playwright"
    },
    "KS": {
        "regex": r"T\d{6}",
        "example": "T123456",
        "type": "Technical Professions",
        "format": "T-prefix + six digits",
        "url": "https://ksbiz.kansas.gov/business-starter-kit/construction/",
        "method": "playwright"
    },
    "KY": {
        "regex": r"HBC\d{6}",
        "example": "HBC123456",
        "type": "Building/Housing trades",
        "format": "Prefix + six digits",
        "url": "https://ky.joportal.com/License/Search",
        "method": "playwright"
    },
    "LA": {
        "regex": r"\d{6}",
        "example": "123456",
        "type": "General & Residential Contractors",
        "format": "Six-digit ID",
        "url": "https://lslbc.louisiana.gov/contractor-search/",
        "method": "playwright"
    },
    "ME": {
        "regex": r"\d{4,5}",
        "example": "1234",
        "type": "Electrical & Plumbing",
        "format": "Four to five digit ID",
        "url": "https://pfr.maine.gov/ALMSOnline/ALMSQuery/SearchIndividual.aspx",
        "method": "playwright"
    },
    "MD": {
        "regex": r"\d{2}-\d{6}",
        "example": "01-123456",
        "type": "Home Improvement Commission",
        "format": "Two digit prefix + dash + six digits",
        "url": "https://www.dllr.state.md.us/cgi-bin/ElectronicLicensing/OP_search/OP_search.cgi?calling_app=HIC::HIC_qselect",
        "method": "requests"
    },
    "MA": {
        "regex": r"CSL-\d{6}",
        "example": "CSL-123456",
        "type": "Construction Supervisor",
        "format": "Prefix 'CSL-' + six digits",
        "url": "https://madpl.mylicense.com/Verification/",
        "method": "playwright"
    },
    "MI": {
        "regex": r"\d{7}",
        "example": "1234567",
        "type": "Construction professionals",
        "format": "Seven-digit numeric ID",
        "url": "https://www.michigan.gov/lara/i-need-to/find-or-verify-a-licensed-professional-or-business",
        "method": "playwright"
    },
    "MN": {
        "regex": r"\d{4,6}",
        "example": "123456",
        "type": "Residential contractor & trades",
        "format": "Four to six-digit numeric ID",
        "url": "https://secure.doli.state.mn.us/lookup/licensing.aspx",
        "method": "playwright"
    },
    "MS": {
        "regex": r"\d{5}",
        "example": "12345",
        "type": "Commercial & Residential Contractors",
        "format": "Five-digit numeric ID",
        "url": "http://search.msboc.us/ConsolidatedResults.cfm?ContractorType=&VarDatasource=BOC&Advanced=1",
        "method": "requests"
    },
    "MO": {
        "example": "KC: 20231234, STL: 23BUS001",
        "type": "Local Contractor Licensing",
        "format": "Varies by city",
        "url": "https://pr.mo.gov/licensee-search.asp",
        "method": "playwright",
        "notes": "No statewide license; city/county level"
    },
    "MT": {
        "regex": r"\d{5}",
        "example": "12345",
        "type": "Construction Contractor",
        "format": "Five-digit numeric ID",
        "url": "https://erdcontractors.mt.gov/ICCROnlineSearch/registrationlookup.jsp",
        "method": "playwright"
    },
    "NE": {
        "regex": r"\d{5}",
        "example": "12345",
        "type": "Contractor Registration",
        "format": "Five-digit numeric ID",
        "url": "https://dol.nebraska.gov/conreg/Search",
        "method": "playwright"
    },
    "NV": {
        "regex": r"\d{6}",
        "example": "123456",
        "type": "State Contractors Board",
        "format": "Six-digit numeric ID",
        "url": "https://app.nvcontractorsboard.com/Clients/NVSCB/Public/ContractorLicenseSearch/ContractorLicenseSearch.aspx",
        "method": "playwright"
    },
    "NH": {
        "regex": r"\d{6}",
        "example": "123456",
        "type": "Licensed trades",
        "format": "Six-digit numeric ID",
        "url": "https://oplc.nh.gov/license-lookup",
        "method": "playwright"
    },
    "NJ": {
        "regex": r"\d{7}",
        "example": "1234567",
        "type": "Home Improvement Contractor",
        "format": "Seven-digit numeric ID",
        "url": "https://newjersey.mylicense.com/verification/Search.aspx?facility=Y",
        "method": "playwright"
    },
    "NM": {
        "regex": r"\d{6}",
        "example": "123456",
        "type": "Construction Industries Division",
        "format": "Six-digit numeric ID",
        "url": "https://public.psiexams.com/search.jsp",
        "method": "playwright"
    },
    "NY": {
        "example": "NYC HIC: 123456",
        "type": "Municipal Licensing",
        "format": "6-digit varies by municipality",
        "url": "https://appext20.dos.ny.gov/lcns_public/licenseesearch/lcns_public_index.cfm",
        "method": "playwright",
        "notes": "No statewide GC license; local level"
    },
    "NC": {
        "regex": r"\d{5}",
        "example": "12345",
        "type": "General Contractor",
        "format": "Five-digit numeric ID",
        "url": "https://portal.nclbgc.org/Public/Search",
        "method": "playwright"
    },
    "ND": {
        "regex": r"\d{5}",
        "example": "12345",
        "type": "State Contractor License",
        "format": "Five-digit numeric ID",
        "url": "https://firststop.sos.nd.gov/search/contractor",
        "method": "playwright"
    },
    "OH": {
        "regex": r"[A-Z]{2}\d{6}",
        "example": "HV123456",
        "type": "Commercial Trades",
        "format": "Prefix + six digits",
        "url": "https://elicense3.com.ohio.gov/",
        "method": "playwright"
    },
    "OK": {
        "regex": r"\d{6}",
        "example": "123456",
        "type": "Construction Industries Board",
        "format": "Six-digit numeric ID",
        "url": "https://okcibv7prod.glsuite.us/GLSuiteWeb/Clients/OKCIB/Public/LicenseeSearch/LicenseeSearch.aspx",
        "method": "playwright"
    },
    "OR": {
        "regex": r"\d{6}",
        "example": "123456",
        "type": "Construction Contractors Board",
        "format": "Six-digit numeric ID",
        "url": "https://search.ccb.state.or.us/search/",
        "method": "playwright"
    },
    "PA": {
        "regex": r"PA\d{6}",
        "example": "PA123456",
        "type": "Home Improvement Contractor",
        "format": "Prefix 'PA' + six digits",
        "url": "https://hicsearch.attorneygeneral.gov/",
        "method": "playwright"
    },
    "RI": {
        "example": "Reg ID: 12345",
        "type": "Contractor Registration",
        "format": "5-digit numeric registration ID",
        "url": "https://crb.ri.gov/consumer/search-registrantlicensee",
        "method": "playwright",
        "notes": "General contractors registered, not licensed"
    },
    "SC": {
        "regex": r"CLG\d{6}",
        "example": "CLG123456",
        "type": "Contractor's Licensing Board",
        "format": "Prefix + six digits",
        "url": "https://verify.llronline.com/LicLookup/Contractors/Contractor.aspx?div=69&AspxAutoDetectCookieSupport=1",
        "method": "playwright"
    },
    "SD": {
        "regex": r"\d{5}",
        "example": "12345",
        "type": "Electrical, Plumbing, etc.",
        "format": "Five-digit numeric ID",
        "url": "https://sdec.portalus.thentiacloud.net/webs/portal/register/#/",
        "method": "playwright"
    },
    "TN": {
        "regex": r"\d{6}",
        "example": "123456",
        "type": "Commercial & Residential",
        "format": "Six-digit numeric ID",
        "url": "https://www.tn.gov/commerce/regboards/contractor.html",
        "method": "playwright"
    },
    "TX": {
        "regex": r"\d{5,6}",
        "example": "12345",
        "type": "Electrical, HVAC, Plumbing",
        "format": "Five or six-digit numeric ID",
        "url": "https://www.tdlr.texas.gov/LicenseSearch/",
        "method": "playwright"
    },
    "UT": {
        "regex": r"\d{6}-\d{4}",
        "example": "123456-5501",
        "type": "Contractor",
        "format": "Six digits + dash + four-digit suffix",
        "url": "https://secure.utah.gov/llv/search/index.html",
        "method": "playwright"
    },
    "VT": {
        "example": "Reg ID: 456789",
        "type": "Contractor Registration",
        "format": "Numeric registration ID (varies)",
        "url": "https://sos.vermont.gov/opr/find-a-professional/",
        "method": "playwright",
        "notes": "No state GC license; municipal requirements"
    },
    "VA": {
        "regex": r"2705\d{6}",
        "example": "2710000000",
        "type": "Class A/B/C Contractors",
        "format": "10-digit starting with 2705",
        "url": "https://www.dpor.virginia.gov/LicenseLookup/",
        "method": "playwright"
    },
    "WA": {
        "regex": r"[A-Z]{3}\d{4}",
        "example": "ABC1234",
        "type": "Contractor Registration",
        "format": "Three letters + four digits",
        "url": "https://secure.lni.wa.gov/verify/",
        "method": "playwright"
    },
    "WV": {
        "regex": r"WV\d{6}",
        "example": "WV012345",
        "type": "Contractors Licensing Board",
        "format": "WV + six digits",
        "url": "https://wvclboard.wv.gov/verify/",
        "method": "playwright"
    },
    "WI": {
        "regex": r"\d{6}",
        "example": "123456",
        "type": "Dwelling Contractor",
        "format": "Six-digit numeric ID",
        "url": "https://dsps.wi.gov/Pages/Professions/Default.aspx",
        "method": "playwright"
    },
    "WY": {
        "regex": r"\d{5}",
        "example": "12345",
        "type": "Local Licensing Only",
        "format": "Five-digit numeric ID (varies)",
        "url": "https://doe.state.wy.us/lmi/licensed_occupations.htm",
        "method": "playwright",
        "notes": "Local licensing only"
    }
}

def validate_license_format(state: str, license_number: str) -> Dict[str, Any]:
    """Validate license number format against state requirements"""
    state = state.upper()
    
    if state not in STATE_CONFIGS:
        return {
            "valid": False,
            "error": f"License format validation not available for {state}",
            "supported_states": list(STATE_CONFIGS.keys())
        }
    
    config = STATE_CONFIGS[state]
    regex_pattern = config.get("regex")
    
    if not regex_pattern:
        return {
            "valid": True,
            "warning": f"No standard format for {state} - {config.get('notes', 'varies by municipality')}",
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

async def scrape_with_requests_fallback(state: str, config: Dict, license_number: str, business_name: Optional[str] = None) -> Dict[str, Any]:
    """Fallback HTTP requests method when Playwright is not available"""
    async with aiohttp.ClientSession() as session:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            # For Playwright-only sites, try to get the page content first
            if config["method"] == "playwright":
                # Just fetch the page and try to extract any useful info
                async with session.get(config["url"], headers=headers) as response:
                    html_content = await response.text()
                
                # Basic text analysis for license information
                content_lower = html_content.lower()
                
                # Look for the license number in the page content
                if license_number.lower() in content_lower:
                    status = "Found"
                    message = f"License number {license_number} found on {state} verification page"
                else:
                    status = "Not Found"
                    message = f"License number {license_number} not found on {state} verification page"
                
                return {
                    "status": status,
                    "license_number": license_number,
                    "business_name": business_name or "Unknown",
                    "issuing_authority": f"{state} {config.get('type', 'Licensing Board')}",
                    "expires": "Unknown",
                    "verified": False,
                    "verification_url": config["url"],
                    "method_used": "requests_fallback",
                    "message": message,
                    "note": "Playwright not available - using basic page analysis"
                }
            
            # For requests-compatible sites, try form submission
            else:
                # Prepare form data based on state
                if state == "FL":
                    form_data = {
                        'licnbr': license_number,
                        'Submit': 'Search'
                    }
                elif state == "AR":
                    form_data = {
                        'license_number': license_number,
                        'search': 'Search'
                    }
                elif state == "MD":
                    form_data = {
                        'license_number': license_number,
                        'action': 'search'
                    }
                else:
                    # Generic form data
                    form_data = {
                        'license_number': license_number,
                        'search': 'Search'
                    }
                
                # Submit search request
                async with session.post(config["url"], data=form_data, headers=headers) as response:
                    html_content = await response.text()
                
                # Parse results
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Determine status
                status = "Unknown"
                business_name_result = business_name or "Unknown"
                expires = "Unknown"
                
                content_lower = html_content.lower()
                if any(word in content_lower for word in ["active", "valid", "current", "good standing"]):
                    status = "Active"
                elif any(word in content_lower for word in ["expired", "inactive", "lapsed"]):
                    status = "Expired"
                elif any(word in content_lower for word in ["invalid", "not found", "no results", "no license"]):
                    status = "Invalid"
                elif any(word in content_lower for word in ["suspended", "revoked", "cancelled"]):
                    status = "Suspended"
                
                return {
                    "status": status,
                    "license_number": license_number,
                    "business_name": business_name_result,
                    "issuing_authority": f"{state} {config.get('type', 'Licensing Board')}",
                    "expires": expires,
                    "verified": True,
                    "verification_url": config["url"],
                    "method_used": "requests",
                    "format_valid": validate_license_format(state, license_number)["valid"]
                }
            
        except Exception as e:
            raise Exception(f"Error verifying {state} license with fallback method: {str(e)}")

async def scrape_with_playwright(state: str, config: Dict, license_number: str, business_name: Optional[str] = None) -> Dict[str, Any]:
    """Playwright scraping function"""
    if not PLAYWRIGHT_AVAILABLE:
        return await scrape_with_requests_fallback(state, config, license_number, business_name)
    
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-dev-shm-usage']
            )
            
            # Use different user agents and headers based on state requirements
            user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            extra_headers = {}
            
            if state == "CA":
                extra_headers['Referer'] = 'https://www.cslb.ca.gov/'
            
            context = await browser.new_context(
                user_agent=user_agent,
                extra_http_headers=extra_headers
            )
            page = await context.new_page()
            
            await page.goto(config["url"], wait_until="networkidle", timeout=30000)
            
            # State-specific scraping logic
            if state == "CA":
                await page.fill("#ctl00_ContentPlaceHolder1_txtLicnum", license_number)
                await page.click("#ctl00_ContentPlaceHolder1_btnSearch")
            elif state == "TX":
                await page.fill("#LicenseNumber", license_number)
                await page.click("#SearchButton")
            elif state == "OR":
                await page.fill("input[name='license_number']", license_number)
                await page.click("input[type='submit']")
            elif state == "WA":
                await page.fill("input[name='licenseNumber']", license_number)
                await page.click("input[value='Search']")
            else:
                # Generic approach - look for common input patterns
                license_inputs = await page.query_selector_all("input[type='text'], input[name*='license'], input[id*='license']")
                if license_inputs:
                    await license_inputs[0].fill(license_number)
                
                search_buttons = await page.query_selector_all("input[type='submit'], button[type='submit'], input[value*='Search'], button:has-text('Search')")
                if search_buttons:
                    await search_buttons[0].click()
            
            # Wait for results
            await page.wait_for_load_state("networkidle", timeout=15000)
            
            # Take screenshot for evidence
            screenshot_bytes = await page.screenshot(full_page=True)
            
            # Extract results
            content = await page.content()
            
            # Determine license status
            status = "Unknown"
            business_name_result = business_name or "Unknown"
            expires = "Unknown"
            
            content_lower = content.lower()
            if any(word in content_lower for word in ["active", "valid", "current", "good standing"]):
                status = "Active"
            elif any(word in content_lower for word in ["expired", "inactive", "lapsed"]):
                status = "Expired"
            elif any(word in content_lower for word in ["invalid", "not found", "no results", "no license"]):
                status = "Invalid"
            elif any(word in content_lower for word in ["suspended", "revoked", "cancelled"]):
                status = "Suspended"
            
            # Try to extract business name if not provided
            if business_name_result == "Unknown":
                name_patterns = [
                    r"business name[:\s]+([^<\n\r]+)",
                    r"company name[:\s]+([^<\n\r]+)",
                    r"contractor name[:\s]+([^<\n\r]+)"
                ]
                for pattern in name_patterns:
                    match = re.search(pattern, content_lower)
                    if match:
                        business_name_result = match.group(1).strip()
                        break
            
            # Try to extract expiration date
            date_patterns = [
                r"expir[a-z]*[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
                r"expires?[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
                r"valid through[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})"
            ]
            for pattern in date_patterns:
                match = re.search(pattern, content_lower)
                if match:
                    expires = match.group(1)
                    break
            
            await browser.close()
            
            return {
                "status": status,
                "license_number": license_number,
                "business_name": business_name_result,
                "issuing_authority": f"{state} {config.get('type', 'Licensing Board')}",
                "expires": expires,
                "screenshot_data": base64.b64encode(screenshot_bytes).decode('utf-8'),
                "verified": True,
                "verification_url": config["url"],
                "method_used": "playwright",
                "format_valid": validate_license_format(state, license_number)["valid"]
            }
            
        except Exception as e:
            await browser.close()
            # Fallback to requests method if Playwright fails
            print(f"Playwright failed for {state}, trying requests fallback: {str(e)}")
            return await scrape_with_requests_fallback(state, config, license_number, business_name)

async def scrape_with_requests(state: str, config: Dict, license_number: str, business_name: Optional[str] = None) -> Dict[str, Any]:
    """HTTP requests scraping for simpler sites"""
    async with aiohttp.ClientSession() as session:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            # Prepare form data based on state
            if state == "FL":
                form_data = {
                    'licnbr': license_number,
                    'Submit': 'Search'
                }
                # For Florida, try GET request first
                search_url = f"{config['url']}?licnbr={license_number}"
                async with session.get(search_url, headers=headers) as response:
                    html_content = await response.text()
            elif state == "AR":
                form_data = {
                    'license_number': license_number,
                    'search': 'Search'
                }
                async with session.post(config["url"], data=form_data, headers=headers) as response:
                    html_content = await response.text()
            elif state == "MD":
                form_data = {
                    'license_number': license_number,
                    'action': 'search'
                }
                async with session.post(config["url"], data=form_data, headers=headers) as response:
                    html_content = await response.text()
            else:
                # Generic GET request with license number as parameter
                search_url = f"{config['url']}?license={license_number}&search=1"
                async with session.get(search_url, headers=headers) as response:
                    html_content = await response.text()
            
            # Parse results
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Determine status
            status = "Unknown"
            business_name_result = business_name or "Unknown"
            expires = "Unknown"
            
            content_lower = html_content.lower()
            if any(word in content_lower for word in ["active", "valid", "current", "good standing"]):
                status = "Active"
            elif any(word in content_lower for word in ["expired", "inactive", "lapsed"]):
                status = "Expired"
            elif any(word in content_lower for word in ["invalid", "not found", "no results", "no license"]):
                status = "Invalid"
            elif any(word in content_lower for word in ["suspended", "revoked", "cancelled"]):
                status = "Suspended"
            
            # Extract business name if found
            name_patterns = [
                r"business name[:\s]+([^<\n\r]+)",
                r"company name[:\s]+([^<\n\r]+)",
                r"contractor name[:\s]+([^<\n\r]+)"
            ]
            for pattern in name_patterns:
                match = re.search(pattern, content_lower)
                if match:
                    business_name_result = match.group(1).strip()
                    break
            
            return {
                "status": status,
                "license_number": license_number,
                "business_name": business_name_result,
                "issuing_authority": f"{state} {config.get('type', 'Licensing Board')}",
                "expires": expires,
                "verified": True,
                "verification_url": config["url"],
                "method_used": "requests",
                "format_valid": validate_license_format(state, license_number)["valid"],
                "note": "Limited data extraction due to website complexity"
            }
            
        except Exception as e:
            raise Exception(f"Error verifying {state} license with requests: {str(e)}")

async def verify_license(state: str, license_number: Optional[str] = None, business_name: Optional[str] = None) -> Dict[str, Any]:
    """Main license verification function"""
    
    # Input validation
    if not state:
        raise Exception("State is required")
    
    if not license_number and not business_name:
        raise Exception("Either license number or business name is required")
    
    state = state.upper()
    
    # Check cache first
    cache_key = f"{state}_{license_number}_{business_name}"
    cached = get_cached_result(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached
    
    # Check if state is supported
    if state not in STATE_CONFIGS:
        return {
            "status": "Unsupported",
            "message": f"License verification not yet supported for {state}",
            "supported_states": list(STATE_CONFIGS.keys()),
            "verified": False
        }
    
    config = STATE_CONFIGS[state]
    
    # Validate license format if provided
    if license_number:
        license_number = normalize_license_number(state, license_number)
        format_validation = validate_license_format(state, license_number)
        if not format_validation["valid"] and "warning" not in format_validation:
            return {
                "status": "Invalid Format",
                "message": f"License number '{license_number}' does not match expected format for {state}",
                "expected_format": format_validation["format_info"]["format"],
                "example": format_validation["format_info"]["example"],
                "verified": False
            }
    
    try:
        # Choose scraping method - fallback to requests if Playwright not available
        if config["method"] == "playwright" and PLAYWRIGHT_AVAILABLE:
            result = await scrape_with_playwright(state, config, license_number, business_name)
        else:
            result = await scrape_with_requests(state, config, license_number, business_name)
        
        # Add state-specific information
        result["state"] = state
        result["license_type"] = config.get("type", "Unknown")
        result["notes"] = config.get("notes")
        result["playwright_available"] = PLAYWRIGHT_AVAILABLE
        
        # Cache the result
        store_result(cache_key, result)
        
        return result
        
    except Exception as e:
        error_result = {
            "status": "Error",
            "message": str(e),
            "license_number": license_number,
            "state": state,
            "verified": False,
            "verification_url": config["url"],
            "playwright_available": PLAYWRIGHT_AVAILABLE
        }
        
        return error_result

async def verify_batch(requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Verify multiple licenses in batch with rate limiting"""
    
    # Group requests by state to optimize scraping
    state_groups = {}
    for i, request in enumerate(requests):
        state = request.get("state", "").upper()
        if state not in state_groups:
            state_groups[state] = []
        state_groups[state].append((i, request))
    
    results = [None] * len(requests)
    
    # Process each state group with appropriate delays
    for state, state_requests in state_groups.items():
        for i, (original_index, request) in enumerate(state_requests):
            try:
                # Add delay between requests to avoid overwhelming servers
                if i > 0:
                    await asyncio.sleep(3)  # 3 second delay between requests to same state
                
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
        
        # Add longer delay between different states
        await asyncio.sleep(2)
    
    return results

def get_supported_states() -> Dict[str, Dict[str, Any]]:
    """Get list of supported states with their configurations"""
    return {
        state: {
            "type": config.get("type", "Unknown"),
            "format": config.get("format", "Unknown"),
            "example": config.get("example", "Unknown"),
            "notes": config.get("notes"),
            "method": config.get("method"),
            "playwright_required": config.get("method") == "playwright"
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
    elif state == "WV" and not license_number.startswith("WV"):
        # Add WV prefix if missing
        if license_number.isdigit():
            license_number = f"WV{license_number}"
    elif state == "CT" and not license_number.startswith("HIC."):
        # Add HIC. prefix if missing
        if license_number.isdigit():
            license_number = f"HIC.{license_number}"
    elif state == "SC" and not license_number.startswith("CLG"):
        # Add CLG prefix if missing
        if license_number.isdigit():
            license_number = f"CLG{license_number}"
    
    return license_number

def get_state_info(state: str) -> Dict[str, Any]:
    """Get detailed information about a state's licensing system"""
    state = state.upper()
    if state not in STATE_CONFIGS:
        return {"error": f"State {state} not supported"}
    
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
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "playwright_required": config.get("method") == "playwright"
    }

def get_system_status() -> Dict[str, Any]:
    """Get system status and capabilities"""
    return {
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "supported_states": len(STATE_CONFIGS),
        "playwright_states": len([s for s, c in STATE_CONFIGS.items() if c.get("method") == "playwright"]),
        "requests_states": len([s for s, c in STATE_CONFIGS.items() if c.get("method") == "requests"]),
        "status": "operational" if PLAYWRIGHT_AVAILABLE else "limited_functionality"
    }
