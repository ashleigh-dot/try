#!/bin/bash
# Simple, reliable build script for Render

echo "==> Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Installing Playwright browsers..."
python -m playwright install chromium

echo "==> Installing system dependencies for Playwright..."
python -m playwright install-deps

echo "==> Build completed successfully!"
