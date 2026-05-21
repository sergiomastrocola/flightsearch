#!/bin/bash
# flisearch setup script — macOS / Linux
set -e

echo "🔧 Setting up flisearch..."

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found. Install it from https://python.org"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PYTHON_VERSION" -lt 10 ]; then
    echo "❌ Python 3.10+ required. Current: $(python3 --version)"
    exit 1
fi

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
else
    echo "✅ Virtual environment already exists."
fi

# Activate and install
echo "📥 Installing dependencies..."
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo ""
echo "✅ Setup complete! Run the tool with:"
echo ""
echo "   source venv/bin/activate"
echo "   python flisearch.py --help"
echo ""
