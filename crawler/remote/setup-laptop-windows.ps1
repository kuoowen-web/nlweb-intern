# NLWeb Crawler - Windows 筆電防休眠設定
# 用途：讓筆電 24/7 運行 crawler，蓋上螢幕不休眠
# 以管理員身份執行：Right-click → Run as Administrator

Write-Host "=== NLWeb Crawler - Windows Laptop Setup ===" -ForegroundColor Cyan

# ==================== 防休眠 ====================
Write-Host "`n[1/3] Disabling sleep/hibernate..." -ForegroundColor Yellow

# 插電時不休眠
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0

# 電池時也不休眠（保險）
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-dc 0
powercfg /change monitor-timeout-dc 30

# 蓋上螢幕不做任何動作（插電 + 電池）
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT

Write-Host "  Sleep/hibernate disabled." -ForegroundColor Green

# ==================== 建立開機自動啟動 ====================
Write-Host "`n[2/3] Setting up auto-start..." -ForegroundColor Yellow

$StartupFolder = [System.IO.Path]::Combine(
    [Environment]::GetFolderPath("Startup"),
    "start-nlweb-dashboard.bat"
)

$BatContent = @"
@echo off
cd /d C:\Users\User\nlweb\code\python
python -m indexing.dashboard_server
"@

$BatContent | Out-File -FilePath $StartupFolder -Encoding ASCII
Write-Host "  Created: $StartupFolder" -ForegroundColor Green

# ==================== 提醒 ====================
Write-Host "`n[3/3] Manual steps required:" -ForegroundColor Yellow
Write-Host "  1. Auto-login: Run 'netplwiz' -> uncheck 'Users must enter...' -> Apply" -ForegroundColor White
Write-Host "  2. Windows Update: Settings -> Update -> Advanced -> Active Hours -> 0:00-23:59" -ForegroundColor White
Write-Host "  3. Chrome Remote Desktop: Install from remotedesktop.google.com" -ForegroundColor White
Write-Host "  4. Keep laptop plugged in at all times" -ForegroundColor White

Write-Host "`n=== Setup Complete ===" -ForegroundColor Cyan
