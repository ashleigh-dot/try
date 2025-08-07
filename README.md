# Contractor License Verification API

Real-time contractor license verification across all 50 US states.

## Features

- ✅ **All 50 States Supported** - Complete coverage with state-specific license formats
- ✅ **Real-time Verification** - Live scraping of official state databases
- ✅ **Format Validation** - Validates license numbers against state-specific patterns
- ✅ **Batch Processing** - Verify multiple licenses in a single request
- ✅ **Screenshot Evidence** - Captures verification screenshots for proof
- ✅ **Smart Caching** - 24-hour cache to avoid duplicate requests
- ✅ **Rate Limiting** - Built-in delays to respect state servers

## API Endpoints

### GET `/`
Returns API information and status

### POST `/verify`
Verify a single contractor license
```json
{
  "state": "CA",
  "license_number": "927123"
}
