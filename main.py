from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator
from typing import Optional, List, Dict, Any
from scraper import (
    verify_license, 
    verify_batch, 
    validate_license_format, 
    get_supported_states, 
    get_state_info,
    normalize_license_number,
    STATE_CONFIGS
)
import re

app = FastAPI(
    title="Contractor License Verification API", 
    version="4.0",
    description="Real-time contractor license verification across all US states"
)

# Add CORS middleware for web applications
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
    
    @validator('license_number')
    def validate_license_number(cls, v, values):
        if v:
            # Remove extra whitespace
            v = v.strip()
            if 'state' in values:
                # Normalize format for the state
                v = normalize_license_number(values['state'], v)
        return v

class BatchRequest(BaseModel):
    requests: List[LicenseRequest]
    
    @validator('requests')
    def validate_batch_size(cls, v):
        if len(v) > 50:  # Limit batch size to prevent overload
            raise ValueError('Batch size cannot exceed 50 requests')
        return v

class FormatValidationRequest(BaseModel):
    state: str
    license_number: str

@app.get("/")
async def root():
    return {
        "message": "Contractor License Verification API v4.0",
        "status": "active",
        "supported_states": len(STATE_CONFIGS),
        "features": [
            "Real-time license verification",
            "Format validation",
            "Batch processing",
            "Screenshot evidence",
            "All 50 US states support"
        ]
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": "2025-08-07"}

@app.post("/verify")
async def verify(request: LicenseRequest):
    """Verify a single contractor license"""
    try:
        result = await verify_license(
            request.state, 
            request.license_number, 
            request.business_name
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/verify_batch")
async def verify_multiple(batch: BatchRequest):
    """Verify multiple contractor licenses in batch"""
    try:
        queries = [r.dict() for r in batch.requests]
        results = await verify_batch(queries)
        
        # Add summary statistics
        summary = {
            "total_requests": len(results),
            "successful": len([r for r in results if r.get("verified", False)]),
            "errors": len([r for r in results if r.get("status") == "Error"]),
            "active_licenses": len([r for r in results if r.get("status") == "Active"]),
            "expired_licenses": len([r for r in results if r.get("status") == "Expired"]),
            "invalid_licenses": len([r for r in results if r.get("status") == "Invalid"])
        }
        
        return {
            "results": results,
            "summary": summary
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/validate_format")
async def validate_format(request: FormatValidationRequest):
    """Validate license number format without performing verification"""
    try:
        result = validate_license_format(request.state, request.license_number)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/states")
async def get_states():
    """Get list of supported states with their license requirements"""
    return get_supported_states()

@app.get("/states/{state}")
async def get_state_details(state: str):
    """Get detailed information about a specific state's licensing system"""
    try:
        state_info = get_state_info(state)
        if "error" in state_info:
            raise HTTPException(status_code=404, detail=state_info["error"])
        return state_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/search")
async def search_license(
    state: str = Query(..., description="2-letter state code"),
    license_number: Optional[str] = Query(None, description="License number to verify"),
    business_name: Optional[str] = Query(None, description="Business name to search"),
    format_only: bool = Query(False, description="Only validate format, don't verify")
):
    """Search/verify license via GET request (for easy testing)"""
    
    if format_only and license_number:
        return validate_license_format(state, license_number)
    
    try:
        result = await verify_license(state, license_number, business_name)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/examples")
async def get_examples():
    """Get example license numbers for each state"""
    examples = {}
    for state, config in STATE_CONFIGS.items():
        examples[state] = {
            "example": config.get("example", "Unknown"),
            "format": config.get("format", "Unknown"),
            "type": config.get("type", "Unknown")
        }
    return examples

@app.get("/stats")
async def get_stats():
    """Get API usage statistics"""
    from cache import get_cache_stats
    cache_stats = get_cache_stats()
    return {
        "supported_states": len(STATE_CONFIGS),
        "cached_results": cache_stats["cached_items"],
        "cache_size_mb": cache_stats["cache_size_mb"],
        "most_verified_states": ["CA", "FL", "TX", "NY", "PA"]
    }

# Error handlers
@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    return HTTPException(status_code=400, detail=str(exc))

@app.exception_handler(404)
async def not_found_handler(request, exc):
    return {"error": "Resource not found", "detail": str(exc)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
