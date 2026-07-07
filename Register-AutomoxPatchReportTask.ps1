param(
    [string]$TaskName = "Automox Patch Impact Report",
    [string]$RunAt = "06:00",
    [string]$ScriptPath = "$PSScriptRoot\run_automox_report.ps1",
    [string]$ConfigPath = "$PSScriptRoot\config.json",
    [string]$OutputRoot = "$PSScriptRoot\reports",
    [int]$Days = 30,
    [string]$PythonPath = "python"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "ScriptPath not found: $ScriptPath"
}

$argument = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$ScriptPath`"",
    "-ConfigPath", "`"$ConfigPath`"",
    "-OutputRoot", "`"$OutputRoot`"",
    "-Days", $Days,
    "-PythonPath", "`"$PythonPath`""
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument
$trigger = New-ScheduledTaskTrigger -Daily -At $RunAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Generate Automox patch impact/performance report CSV and HTML outputs." -Force

Write-Host "Registered scheduled task '$TaskName' to run daily at $RunAt."
