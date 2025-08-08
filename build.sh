#!/bin/bash
# Fixed build script for Render deployment

set -e  # Exit on any error

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Installing Playwright..."
python -m playwright install --with-deps chromium

echo "Verifying Playwright installation..."
python -c "
try:
    from playwright.sync_api import sync_playwright
    print('✓ Playwright installed successfully')
except Exception as e:
    print(f'✗ Playwright verification failed: {e}')
    exit(1)
"

echo "✓ Build complete!"
