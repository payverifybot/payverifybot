# =====================================================================
# PayVerify Bot -- Native Windows install (invoked by install-native.cmd)
# Installs Python + MongoDB + Playwright Chromium locally, no Docker.
# Runs Chromium HEADED so Google 2FA / captcha screens are visible.
# =====================================================================
$ErrorActionPreference = "Stop"

$InstallDir = "$env:USERPROFILE\PayVerifyBot-Native"
$VenvDir    = Join-Path $InstallDir ".venv"
$EnvFile    = Join-Path $InstallDir ".env"
$BackendDir = Join-Path $InstallDir "backend"
$FrontendBuildDir = Join-Path $InstallDir "frontend\build"

function Write-Step($m) { Write-Host "`n>>> $m" -ForegroundColor Cyan }
function Write-OK($m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "    [!]  $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "    [X]  $m" -ForegroundColor Red }

# ---------------------------------------------------------------------
# 1. Python 3.11 (winget)
# ---------------------------------------------------------------------
Write-Step "Checking Python 3.11..."
$py = Get-Command py -ErrorAction SilentlyContinue
$hasPy311 = $false
if ($py) {
    $vers = & py -0 2>$null
    if ($vers -match "3\.11") { $hasPy311 = $true }
}
if (-not $hasPy311) {
    Write-Warn "Python 3.11 not found -- installing via winget..."
    winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        Write-Err "winget install of Python failed. Install from https://www.python.org/downloads/release/python-3119/ then re-run."
        exit 1
    }
    # winget doesn't refresh PATH for this session; use full path
    Write-OK "Python 3.11 installed. Please close this window and re-run install-native.cmd."
    exit 0
}
Write-OK "Python 3.11 present."

# ---------------------------------------------------------------------
# 2. MongoDB Community
# ---------------------------------------------------------------------
Write-Step "Checking MongoDB..."
$mongoSvc = Get-Service -Name "MongoDB" -ErrorAction SilentlyContinue
if (-not $mongoSvc) {
    Write-Warn "MongoDB service not found -- installing MongoDB 7 Community..."
    winget install -e --id MongoDB.Server --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        Write-Err "MongoDB install failed. Install manually from https://www.mongodb.com/try/download/community"
        exit 1
    }
    Start-Sleep -Seconds 4
    $mongoSvc = Get-Service -Name "MongoDB" -ErrorAction SilentlyContinue
}
if ($mongoSvc -and $mongoSvc.Status -ne "Running") {
    Start-Service $mongoSvc
}
Write-OK "MongoDB running as a Windows service."

# ---------------------------------------------------------------------
# 3. Install dir
# ---------------------------------------------------------------------
Write-Step "Preparing install folder $InstallDir ..."
if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir | Out-Null }

