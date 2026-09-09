param(
    [Parameter(Mandatory = $true)]
    [string]$SatelliteId,
    [Parameter(Mandatory = $true)]
    [string]$ProjectionStoreRoot,
    [Parameter(Mandatory = $true)]
    [string]$RuntimeCompatibilityPath,
    [string]$BindHost = "0.0.0.0",
    [int]$BindPort = 8021,
    [string]$LogLevel = "INFO",
    [string]$LogDir = "logs\windows-satellite"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$control = Join-Path $repoRoot "satellite\control_service.py"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Satellite Python environment is unavailable." }
if (-not (Test-Path -LiteralPath $control -PathType Leaf)) { throw "Satellite control runtime is unavailable." }

Remove-Item Env:ORACLE_ALLOW_LEGACY_SATELLITE_CONFIGURATION -ErrorAction SilentlyContinue
$env:ORACLE_SATELLITE_ID = $SatelliteId
$env:ORACLE_SATELLITE_PROJECTION_STORE_ROOT = $ProjectionStoreRoot
$env:ORACLE_SATELLITE_RUNTIME_COMPATIBILITY_PATH = $RuntimeCompatibilityPath
$env:ORACLE_SATELLITE_CONTROL_BIND_HOST = $BindHost
$env:ORACLE_SATELLITE_CONTROL_BIND_PORT = [string]$BindPort
$env:ORACLE_SATELLITE_CONTROL_LOG_LEVEL = $LogLevel
$ffmpeg = Join-Path $repoRoot "tools\ffmpeg\bin"
if (Test-Path -LiteralPath $ffmpeg -PathType Container) { $env:PATH = "$ffmpeg;$env:PATH" }

$resolvedLogDir = if ([System.IO.Path]::IsPathRooted($LogDir)) { $LogDir } else { Join-Path $repoRoot $LogDir }
New-Item -ItemType Directory -Force -Path $resolvedLogDir | Out-Null
$process = Start-Process -FilePath $python -ArgumentList @($control) -WorkingDirectory $repoRoot `
    -RedirectStandardOutput (Join-Path $resolvedLogDir "oracle-windows-control.out.log") `
    -RedirectStandardError (Join-Path $resolvedLogDir "oracle-windows-control.err.log") `
    -NoNewWindow -Wait -PassThru
exit $process.ExitCode
