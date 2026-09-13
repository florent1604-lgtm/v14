$ErrorActionPreference = "Stop"

$launcher = (Resolve-Path (Join-Path $PSScriptRoot "start-observability.ps1")).Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "V14-Observability" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Prometheus et Grafana locaux pour Titanium V14" `
    -Force | Out-Null

Write-Output "Tâche planifiée V14-Observability enregistrée pour $env:USERNAME."