# Copy the backend + built frontend from wherever install-native.cmd was run.
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Write-OK "Copying backend from $RepoRoot ..."
Copy-Item -Path (Join-Path $RepoRoot "backend") -Destination $InstallDir -Recurse -Force
if (Test-Path (Join-Path $RepoRoot "frontend\build")) {
    Copy-Item -Path (Join-Path $RepoRoot "frontend\build") -Destination (Join-Path $InstallDir "frontend\") -Recurse -Force
    Write-OK "Copied pre-built React bundle."
} else {
    Write-Warn "No frontend\build found. The dashboard will not be served."
    Write-Warn "Run 'cd frontend && yarn install && yarn build' in your repo first."
}

# ---------------------------------------------------------------------
# 4. Virtual env + deps
# ---------------------------------------------------------------------
Write-Step "Creating virtual environment..."
if (-not (Test-Path $VenvDir)) {
    py -3.11 -m venv $VenvDir
}
$Python = Join-Path $VenvDir "Scripts\python.exe"
& $Python -m pip install --upgrade pip

Write-Step "Installing Python packages (this may take a couple minutes)..."
& $Python -m pip install --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/ `
    -r (Join-Path $RepoRoot "local-runner\requirements.txt")
if ($LASTEXITCODE -ne 0) { Write-Err "pip install failed."; exit 1 }
Write-OK "Python packages installed."

Write-Step "Installing Playwright Chromium browser..."
& $Python -m playwright install chromium
Write-OK "Chromium installed."

# ---------------------------------------------------------------------
# 5. .env
# ---------------------------------------------------------------------
if (-not (Test-Path $EnvFile)) {
    Write-Step "First-time setup -- enter your API keys once"
    Write-Host "    Get these from your Emergent app -> Code viewer -> backend/.env" -ForegroundColor DarkGray

    $llmKey  = Read-Host "  Paste EMERGENT_LLM_KEY (starts with sk-emergent-)"
    $mailKey = Read-Host "  Paste EMERGENT_EMAIL_KEY (starts with ek_)"
    $bizName = Read-Host "  Your business name (shown on emails)"
    $encKey  = Read-Host "  Paste GPAY_ENC_KEY (or press Enter to generate a new one)"

    if (-not $encKey) {
        Add-Type -AssemblyName System.Security
        $bytes = New-Object byte[] 32
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $encKey = [Convert]::ToBase64String($bytes)
    }
    $webhookSecret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | % { [char]$_ })

    @"
MONGO_URL=mongodb://localhost:27017
DB_NAME=payverify
BOT_MOCK_MODE=false
PLAYWRIGHT_HEADLESS=false
EMERGENT_LLM_KEY=$llmKey
EMERGENT_EMAIL_KEY=$mailKey
EMAIL_FROM_NAME=$bizName
WEBHOOK_CRON_SECRET=$webhookSecret
GPAY_ENC_KEY=$encKey
WA_PROFILE_DIR=$($InstallDir -replace '\\','\\')\\wa_profile
GPAY_PROFILE_DIR=$($InstallDir -replace '\\','\\')\\gpay_profiles
"@ | Set-Content -Path $EnvFile -Encoding UTF8

    Write-OK "Config saved to $EnvFile"
} else {
    Write-OK "Existing .env found -- keeping it."
}

# Symlink .env into the backend folder so python-dotenv picks it up
$backendEnv = Join-Path $InstallDir "backend\.env"
Copy-Item -Path $EnvFile -Destination $backendEnv -Force

# ---------------------------------------------------------------------
# 6. start.cmd + stop.cmd + Desktop shortcut
# ---------------------------------------------------------------------
$startCmd = Join-Path $InstallDir "start.cmd"
@"
@echo off
setlocal
cd /d "$InstallDir\backend"
"$Python" -m uvicorn server:app --host 127.0.0.1 --port 8001
"@ | Set-Content -Path $startCmd -Encoding ASCII

$stopCmd = Join-Path $InstallDir "stop.cmd"
@"
@echo off
taskkill /f /im chrome.exe 2>nul
taskkill /f /im python.exe 2>nul
echo Stopped.
"@ | Set-Content -Path $stopCmd -Encoding ASCII

Write-Step "Creating desktop shortcut..."
$shortcutPath = "$env:USERPROFILE\Desktop\PayVerify Bot (Native).lnk"
$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($shortcutPath)
$sc.TargetPath = "cmd.exe"
$sc.Arguments  = "/c start `"`" `"$startCmd`" && timeout /t 4 && start http://localhost:8001"
$sc.WorkingDirectory = $InstallDir
$sc.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$sc.Save()
Write-OK "Shortcut created: PayVerify Bot (Native) on your Desktop."

# ---------------------------------------------------------------------
# 7. Launch
# ---------------------------------------------------------------------
Write-Step "Starting the bot..."
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $startCmd
Start-Sleep -Seconds 5
Start-Process "http://localhost:8001"

Write-Host @"

=====================================================================
                       ALL DONE (native).
=====================================================================
Dashboard: http://localhost:8001
Every time you log in to Windows, click the Desktop shortcut to start
the bot. To stop it, close the terminal window that opened.

WHY THIS IS BETTER THAN DOCKER FOR GPAY:
  * Chromium runs on your desktop as a real, VISIBLE window.
  * When Google asks for 2FA, the popup opens on YOUR screen --
    approve on your phone, or type the code in the browser, and
    the bot will detect success and continue automatically.
  * Captchas also render in the visible browser -- solve them once,
    the persistent profile keeps you signed in for next time.

=====================================================================
"@ -ForegroundColor Green
