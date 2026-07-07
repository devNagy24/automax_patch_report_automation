param(
    [string]$ConfigPath = "$PSScriptRoot\config.json",
    [string]$OutputRoot = "$PSScriptRoot\reports",
    [int]$Days = 30,
    [string]$PythonPath = "python"
)

$ErrorActionPreference = "Stop"

if (-not $env:AUTOMOX_API_KEY) {
    throw "AUTOMOX_API_KEY is not set for this user/session."
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Config file not found: $ConfigPath. Copy config.example.json to config.json and fill in org_id or org_uuid."
}

& $PythonPath "$PSScriptRoot\automox_patch_report.py" `
    --config $ConfigPath `
    --days $Days `
    --output-dir $OutputRoot

exit $LASTEXITCODE
