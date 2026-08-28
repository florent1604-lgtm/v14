$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtime = Join-Path $env:LOCALAPPDATA "V14-Observability"
$prometheusHome = Join-Path $runtime "packages\prometheus-3.14.0.windows-amd64"
$grafanaHome = Join-Path $runtime "packages\grafana-13.2.0"
$prometheusExe = Join-Path $prometheusHome "prometheus.exe"
$grafanaExe = Join-Path $grafanaHome "bin\grafana.exe"
$prometheusConfig = Join-Path $repo "observability\prometheus\prometheus-bare-metal.yml"
$provisioning = Join-Path $repo "observability\grafana\provisioning"
$dashboards = Join-Path $repo "observability\grafana\dashboards"
$dataRoot = Join-Path $runtime "data"

foreach ($required in @($prometheusExe, $grafanaExe, $prometheusConfig, $provisioning, $dashboards)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Composant de supervision introuvable : $required"
    }
}

$prometheusData = Join-Path $dataRoot "prometheus"
$grafanaData = Join-Path $dataRoot "grafana"
$grafanaLogs = Join-Path $runtime "logs\grafana"
$grafanaPlugins = Join-Path $runtime "plugins"
New-Item -ItemType Directory -Force -Path $prometheusData, $grafanaData, $grafanaLogs, $grafanaPlugins | Out-Null

function Test-ListeningPort([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

if (-not (Test-ListeningPort 9090)) {
    $prometheusArgs = @(
        "--config.file=$prometheusConfig",
        "--storage.tsdb.path=$prometheusData",
        "--storage.tsdb.retention.time=30d",
        "--web.listen-address=127.0.0.1:9090"
    )
    Start-Process -FilePath $prometheusExe -ArgumentList $prometheusArgs `
        -WorkingDirectory $prometheusHome -WindowStyle Hidden | Out-Null
}

if (-not (Test-ListeningPort 3000)) {
    $env:PROMETHEUS_URL = "http://127.0.0.1:9090"
    $env:GRAFANA_DASHBOARDS_PATH = $dashboards
    if (-not $env:DISCORD_WEBHOOK_URL) {
        $env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/REPLACE/ME"
    }
    $env:GF_PATHS_DATA = $grafanaData
    $env:GF_PATHS_LOGS = $grafanaLogs
    $env:GF_PATHS_PLUGINS = $grafanaPlugins
    $env:GF_PATHS_PROVISIONING = $provisioning
    $env:GF_SERVER_HTTP_ADDR = "127.0.0.1"
    $env:GF_SERVER_HTTP_PORT = "3000"
    $env:GF_SECURITY_ADMIN_USER = "admin"
    $env:GF_SECURITY_ADMIN_PASSWORD = "admin"
    $env:GF_USERS_ALLOW_SIGN_UP = "false"
    $env:GF_PLUGINS_PLUGIN_ADMIN_ENABLED = "false"
    $env:GF_PLUGINS_PREINSTALL_DISABLED = "true"
    $env:GF_PLUGINS_PREINSTALL_AUTO_UPDATE = "false"

    Start-Process -FilePath $grafanaExe -ArgumentList @(
        "server", "--homepath=$grafanaHome", "--packaging=standalone"
    ) -WorkingDirectory $grafanaHome -WindowStyle Hidden | Out-Null
}

$deadline = [DateTime]::UtcNow.AddSeconds(90)
do {
    Start-Sleep -Milliseconds 500
    $ready = (Test-ListeningPort 9090) -and (Test-ListeningPort 3000)
} until ($ready -or [DateTime]::UtcNow -ge $deadline)

if (-not $ready) {
    throw "Prometheus ou Grafana n'a pas ouvert son port dans les 90 secondes."
}

Write-Output "Prometheus : http://127.0.0.1:9090"
Write-Output "Grafana    : http://127.0.0.1:3000 (admin/admin au premier accès)"
