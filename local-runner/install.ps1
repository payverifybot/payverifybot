# =====================================================================
# PayVerify Bot -- One-click Windows installer
# Handles: Docker install, code fetch, config, first run, shortcuts.
# =====================================================================

# =====================================================================
# PayVerify Bot -- one-click Windows installer (invoked by install.cmd).
# Handles: Docker install, code fetch, config, first run, shortcuts.
# =====================================================================
param(
    [string]$Repo = ""
)

$ErrorActionPreference = "Stop"
$InstallDir = "$env:USERPROFILE\PayVerifyBot"

if (-not $Repo) {
    # Fallback if launched directly without a repo argument
    $repoFile = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "repo.txt"
    if (Test-Path $repoFile) { $Repo = (Get-Content $repoFile -Raw).Trim() }
    if (-not $Repo) {
        Write-Host "First-time only: which GitHub repo has your PayVerify bot code?" -ForegroundColor Yellow
        Write-Host "  Format: username/repo-name (e.g. itbar/payverify-bot)" -ForegroundColor DarkGray
        $Repo = Read-Host "  Enter your repo"
    }
}
$RepoZip = "https://github.com/$Repo/archive/refs/heads/main.zip"

function Write-Step($msg) { Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    [X]  $msg" -ForegroundColor Red }

Clear-Host
Write-Host @"
=====================================================================
             PayVerify Bot -- one-click installer
=====================================================================
This will:
  * Install Docker Desktop (if not already installed)
  * Download the bot into $InstallDir
  * Ask you for two API keys (one-time)
  * Start the bot and open the dashboard in your browser
  * Create a "Start PayVerify Bot" desktop shortcut

Total time: 5-10 minutes on first run, 30 seconds after that.
"@ -ForegroundColor White

Read-Host "`nPress Enter to begin"

# ---------------------------------------------------------------------
# 1. Docker Desktop
# ---------------------------------------------------------------------
Write-Step "Checking for Docker Desktop..."
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Write-Warn "Docker not found. Installing via winget..."
    try {
        winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
    } catch {
        Write-Err "winget install failed. Please download Docker Desktop manually:"
        Write-Host "    https://www.docker.com/products/docker-desktop/" -ForegroundColor Cyan
        Write-Host "    Then run this installer again." -ForegroundColor Yellow
        exit 1
    }
    Write-OK "Docker Desktop installed."
    Write-Warn "You MUST restart your laptop now, then run this installer again."
    Read-Host "Press Enter to close (then restart your laptop)"
    exit 0
} else {
    Write-OK "Docker is installed."
}

# Make sure Docker daemon is running
Write-Step "Making sure Docker is running..."
$dockerRunning = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $dockerRunning = $true; break }
    } catch {}
    if ($i -eq 0) {
        Write-Warn "Docker daemon not running -- starting Docker Desktop..."
        $dd = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
        if (Test-Path $dd) { Start-Process $dd | Out-Null }
    }
    Write-Host "    waiting for Docker... ($($i+1)/30)" -ForegroundColor DarkGray
    Start-Sleep -Seconds 3
}
if (-not $dockerRunning) {
    Write-Err "Docker Desktop didn't start in time. Please open it manually and re-run this installer."
    exit 1
}
Write-OK "Docker is running."

# ---------------------------------------------------------------------
# 2. Download the code
# ---------------------------------------------------------------------
Write-Step "Downloading the bot into $InstallDir ..."
if (Test-Path $InstallDir) {
    Write-Warn "Existing install found -- updating in place."
} else {
    New-Item -ItemType Directory -Path $InstallDir | Out-Null
}
$zipPath = "$env:TEMP\payverify-bot.zip"
try {
    Invoke-WebRequest -Uri $RepoZip -OutFile $zipPath -UseBasicParsing
} catch {
    Write-Err "Failed to download from $RepoZip"
    Write-Host "    Check your internet connection, then try again." -ForegroundColor Yellow
    exit 1
}
Expand-Archive -Path $zipPath -DestinationPath $env:TEMP -Force
$extracted = Get-ChildItem "$env:TEMP" -Directory | Where-Object { $_.Name -like "*payverify*" -or $_.Name -like "*main*" } | Select-Object -First 1
if (-not $extracted) {
    Write-Err "Could not find extracted folder. Aborting."
    exit 1
}
Copy-Item -Path "$($extracted.FullName)\*" -Destination $InstallDir -Recurse -Force
Remove-Item $zipPath -Force
Write-OK "Code downloaded."

