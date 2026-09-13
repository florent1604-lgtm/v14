$ErrorActionPreference = "Stop"

$runtime = (Join-Path $env:LOCALAPPDATA "V14-Observability").Replace("\", "/").ToLowerInvariant()
$targets = Get-CimInstance Win32_Process | Where-Object {
    $executable = ([string]$_.ExecutablePath).Replace("\", "/").ToLowerInvariant()
    $executable.StartsWith($runtime) -and
        ($executable.EndsWith("/prometheus.exe") -or $executable.EndsWith("/grafana.exe"))
}

foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -Force
    Write-Output "Supervision arrêtée : PID $($target.ProcessId)"
}

if (-not $targets) {
    Write-Output "Aucun processus Prometheus/Grafana V14 actif."
}
