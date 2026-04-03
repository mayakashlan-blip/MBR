#!/usr/bin/env bash
set -e

pip install -r requirements.txt

# Install Playwright Chromium browser (without --with-deps since Render can't sudo)
playwright install chromium

# Render's base image has most deps, but we need to find and set the browser path
echo "Playwright browsers installed at: $(python -c 'import playwright; print(playwright.__file__)')"
