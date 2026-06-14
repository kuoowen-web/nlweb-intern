#!/bin/bash
# NLWeb Development Environment Setup
# Works on: macOS, Windows (Git Bash)
# Usage: bash scripts/setup.sh

set -e

echo "=== NLWeb Development Setup ==="
echo ""

# --- 1. Check Python version ---
PYTHON_CMD=""
for cmd in python3.11 python3 python; do
    if command -v "$cmd" &> /dev/null; then
        version=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" = "3" ] && [ "$minor" = "11" ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "ERROR: Python 3.11 is required (not 3.12, not 3.13)."
    echo ""
    echo "  Your current Python:"
    python3 --version 2>/dev/null || python --version 2>/dev/null || echo "  (not found)"
    echo ""
    echo "Install Python 3.11:"
    echo "  macOS:   brew install python@3.11"
    echo "  Windows: https://www.python.org/downloads/release/python-3119/"
    echo "           (install 時勾選 Add Python to PATH)"
    exit 1
fi

echo "[1/5] Python: $($PYTHON_CMD --version)"

# --- 2. Check C++ compiler ---
NEED_COMPILER_HELP=false

if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS: need Xcode Command Line Tools
    if ! xcode-select -p &> /dev/null; then
        echo ""
        echo "ERROR: Xcode Command Line Tools not installed."
        echo "       This is needed to compile chroma-hnswlib."
        echo ""
        echo "Run this command, wait for install to finish, then re-run this script:"
        echo "  xcode-select --install"
        exit 1
    fi
    echo "[2/5] C++ compiler: Xcode Command Line Tools OK"

elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    # Windows (Git Bash): check for MSVC
    if ! command -v cl &> /dev/null; then
        # cl.exe might not be in PATH but Build Tools could still be installed
        if [ -d "/c/Program Files/Microsoft Visual Studio" ] || \
           [ -d "/c/Program Files (x86)/Microsoft Visual Studio" ]; then
            echo "[2/5] C++ compiler: Visual Studio detected"
        else
            NEED_COMPILER_HELP=true
            echo "[2/5] C++ compiler: WARNING - Visual Studio Build Tools not detected"
            echo "       If pip install fails later, install from:"
            echo "       https://visualstudio.microsoft.com/visual-cpp-build-tools/"
            echo "       (select 'Desktop development with C++' workload)"
            echo ""
        fi
    else
        echo "[2/5] C++ compiler: MSVC OK"
    fi
fi

# --- 3. Create virtual environment ---
VENV_DIR="venv"
if [ -d "$VENV_DIR" ]; then
    echo "[3/5] Virtual environment: already exists"
else
    echo "[3/5] Creating virtual environment..."
    $PYTHON_CMD -m venv "$VENV_DIR"
fi

# Activate
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
    source "$VENV_DIR/Scripts/activate"
else
    source "$VENV_DIR/bin/activate"
fi
echo "       Activated: $(python --version) at $(which python)"

# --- 4. Install dependencies ---
echo "[4/5] Installing dependencies (first run may take several minutes)..."
pip install --upgrade pip --quiet 2>&1 | tail -1
if pip install -r requirements.txt 2>&1 | tail -5; then
    echo "       All dependencies installed."
else
    echo ""
    echo "ERROR: pip install failed."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "  Try: xcode-select --install"
    elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        echo "  Try: Install Visual Studio Build Tools (C++ workload)"
        echo "  https://visualstudio.microsoft.com/visual-cpp-build-tools/"
    fi
    exit 1
fi

# --- 5. Setup .env ---
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "[5/5] Created .env from .env.example"
        echo ""
        echo "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo "  !! 請編輯 .env 填入 API keys（找專案負責人拿）  !!"
        echo "  !! 至少需要: OPENAI_API_KEY, QDRANT_URL,        !!"
        echo "  !!          QDRANT_API_KEY                       !!"
        echo "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    else
        echo "[5/5] WARNING: .env.example not found. Create .env manually."
    fi
else
    echo "[5/5] .env already exists."
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "啟動 server："
echo ""
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
    echo "  source venv/Scripts/activate"
else
    echo "  source venv/bin/activate"
fi
echo "  cd code/python"
echo "  python app-file.py"
echo ""
echo "然後打開 http://localhost:8000"
