param(
    [Parameter(Mandatory = $true)]
    [string]$SatelliteId,
    [Parameter(Mandatory = $true)]
    [string]$StoreRoot,
    [Parameter(Mandatory = $true)]
    [string]$RuntimeCompatibilityPath,
    [string]$TaskName = "OracleSatelliteProjectionSync",
    [switch]$Enable,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

function Test-AbsoluteWindowsPath {
    param([string]$Path)

    return $Path -match '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+(?:\\|$))'
}

if ($SatelliteId -notmatch '^[a-z][a-z0-9]*([_-][a-z0-9]+)*$') {
    throw "SatelliteId is invalid."
}
if (-not (Test-AbsoluteWindowsPath $StoreRoot)) {
    throw "StoreRoot must be absolute."
}
if ($StoreRoot.Contains('"')) {
    throw "StoreRoot contains an unsupported quote."
}
if (-not (Test-AbsoluteWindowsPath $RuntimeCompatibilityPath)) {
    throw "RuntimeCompatibilityPath must be absolute."
}
if ($RuntimeCompatibilityPath.Contains('"')) {
    throw "RuntimeCompatibilityPath contains an unsupported quote."
}
if ($RunNow -and -not $Enable) {
    throw "RunNow requires Enable."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$syncScript = Join-Path $scriptDir "sync-windows-satellite-projection.ps1"
if (-not (Test-Path $syncScript)) {
    throw "Projection sync wrapper is unavailable."
}

$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$syncScript`"",
    "-SatelliteId", "`"$SatelliteId`"",
    "-StoreRoot", "`"$StoreRoot`"",
    "-RuntimeCompatibilityPath", "`"$RuntimeCompatibilityPath`""
)
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ($arguments -join " ") `
    -WorkingDirectory $repoRoot
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$repeatTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($startupTrigger, $repeatTrigger) `
    -Settings $settings `
    -Principal $principal `
    -Description "Refresh Oracle satellite projection at startup and every minute" `
    -Force | Out-Null

if ($Enable) {
    Enable-ScheduledTask -TaskName $TaskName | Out-Null
    if ($RunNow) {
        Start-ScheduledTask -TaskName $TaskName
    }
    Write-Output "Installed and enabled scheduled task: $TaskName"
} else {
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Output "Installed scheduled task without enabling it: $TaskName"
}
