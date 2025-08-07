import os
import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator
from typing import Optional, List, Dict, Any

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import our verification functions
try:
    from scraper import (
        verify_license, 
        verify_batch, 
        validate_license_format, 
        get_supported_states, 
        get_state_info,
        normalize_license_number,
        STATE_CONFIGS,
        get_system_status
    )
    logger.info("Successfully imported scraper functions")
except ImportError as e:
    logger.error(f"Failed to import scraper: {e}")
    # Create minimal fallback
    STATE_CONFIGS = {}
    
    async def verify_license(*args, **kwargs):
        return {"status": "Error", "message": "Scraper module not available"}
    
    async def verify_batch(*args, **kwargs):
        return [{"status": "Error", "message": "Scraper module not available"}]

app = FastAPI(
    title="Contractor License Verification API", 
    version="4.0-simplified",
    description="Real-time contractor license verification (Render-optimized version)"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class LicenseRequest(BaseModel):
    state: str
    license_number: Optional[str] = None
    business_name: Optional[str] = None
    
    @validator('state')
    def validate_state(cls, v):
        if not v or len(v) != 2:
            raise ValueError('State must be a 2-letter code (e.g., CA, FL, TX)')
        return v.upper()

class BatchRequest(BaseModel):
    requests: List[LicenseRequest]
    
    @validator('requests')
    def validate_batch_size(cls, v):
        if len(v) > 20:  # Reduced batch size for free tier
            raise ValueError('Batch size cannot exceed 20 requests on free tier')
        return v

@app.get("/")
async def root():
    """API root endpoint with system information"""
    try:
        system_status = get_system_status()
        return {
            "message": "Contractor License Verification API v4.0",
            "status": "active",
            "supported_states": len(STATE_CONFIGS),
            "system_info": system_status,
            "endpoints": {
                "verify": "POST /verify - Verify single license",
                "verify_batch": "POST /verify_batch - Verify multiple licenses", 
                "validate_format": "POST /validate_format - Validate license format",
                "states": "GET /states - List supported states",
                "search": "GET /search - Quick license search",
                "examples": "GET /examples - Get example license numbers"
            }
        }
    except Exception as e:
        logger.error(f"Error in root endpoint: {e}")
        return {
            "message": "Contractor License Verification API v4.0",
            "status": "limited",
            "error": "Some features may be unavailable"
        }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy", 
        "timestamp": "2025-08-07",
        "environment": "render" if os.environ.get("RENDER") else "local"
    }

@app.post("/verify")
async def verify(request: LicenseRequest):
    """Verify a single contractor license"""
    try:
        logger.info(f"Verifying license: {request.state} - {request.license_number}")
        
        result = await verify_license(
            request.state, 
            request.license_number, 
            request.business_name
        )
        
        logger.info(f"Verification result: {result.get('status', 'Unknown')}")
        return result
        
    except Exception as e:
        logger.error(f"Verification error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/verify_batch")
async def verify_multiple(batch: BatchRequest):
    """Verify multiple contractor licenses in batch"""
    try:
        logger.info(f"Batch verification: {len(batch.requests)} requests")
        
        queries = [r.dict() for r in batch.requests]
        results = await verify_batch(queries)
        
        # Add summary statistics
        summary = {
            "total_requests": len(results),
            "successful": len([r for r in results if r.get("verified", False)]),
            "errors": len([r for r in results if r.get("status") == "Error"])
        }
        
        logger.info(f"Batch results: {summary}")
        
        return {
            "results": results,
            "summary": summary
        }
    except Exception as e:
        logger.error(f"Batch verification error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/validate_format")
async def validate_format(request: LicenseRequest):
    """Validate license number format without performing verification"""
    try:
        result = validate_license_format(request.state, request.license_number)
        return result
    except Exception as e:
        logger.error(f"Format validation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/states")
async def get_states():
    """Get list of supported states with their license requirements"""
    try:
        return get_supported_states()
    except Exception as e:
        logger.error(f"Error getting states: {e}")
        return {"error": "Unable to retrieve state information"}

@app.get("/states/{state}")
async def get_state_details(state: str):
    """Get detailed information about a specific state's licensing system"""
    try:
        state_info = get_state_info(state)
        if "error" in state_info:
            raise HTTPException(status_code=404, detail=state_info["error"])
        return state_info
    except Exception as e:
        logger.error(f"Error getting state details: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/search")
async def search_license(
    state: str = Query(..., description="2-letter state code"),
    license_number: Optional[str] = Query(None, description="License number to verify"),
    business_name: Optional[str] = Query(None, description="Business name to search"),
    format_only: bool = Query(False, description="Only validate format, don't verify")
):
    """Search/verify license via GET request (for easy testing)"""
    
    try:
        if format_only and license_number:
            return validate_license_format(state, license_number)
        
        result = await verify_license(state, license_number, business_name)
        return result
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/examples")
async def get_examples():
    """Get example license numbers for each state"""
    try:
        examples = {}
        for state, config in STATE_CONFIGS.items():
            examples[state] = {
                "example": config.get("example", "Contact state for format"),
                "format": config.get("format", "Varies"),
                "type": config.get("type", "Professional License")
            }
        return examples
    except Exception as e:
        logger.error(f"Error getting examples: {e}")
        return {"error": "Unable to retrieve examples"}

@app.get("/debug")
async def debug_info():
    """Debug endpoint to check system status"""
    try:
        system_status = get_system_status()
        return {
            "system_status": system_status,
            "environment_vars": {
                "PORT": os.environ.get("PORT", "Not set"),
                "PYTHON_VERSION": os.environ.get("PYTHON_VERSION", "Not set"),
                "RENDER": os.environ.get("RENDER", "Not set"),
                "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "Not set")
            },
            "supported_states_count": len(STATE_CONFIGS)
        }
    except Exception as e:
        return {"error": f"Debug info error: {e}"}

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Global exception: {exc}")
    return HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1)
