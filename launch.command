#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo " =========================================="
echo "  📊 Starting Stock Analysis Dashboard..."
echo " =========================================="
echo ""
echo " Opening http://localhost:5000"
echo " Press Ctrl+C to stop."
echo ""
python3 app.py
