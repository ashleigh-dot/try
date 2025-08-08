#!/bin/bash
# CLEAN build.sh - no complex Python commands

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Build complete - using requests fallback for web scraping"
