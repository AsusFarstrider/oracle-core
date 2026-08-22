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

function Normalize-ArgumentValue {
    param([string]$Value)

    $trimmed = $Value.Trim()
    if ($trimmed.Length -ge 2) {
        $first = $trimmed.Substring(0, 1)
        $last = $trimmed.Substring($trimmed.Length - 1, 1)
        if (($first -eq "'" -and $last -eq "'") -or ($first -eq '"' -and $last -eq '"')) {
            return $trimmed.Substring(1, $trimmed.Length - 2)
        }
    }
    return $trimmed
}

function Resolve-BrowserExe {
    param([string]$ConfiguredPath)

    $normalizedPath = Normalize-ArgumentValue -Value $ConfiguredPath
    if ($normalizedPath -and (Test-Path $normalizedPath)) {
        return $normalizedPath
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
$normalizedBrowserUrl = Normalize-ArgumentValue -Value $BrowserUrl
$normalizedTreatInsecureOriginAsSecure = Normalize-ArgumentValue -Value $TreatInsecureOriginAsSecure
$arguments = @(
    "--kiosk",
    $normalizedBrowserUrl,
    "--edge-kiosk-type=fullscreen",
    "--no-first-run"
)
if ($normalizedTreatInsecureOriginAsSecure) {
    $arguments += "--unsafely-treat-insecure-origin-as-secure=$normalizedTreatInsecureOriginAsSecure"
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
