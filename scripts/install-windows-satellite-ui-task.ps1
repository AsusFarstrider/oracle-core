param(
    [string]$TaskName = "OracleSurfaceSatelliteUI",
    [string]$BrowserUrl,
    [string]$BrowserExe = "",
    [string]$TreatInsecureOriginAsSecure = "",
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

if (-not $BrowserUrl.Trim()) {
    throw "BrowserUrl is required."
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

$action = New-ScheduledTaskAction `
    -Execute $resolvedBrowser `
    -Argument ($arguments -join " ")

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Set-ScheduledTask -TaskName $TaskName -Action $action | Out-Null
} else {
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Days 0)
    $principal = New-ScheduledTaskPrincipal `
        -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
        -LogonType Interactive `
        -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Oracle Surface satellite browser UI" | Out-Null
}

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
}

Write-Output "Installed scheduled task: $TaskName"
Write-Output "Command: $resolvedBrowser $($arguments -join ' ')"
