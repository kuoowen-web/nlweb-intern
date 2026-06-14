#!/bin/bash
# GCP e2-micro 初始化腳本（Debian 12）
# 用途：春節期間在 GCP 免費方案跑 crawler + dashboard
# 記憶體：1GB RAM + 2GB swap，一次跑一個 source
set -e

echo "=== NLWeb Crawler - GCP e2-micro Setup ==="

# ==================== 系統套件 ====================
echo "[1/5] Installing system packages..."
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev \
    git tmux htop curl build-essential

# ==================== Swap（2GB 保險）====================
echo "[2/5] Setting up 2GB swap..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "Swap created and enabled."
else
    echo "Swap already exists, skipping."
fi

# ==================== Clone + venv ====================
echo "[3/5] Setting up project..."
cd ~
if [ ! -d nlweb ]; then
    echo "Please clone the repo first: git clone <REPO_URL> nlweb"
    echo "Then re-run this script."
    exit 1
fi
cd nlweb

echo "[4/5] Creating Python venv..."
python3.11 -m venv venv
source venv/bin/activate

# 只裝 crawler 需要的套件（省 500MB+）
pip install --no-cache-dir \
    aiohttp aiofiles pyyaml python-dotenv chardet \
    feedparser httpx beautifulsoup4 lxml \
    trafilatura htmldate charset-normalizer curl_cffi rich

# ==================== 建立資料目錄 ====================
echo "[5/5] Creating data directories..."
mkdir -p data/crawler/{articles,logs,crawled_ids,signals}

echo ""
echo "=== Setup Complete ==="
echo "Next steps:"
echo "  1. Copy crawled_registry.db from your desktop"
echo "  2. Start dashboard: source venv/bin/activate && cd code/python && python -m indexing.dashboard_server"
echo "  3. Or use systemd: sudo cp crawler/remote/nlweb-*.service /etc/systemd/system/ && sudo systemctl enable nlweb-dashboard"
echo ""
echo "Memory check:"
free -m
