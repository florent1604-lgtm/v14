param(
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"
if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot
}
$Root = [System.IO.Path]::GetFullPath($Root)
$Results = Join-Path $Root "results"
$Logs = Join-Path $Root "collab\prime_agent\runs\strategie-entree-20260819\rejeu_univers_logs"
$StatePath = Join-Path $Results "suivi_v14_10min.json"
$HistoryPath = Join-Path $Results "suivi_v14_10min.ndjson"

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
    catch { return $null }
}

function Read-LastNdjson([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $line = Get-Content -LiteralPath $Path -Tail 1
        if ($line) { return $line | ConvertFrom-Json }
    }
    catch { return $null }
    return $null
}

New-Item -ItemType Directory -Path $Results -Force | Out-Null
$previous = Read-JsonFile $StatePath
$heartbeat = Read-JsonFile (Join-Path $Results "loop_heartbeat.json")
$pending = Read-JsonFile (Join-Path $Results "pending_limits.json")
$lastLimit = Read-LastNdjson (Join-Path $Results "limit_lifecycle.ndjson")
$lastTrade = Read-LastNdjson (Join-Path $Results "trades.ndjson")

$workers = @()
try {
    $workers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'tools[\\/]rejeu_univers\.py' } |
        ForEach-Object {
            [ordered]@{
                pid = [int]$_.ProcessId
                command = [string]$_.CommandLine
            }
        })
}
catch {
    $workers = @()
}

$rawRoot = Join-Path $Results "rejeu_univers_brut"
$rawArtifacts = 0
if (Test-Path -LiteralPath $rawRoot) {
    $rawArtifacts = @(Get-ChildItem -LiteralPath $rawRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "manifest.json") }).Count
}

$errorLogs = @()
if (Test-Path -LiteralPath $Logs) {
    $errorLogs = @(Get-ChildItem -LiteralPath $Logs -Filter "backfill_v2_lot*.err.log" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -gt 0 } |
        ForEach-Object {
            [ordered]@{
                name = $_.Name
                bytes = [long]$_.Length
                modified_at = $_.LastWriteTimeUtc.ToString("o")
            }
        })
}

$warnings = @()
if ($workers.Count -eq 0 -and $rawArtifacts -lt 149) {
    $warnings += "Backfill arrete avant 149 artefacts"
}
if ($errorLogs.Count -gt 0) {
    $warnings += "Un ou plusieurs journaux d'erreur du backfill ne sont pas vides"
}
if (-not $heartbeat) {
    $warnings += "Heartbeat DEMO absent ou illisible"
}

$lastLimitId = if ($lastLimit) { [string]$lastLimit.event_id } else { "" }
$lastTradeId = if ($lastTrade) { [string]$lastTrade.ticket + "|" + [string]$lastTrade.closed_at } else { "" }
$previousLimitId = if ($previous) { [string]$previous.last_limit_id } else { "" }
$previousTradeId = if ($previous) { [string]$previous.last_trade_id } else { "" }

$state = [ordered]@{
    checked_at = (Get-Date).ToUniversalTime().ToString("o")
    root = $Root
    backfill = [ordered]@{
        workers = $workers
        worker_count = $workers.Count
        raw_artifacts = $rawArtifacts
        target = 149
        error_logs = $errorLogs
    }
    demo = [ordered]@{
        heartbeat = $heartbeat
        pending_limits = $pending
        latest_limit_event = $lastLimit
        latest_closed_trade = $lastTrade
    }
    warnings = $warnings
    last_limit_id = $lastLimitId
    last_trade_id = $lastTradeId
}

$json = $state | ConvertTo-Json -Depth 20
$temp = "$StatePath.tmp"
[System.IO.File]::WriteAllText($temp, $json, [System.Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temp -Destination $StatePath -Force

$material = ($lastLimitId -ne $previousLimitId) -or
            ($lastTradeId -ne $previousTradeId) -or
            (-not $previous) -or
            ($rawArtifacts -ne [int]$previous.backfill.raw_artifacts) -or
            ($warnings.Count -gt 0)
if ($material) {
    $event = [ordered]@{
        checked_at = $state.checked_at
        raw_artifacts = $rawArtifacts
        worker_count = $workers.Count
        last_limit_id = $lastLimitId
        last_trade_id = $lastTradeId
        warnings = $warnings
    } | ConvertTo-Json -Compress -Depth 8
    Add-Content -LiteralPath $HistoryPath -Value $event -Encoding UTF8
}

$state | ConvertTo-Json -Depth 20
