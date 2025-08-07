#!/usr/bin/env python3
"""
Test script for the contractor license verification system
Run this to test your API endpoints and scraping functionality
"""

import asyncio
import aiohttp
import json
from scraper import verify_license, validate_license_format, get_supported_states

async def test_single_verification():
    """Test single license verification"""
    print("=== Testing Single License Verification ===")
    
    # Test with known license formats
    test_cases = [
        {"state": "CA", "license_number": "927123"},
        {"state": "FL", "license_number": "CGC1524312"},
        {"state": "TX", "license_number": "12345"},
        {"state": "OR", "license_number": "123456"},
    ]
    
    for test_case in test_cases:
        print(f"\nTesting {test_case['state']} license {test_case['license_number']}")
        try:
            result = await verify_license(
                test_case["state"], 
                test_case["license_number"]
            )
            print(f"Status: {result['status']}")
            print(f"Business Name: {result.get('business_name', 'N/A')}")
            print(f"Expires: {result.get('expires', 'N/A')}")
            print(f"Verified: {result.get('verified', False)}")
        except Exception as e:
            print(f"Error: {e}")

def test_format_validation():
    """Test license format validation"""
    print("\n=== Testing Format Validation ===")
    
    test_cases = [
        {"state": "CA", "license_number": "927123", "should_be_valid": True},
        {"state": "CA", "license_number": "ABC123", "should_be_valid": False},
        {"state": "FL", "license_number": "CGC1524312", "should_be_valid": True},
        {"state": "FL", "license_number": "123456", "should_be_valid": False},
        {"state": "PA", "license_number": "PA123456", "should_be_valid": True},
        {"state": "UT", "license_number": "123456-5501", "should_be_valid": True},
    ]
    
    for test_case in test_cases:
        result = validate_license_format(
            test_case["state"], 
            test_case["license_number"]
        )
        status = "✓" if result["valid"] == test_case["should_be_valid"] else "✗"
        print(f"{status} {test_case['state']}: {test_case['license_number']} -> {result['valid']}")
        if not result["valid"]:
            print(f"   Expected: {result['format_info']['format']}")

async def test_api_endpoints():
    """Test API endpoints if server is running"""
    print("\n=== Testing API Endpoints ===")
    
    base_url = "http://localhost:10000"
    
    async with aiohttp.ClientSession() as session:
        # Test root endpoint
        try:
            async with session.get(f"{base_url}/") as response:
                data = await response.json()
                print(f"✓ Root endpoint: {data['message']}")
        except Exception as e:
            print(f"✗ Root endpoint failed: {e}")
            print("Make sure the server is running with: uvicorn main:app --host 0.0.0.0 --port 10000")
            return
        
        # Test states endpoint
        try:
            async with session.get(f"{base_url}/states") as response:
                data = await response.json()
                print(f"✓ States endpoint: {len(data)} states supported")
        except Exception as e:
            print(f"✗ States endpoint failed: {e}")
        
        # Test single verification
        try:
            test_data = {
                "state": "CA",
                "license_number": "927123"
            }
            async with session.post(f"{base_url}/verify", json=test_data) as response:
                data = await response.json()
                print(f"✓ Verify endpoint: Status = {data.get('status', 'Unknown')}")
        except Exception as e:
            print(f"✗ Verify endpoint failed: {e}")

def print_supported_states():
    """Print all supported states and their formats"""
    print("\n=== Supported States ===")
    states = get_supported_states()
    
    for state, info in sorted(states.items()):
        print(f"{state}: {info['type']}")
        print(f"   Format: {info['format']}")
        print(f"   Example: {info['example']}")
        if info.get('notes'):
            print(f"   Notes: {info['notes']}")
        print()

async def main():
    """Run all tests"""
    print("Contractor License Verification System Test")
    print("=" * 50)
    
    # Test format validation (sync)
    test_format_validation()
    
    # Print supported states
    print_supported_states()
    
    # Test single verification (async)
    await test_single_verification()
    
    # Test API endpoints (async)
    await test_api_endpoints()
    
    print("\n=== Test Complete ===")
    print("If you see errors above, check that:")
    print("1. All required packages are installed")
    print("2. Playwright browsers are installed: python -m playwright install")
    print("3. Your internet connection is working")
    print("4. State websites are accessible")

if __name__ == "__main__":
    asyncio.run(main())
