param(
    [string]$OracleOrigin = "http://oracle-brain.local:8011"
)

$ErrorActionPreference = "Stop"

if (-not $OracleOrigin.Trim()) {
    throw "OracleOrigin is required."
}

$edgePolicyPath = "HKCU:\Software\Policies\Microsoft\Edge"
$audioAllowlistPath = Join-Path $edgePolicyPath "AudioCaptureAllowedUrls"

New-Item -Path $edgePolicyPath -Force | Out-Null
New-ItemProperty `
    -Path $edgePolicyPath `
    -Name "AudioCaptureAllowed" `
    -PropertyType DWord `
    -Value 1 `
    -Force | Out-Null

New-Item -Path $audioAllowlistPath -Force | Out-Null
New-ItemProperty `
    -Path $audioAllowlistPath `
    -Name "1" `
    -PropertyType String `
    -Value $OracleOrigin.Trim() `
    -Force | Out-Null

Write-Output "Edge microphone capture is allowed for $($OracleOrigin.Trim())."
