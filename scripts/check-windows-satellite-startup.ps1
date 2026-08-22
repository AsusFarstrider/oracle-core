param(
    [string]$RuntimeTaskName = "OracleSurfaceSatelliteRuntime",
    [string]$ControlTaskName = "OracleSurfaceSatelliteControl",
    [string]$UiTaskName = "OracleSurfaceSatelliteUI",
    [string]$RuntimeHealthUrl = "http://127.0.0.1:8022/health/config",
    [string]$ControlHealthUrl = "http://127.0.0.1:8021/health",
    [int]$CheckDelaySeconds = 90,
    [int]$RetryDelaySeconds = 10,
    [int]$Attempts = 6,
    [string]$LogDir = "logs\windows-satellite"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

if (-not [System.IO.Path]::IsPathRooted($LogDir)) {
    $LogDir = Join-Path $repoRoot $LogDir
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$logPath = Join-Path $LogDir "satellite-startup-check.log"

function Write-StartupLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
    Add-Content -Path $logPath -Value "$timestamp $Message"
}

function Test-HttpHealth {
    param([string]$Url)
    try {
        $response = Invoke-RestMethod -TimeoutSec 5 -Uri $Url
        return [bool]$response.ok
    } catch {
        Write-StartupLog "health_check_failed url=$Url error=$($_.Exception.Message)"
        return $false
    }
}

function Start-TaskIfNeeded {
    param([string]$TaskName)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-StartupLog "task_missing task=$TaskName"
        return
    }
    if ($task.State -ne "Running") {
        Write-StartupLog "task_start task=$TaskName state=$($task.State)"
        Start-ScheduledTask -TaskName $TaskName
    }
}

function Restart-Task {
    param([string]$TaskName)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-StartupLog "task_missing task=$TaskName"
        return
    }
    Write-StartupLog "task_restart task=$TaskName state=$($task.State)"
    if ($task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    Start-ScheduledTask -TaskName $TaskName
}

function Ensure-HealthyTask {
    param(
        [string]$TaskName,
        [string]$Url
    )
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        Start-TaskIfNeeded -TaskName $TaskName
        if (Test-HttpHealth -Url $Url) {
            Write-StartupLog "healthy task=$TaskName url=$Url attempt=$attempt"
            return
        }
        Start-Sleep -Seconds $RetryDelaySeconds
    }

    Restart-Task -TaskName $TaskName
    Start-Sleep -Seconds $RetryDelaySeconds
    if (Test-HttpHealth -Url $Url) {
        Write-StartupLog "healthy_after_restart task=$TaskName url=$Url"
    } else {
        Write-StartupLog "unhealthy_after_restart task=$TaskName url=$Url"
    }
}

try {
    Write-StartupLog "startup_check_begin delay_seconds=$CheckDelaySeconds attempts=$Attempts retry_delay_seconds=$RetryDelaySeconds"
    if ($CheckDelaySeconds -gt 0) {
        Start-Sleep -Seconds $CheckDelaySeconds
    }

    Ensure-HealthyTask -TaskName $RuntimeTaskName -Url $RuntimeHealthUrl

    Ensure-HealthyTask -TaskName $ControlTaskName -Url $ControlHealthUrl

    $edge = Get-Process msedge -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($edge) {
        Write-StartupLog "ui_process_present task=$UiTaskName process_id=$($edge.Id)"
    } else {
        Write-StartupLog "ui_process_missing_starting task=$UiTaskName"
        Start-TaskIfNeeded -TaskName $UiTaskName
    }

    Write-StartupLog "startup_check_complete"
} catch {
    Write-StartupLog "startup_check_failed error=$($_.Exception.Message)"
    throw
}
