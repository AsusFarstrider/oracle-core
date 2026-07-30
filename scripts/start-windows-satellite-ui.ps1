param(
    [string]$BrowserUrl,
    [string]$BrowserExe = "",
    [int]$DelaySeconds = 45,
    [string]$TreatInsecureOriginAsSecure = "",
    [string]$LogDir = "logs\windows-satellite"
)

$ErrorActionPreference = "Stop"

if (-not $BrowserUrl.Trim()) {
    throw "BrowserUrl is required."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

if (-not [System.IO.Path]::IsPathRooted($LogDir)) {
    $LogDir = Join-Path $repoRoot $LogDir
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$logPath = Join-Path $LogDir "satellite-ui.log"

function Write-UiLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
    Add-Content -Path $logPath -Value "$timestamp $Message"
}

function Resolve-BrowserExe {
    param([string]$ConfiguredPath)
    if ($ConfiguredPath.Trim() -and (Test-Path $ConfiguredPath.Trim())) {
        return $ConfiguredPath.Trim()
    }

    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    throw "Could not find Edge or Chrome. Set -BrowserExe to the browser executable path."
}

try {
    Write-UiLog "ui_start_requested url=$BrowserUrl delay_seconds=$DelaySeconds"
    if ($DelaySeconds -gt 0) {
        Start-Sleep -Seconds $DelaySeconds
    }

    $resolvedBrowser = Resolve-BrowserExe -ConfiguredPath $BrowserExe
    $arguments = @(
        "--kiosk",
        $BrowserUrl.Trim(),
        "--edge-kiosk-type=fullscreen",
        "--no-first-run"
    )
    if ($TreatInsecureOriginAsSecure.Trim()) {
        $arguments += "--unsafely-treat-insecure-origin-as-secure=$($TreatInsecureOriginAsSecure.Trim())"
    }

    Write-UiLog "ui_launch browser=$resolvedBrowser args=$($arguments -join ' ')"
    Start-Process -FilePath $resolvedBrowser -ArgumentList $arguments
    Write-UiLog "ui_launch_dispatched"
} catch {
    Write-UiLog "ui_launch_failed error=$($_.Exception.Message)"
    throw
}
