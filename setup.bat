@echo off
REM flisearch setup script — Windows
echo 🔧 Setting up flisearch...

where python >nul 2>nul
if errorlevel 1 (
    echo ❌ Python not found. Install it from https://python.org
    exit /b 1
)

if not exist "venv\" (
    echo 📦 Creating virtual environment...
    python -m venv venv
) else (
    echo ✅ Virtual environment already exists.
)

echo 📥 Installing dependencies...
call venv\Scripts\activate.bat
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo.
echo ✅ Setup complete! Run the tool with:
echo.
echo    venv\Scripts\activate
echo    python flisearch.py --help
echo.
