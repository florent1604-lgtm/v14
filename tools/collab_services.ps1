param(
    [ValidateSet("start", "status", "stop")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
$V14Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$V12Root = if ($env:TITANIUM_V12_ROOT) {
    [IO.Path]::GetFullPath($env:TITANIUM_V12_ROOT)
} else {
    [IO.Path]::GetFullPath((Join-Path (Split-Path $V14Root -Parent) "v12"))
}
$HermesRoot = if ($env:HERMES_AGENT_ROOT) {
    [IO.Path]::GetFullPath($env:HERMES_AGENT_ROOT)
} else {
    [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "hermes\hermes-agent"))
}
$Runtime = Join-Path $V14Root "data\runtime\collab"

$Services = @(
    [pscustomobject]@{
        Name = "hermes-mcp"
        Port = 8766
        Python = Join-Path $HermesRoot "venv\Scripts\python.exe"
        Script = Join-Path $V12Root "tools\hermes_mcp_http.py"
        WorkingDirectory = $V12Root
    },
    [pscustomobject]@{
        Name = "collab-hub"
        Port = 8770
        Python = Join-Path $V12Root "venv\Scripts\python.exe"
        Script = Join-Path $V12Root "tools\collab_hub_server.py"
        WorkingDirectory = $V12Root
    }
)

function Get-ListenerProcessId([int]$Port) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $listener) { return [int]$listener.OwningProcess }
    return $null
}

function Get-PidPath($Service) {
    Join-Path $Runtime ($Service.Name + ".json")
}

function Assert-ServiceFiles($Service) {
    if (-not (Test-Path -LiteralPath $Service.Python -PathType Leaf)) {
        throw "Python absent pour $($Service.Name): $($Service.Python)"
    }
    if (-not (Test-Path -LiteralPath $Service.Script -PathType Leaf)) {
        throw "Service V12 absent pour $($Service.Name): $($Service.Script)"
    }
}

if ($Action -eq "start") {
    New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
    foreach ($service in $Services) {
        Assert-ServiceFiles $service
        $listenerProcessId = Get-ListenerProcessId $service.Port
        if ($null -ne $listenerProcessId) {
            Write-Output "$($service.Name): deja actif pid=$listenerProcessId port=$($service.Port)"
            continue
        }
        $stdout = Join-Path $Runtime ($service.Name + ".stdout.log")
        $stderr = Join-Path $Runtime ($service.Name + ".stderr.log")
        $process = Start-Process -FilePath $service.Python -ArgumentList @($service.Script) `
            -WorkingDirectory $service.WorkingDirectory -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        [pscustomobject]@{
            name = $service.Name
            pid = $process.Id
            port = $service.Port
            python = $service.Python
            script = $service.Script
            started_at = [DateTime]::UtcNow.ToString("o")
        } | ConvertTo-Json | Set-Content -LiteralPath (Get-PidPath $service) -Encoding UTF8
        Write-Output "$($service.Name): lance pid=$($process.Id) port=$($service.Port)"
    }
    exit 0
}

if ($Action -eq "stop") {
    $runtimeResolved = [IO.Path]::GetFullPath($Runtime)
    foreach ($service in $Services) {
        $pidPath = [IO.Path]::GetFullPath((Get-PidPath $service))
        if (-not $pidPath.StartsWith($runtimeResolved, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Chemin PID hors du dossier de collaboration: $pidPath"
        }
        if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
            Write-Output "$($service.Name): aucun processus gere"
            continue
        }
        $record = Get-Content -LiteralPath $pidPath -Encoding UTF8 | ConvertFrom-Json
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            $expected = [IO.Path]::GetFullPath($service.Script)
            if ([string]$process.CommandLine -notlike "*$expected*") {
                throw "Refus d'arret pour $($service.Name): processus inattendu"
            }
            Stop-Process -Id $record.pid -Force
            Write-Output "$($service.Name): arrete pid=$($record.pid)"
        }
        Remove-Item -LiteralPath $pidPath -Force
    }
    exit 0
}

foreach ($service in $Services) {
    $listenerProcessId = Get-ListenerProcessId $service.Port
    if ($null -eq $listenerProcessId) {
        Write-Output "$($service.Name): ARRETE port=$($service.Port)"
    } else {
        Write-Output "$($service.Name): ACTIF pid=$listenerProcessId port=$($service.Port)"
    }
}
