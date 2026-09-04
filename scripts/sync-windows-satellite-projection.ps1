param(
    [Parameter(Mandatory = $true)]
    [string]$SatelliteId,
    [Parameter(Mandatory = $true)]
    [string]$StoreRoot,
    [Parameter(Mandatory = $true)]
    [string]$RuntimeCompatibilityPath,
    [string]$RuntimeTaskName = "OracleSurfaceSatelliteRuntime",
    [string]$ControlTaskName = "OracleSurfaceSatelliteControl"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$syncScript = Join-Path $repoRoot "oracle_satellite_projection_sync.py"
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = Join-Path $repoRoot "satellite\.venv\Scripts\python.exe"
}
if (-not (Test-Path $python)) {
    throw "Satellite Python environment is unavailable."
}
if (-not (Test-Path $syncScript)) {
    throw "Projection sync command is unavailable."
}

$syncArgs = @(
    $syncScript,
    "--satellite-id", $SatelliteId,
    "--store-root", $StoreRoot,
    "--runtime-compatibility", $RuntimeCompatibilityPath
)

$syncOutput = & $python @syncArgs 2>&1
$syncStatus = $LASTEXITCODE
$syncOutput | ForEach-Object { Write-Output $_ }
if ($syncStatus -eq 0) {
    exit 0
}
if ($syncStatus -ne 3) {
    throw "Satellite projection pull or local installation failed."
}

function Wait-TaskReady {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskName,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        if ($task.State -eq "Ready") {
            return
        }
        if ($task.State -eq "Disabled") {
            throw "Scheduled task is disabled after stop: $TaskName"
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)

    throw "Scheduled task did not reach Ready after stop: $TaskName"
}

foreach ($taskName in @($ControlTaskName, $RuntimeTaskName)) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        continue
    }
    if ($task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $taskName
        Wait-TaskReady -TaskName $taskName
    } elseif ($task.State -ne "Ready") {
        throw "Scheduled task is not safely restartable: $taskName state=$($task.State)"
    }
    Start-ScheduledTask -TaskName $taskName
}

$markOutput = & $python @syncArgs --mark-restarted 2>&1
$markStatus = $LASTEXITCODE
$markOutput | ForEach-Object { Write-Output $_ }
if ($markStatus -ne 0) {
    throw "Runtime restart succeeded but the durable latch could not be cleared."
}