# ---------------------------------------------------------------------
# 3. Config: prompt for keys once, save to .env
# ---------------------------------------------------------------------
$envFile = Join-Path $InstallDir "local-runner\.env"
if (-not (Test-Path $envFile)) {
    Write-Step "First-time setup -- enter your API keys (one time only)"
    Write-Host "    Get these from your Emergent app -> Code viewer -> backend/.env" -ForegroundColor DarkGray

    $llmKey  = Read-Host "  Paste EMERGENT_LLM_KEY (starts with sk-emergent-)"
    $mailKey = Read-Host "  Paste EMERGENT_EMAIL_KEY (starts with ek_)"
    $bizName = Read-Host "  Your business name (shown on emails)"
    $encKey  = Read-Host "  Paste GPAY_ENC_KEY (or press Enter to generate a new one)"

    if (-not $encKey) {
        Add-Type -AssemblyName System.Security
        $bytes = New-Object byte[] 32
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $encKey = [Convert]::ToBase64String($bytes).TrimEnd("=") + "="
    }
    $webhookSecret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | % { [char]$_ })

    @"
EMERGENT_LLM_KEY=$llmKey
EMERGENT_EMAIL_KEY=$mailKey
EMAIL_FROM_NAME=$bizName
WEBHOOK_CRON_SECRET=$webhookSecret
GPAY_ENC_KEY=$encKey
"@ | Set-Content -Path $envFile -Encoding UTF8
    Write-OK "Config saved."
} else {
    Write-OK "Existing config found -- keeping it."
}

# ---------------------------------------------------------------------
# 4. Build & run
# ---------------------------------------------------------------------
Write-Step "Building and starting the bot (first run downloads ~500MB, be patient)..."
Push-Location (Join-Path $InstallDir "local-runner")
docker compose up -d --build
$exitCode = $LASTEXITCODE
Pop-Location
if ($exitCode -ne 0) {
    Write-Err "Docker compose failed. Please share the error above with support."
    exit 1
}
Write-OK "Bot is up and running."

# ---------------------------------------------------------------------
# 5. Desktop shortcut + startup entry
# ---------------------------------------------------------------------
Write-Step "Creating desktop shortcut..."
$shortcutPath = "$env:USERPROFILE\Desktop\PayVerify Bot.lnk"
$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($shortcutPath)
$sc.TargetPath = "cmd.exe"
$sc.Arguments  = "/c start http://localhost:3000"
$sc.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$sc.WorkingDirectory = "$InstallDir\local-runner"
$sc.Save()
Write-OK "Shortcut created on your Desktop."

# Startup: make Docker + this compose auto-run on login
$startup = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
$startupCmd = Join-Path $startup "PayVerifyBot-AutoStart.cmd"
@"
@echo off
cd /d "$InstallDir\local-runner"
docker compose up -d
"@ | Set-Content -Path $startupCmd -Encoding ASCII
Write-OK "Bot will auto-start when you log in to Windows."

# ---------------------------------------------------------------------
# 6. Open the dashboard
# ---------------------------------------------------------------------
Write-Step "Opening the dashboard..."
Start-Sleep -Seconds 3
Start-Process "http://localhost:3000"

Write-Host @"

=====================================================================
                        ALL DONE! 
=====================================================================
The dashboard is now open in your browser.

Next steps in the dashboard:
  1. Go to Setup -> add your WhatsApp group names -> Save
  2. Setup -> WhatsApp session -> Start -> scan QR from your phone
  3. Setup -> GPay Business accounts -> add each account
  4. Post a payment screenshot in your group and watch the bot reply

Daily use:
  * Double-click "PayVerify Bot" on your Desktop to open the dashboard
  * The bot runs automatically in the background whenever your laptop is on

To share with another office: just send them this same install.cmd file.

"@ -ForegroundColor Green
Read-Host "Press Enter to close this window"
