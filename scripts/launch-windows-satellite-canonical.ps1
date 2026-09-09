param(
    [Parameter(Mandatory = $true)]
    [string]$SatelliteId,
    [Parameter(Mandatory = $true)]
    [string]$ProjectionStoreRoot,
    [Parameter(Mandatory = $true)]
    [string]$RuntimeCompatibilityPath,
    [string]$WakeCaptureStoragePath = "",
    [string]$LogDir = "logs\windows-satellite"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$runtime = Join-Path $repoRoot "satellite\pi_wake_satellite.py"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Satellite Python environment is unavailable." }
if (-not (Test-Path -LiteralPath $runtime -PathType Leaf)) { throw "Satellite runtime is unavailable." }

Remove-Item Env:ORACLE_ALLOW_LEGACY_SATELLITE_CONFIGURATION -ErrorAction SilentlyContinue
$env:ORACLE_SATELLITE_ID = $SatelliteId
$env:ORACLE_SATELLITE_PROJECTION_STORE_ROOT = $ProjectionStoreRoot
$env:ORACLE_SATELLITE_RUNTIME_COMPATIBILITY_PATH = $RuntimeCompatibilityPath
if ($WakeCaptureStoragePath) { $env:ORACLE_WAKE_CAPTURE_LOCAL_STORAGE_PATH = $WakeCaptureStoragePath }
$ffmpeg = Join-Path $repoRoot "tools\ffmpeg\bin"
if (Test-Path -LiteralPath $ffmpeg -PathType Container) { $env:PATH = "$ffmpeg;$env:PATH" }

$resolvedLogDir = if ([System.IO.Path]::IsPathRooted($LogDir)) { $LogDir } else { Join-Path $repoRoot $LogDir }
New-Item -ItemType Directory -Force -Path $resolvedLogDir | Out-Null
$process = Start-Process -FilePath $python -ArgumentList @($runtime) -WorkingDirectory $repoRoot `
    -RedirectStandardOutput (Join-Path $resolvedLogDir "oracle-windows-satellite.out.log") `
    -RedirectStandardError (Join-Path $resolvedLogDir "oracle-windows-satellite.err.log") `
    -NoNewWindow -Wait -PassThru
exit $process.ExitCode
