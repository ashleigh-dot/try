#!/bin/bash
# Build script for Render deployment

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Installing Playwright..."
playwright install chromium

echo "Installing Playwright dependencies..."
playwright install-deps

echo "Verifying Playwright installation..."
python -c "from playwright.sync_api import sync_playwright; print('Playwright installed successfully')"

echo "Build complete!"
