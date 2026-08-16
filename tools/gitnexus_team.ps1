[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("sync", "status", "serve-start", "serve-stop", "cli")]
    [string]$Action = "sync",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$GitNexusArgs,
    [switch]$Force,
    [int]$LockTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$StatePath = Join-Path $Root ".gitnexus\team-sync-state.json"
$PidPath = Join-Path $Root ".gitnexus\team-mcp.pid"
$OutLog = Join-Path $Root ".gitnexus\team-mcp.out.log"
$ErrLog = Join-Path $Root ".gitnexus\team-mcp.err.log"
$Port = 4750
$RepoName = "titanium-v14"
$OpenSslBin = Join-Path $Root ".gitnexus\runtime\openssl-3.5.7\x64\bin"
if (Test-Path -LiteralPath (Join-Path $OpenSslBin "libcrypto-3-x64.dll")) {
    $env:PATH = "$OpenSslBin;$env:PATH"
}

function Get-GitNexusCliPath {
    $npmRoot = (& npm root -g).Trim()
    $candidate = Join-Path $npmRoot "gitnexus\dist\cli\index.js"
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw "GitNexus CLI introuvable: $candidate"
    }
    return $candidate
}

function Get-TeamMcpProcess {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $connection) { return $null }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    [pscustomobject]@{
        ProcessId = [int]$process.ProcessId
        CommandLine = [string]$process.CommandLine
        IsGitNexus = ([string]$process.CommandLine -match "gitnexus.*mcp.*--http" -and
            [string]$process.CommandLine -match "--port\s+4750")
    }
}

function Start-TeamMcp {
    $existing = Get-TeamMcpProcess
    if ($existing) {
        if (-not $existing.IsGitNexus) {
            throw "Le port $Port est occupe par un processus inconnu (PID $($existing.ProcessId))."
        }
        Write-Output "GitNexus MCP deja actif: http://127.0.0.1:$Port/mcp (PID $($existing.ProcessId))."
        return
    }

    $node = (Get-Command node.exe -ErrorAction Stop).Source
    $cli = Get-GitNexusCliPath

    # Le serveur est lance par Win32_Process.Create, et non par Start-Process.
    #
    # Pourquoi : Start-Process cree l'enfant avec heritage de handles. Le serveur
    # MCP heritait donc du tube de sortie de L'APPELANT de ce script, et comme il
    # vit indefiniment, ce tube ne se fermait jamais. Le script se terminait
    # normalement, mais quiconque l'appelait en capturant sa sortie restait
    # bloque pour toujours. Le 16/08/2026, Prime Agent a passe 2 h fige sur
    # `gitnexus_team.ps1 sync` pour cette raison : le serveur allait bien, c'est
    # l'appel qui ne revenait pas. Tuer l'intermediaire ne suffit meme pas --
    # le petit-fils tient encore le tube.
    #
    # Win32_Process.Create n'herite d'aucun handle. La redirection est confiee a
    # cmd, qui ouvre lui-meme les journaux. Verifie : l'appelant recupere sa
    # sortie en moins d'une seconde.
    $lignes = 'cmd.exe /c ""{0}" "{1}" mcp --http --host 127.0.0.1 --port {2} > "{3}" 2> "{4}""' `
        -f $node, $cli, $Port, $OutLog, $ErrLog
    $creation = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
        -Arguments @{ CommandLine = $lignes; CurrentDirectory = $Root }
    if ($creation.ReturnValue -ne 0) {
        throw "Lancement du MCP GitNexus refuse par Win32_Process.Create (code $($creation.ReturnValue))."
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 250
        $listener = Get-TeamMcpProcess
        if ($listener -and $listener.IsGitNexus) {
            # Le PID retenu est celui qui ECOUTE, pas celui du lanceur : cmd rend
            # la main aussitot, et enregistrer son PID donnerait un fichier de
            # service pointant sur un processus mort.
            [IO.File]::WriteAllText($PidPath, [string]$listener.ProcessId, [Text.Encoding]::ASCII)
            Write-Output "GitNexus MCP actif: http://127.0.0.1:$Port/mcp (PID $($listener.ProcessId))."
            return
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    $details = if (Test-Path -LiteralPath $ErrLog) { Get-Content -LiteralPath $ErrLog -Raw } else { "" }
    throw "GitNexus MCP n'ecoute pas sur le port $Port apres 20 secondes. $details"
}

function Stop-TeamMcp {
    $existing = Get-TeamMcpProcess
    if (-not $existing) {
        Write-Output "GitNexus MCP deja arrete."
        return $false
    }
    if (-not $existing.IsGitNexus) {
        throw "Refus d'arreter le PID $($existing.ProcessId): le port $Port n'appartient pas au MCP GitNexus attendu."
    }
    Stop-Process -Id $existing.ProcessId
    Wait-Process -Id $existing.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
    Write-Output "GitNexus MCP arrete proprement (PID $($existing.ProcessId))."
    return $true
}

function Test-RelevantPath([string]$Path) {
    $normalized = $Path.Replace("\", "/")
    $extension = [IO.Path]::GetExtension($normalized).ToLowerInvariant()
    $sourceExtensions = @(
        ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
        ".java", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cs",
        ".go", ".rs", ".php", ".kt", ".kts", ".swift", ".rb", ".dart"
    )
    if ($sourceExtensions -contains $extension) { return $true }
    return $normalized -in @(
        "pyproject.toml", "requirements.txt", ".gitnexusrc", ".gitnexusignore"
    )
}

function Get-WorkingTreeFingerprint {
    Push-Location $Root
    try {
        $head = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0) { throw "Impossible de lire HEAD." }
        $paths = @(& git ls-files -co --exclude-standard) |
            Where-Object { $_ -and (Test-RelevantPath $_) } |
            Sort-Object -Unique
        $lines = [Collections.Generic.List[string]]::new()
        $lines.Add("HEAD $head")
        foreach ($relative in $paths) {
            $absolute = Join-Path $Root $relative
            if (Test-Path -LiteralPath $absolute -PathType Leaf) {
                try {
                    $hash = (Get-FileHash -LiteralPath $absolute -Algorithm SHA256).Hash
                    $lines.Add("$relative $hash")
                } catch {
                    $lines.Add("$relative UNREADABLE")
                }
            } else {
                $lines.Add("$relative MISSING")
            }
        }
        $payload = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($sha.ComputeHash($payload))).Replace("-", "")
        } finally {
            $sha.Dispose()
        }
    } finally {
        Pop-Location
    }
}

function Read-SyncState {
    if (-not (Test-Path -LiteralPath $StatePath)) { return $null }
    try { return Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json }
    catch { return $null }
}

function Write-SyncState([string]$Fingerprint) {
    $state = [ordered]@{
        repo = $RepoName
        fingerprint = $Fingerprint
        synced_at_utc = [DateTime]::UtcNow.ToString("o")
        index_meta = ".gitnexus/gitnexus.json"
    }
    [IO.File]::WriteAllText(
        $StatePath,
        ($state | ConvertTo-Json -Depth 4),
        [Text.UTF8Encoding]::new($false)
    )
}

function Sync-TeamIndex {
    $created = $false
    $mutex = [Threading.Mutex]::new($false, "Local\TitaniumV14GitNexusSync", [ref]$created)
    $locked = $false
    try {
        try { $locked = $mutex.WaitOne([TimeSpan]::FromSeconds($LockTimeoutSeconds)) }
        catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { throw "Timeout: un autre agent synchronise GitNexus depuis plus de $LockTimeoutSeconds secondes." }

        $before = Get-WorkingTreeFingerprint
        $state = Read-SyncState
        if (-not $Force -and $state -and $state.fingerprint -eq $before) {
            Write-Output "GitNexus deja synchronise pour l'etat de travail courant ($before)."
            return
        }

        $mcpWasRunning = [bool](Get-TeamMcpProcess)
        if ($mcpWasRunning) { [void](Stop-TeamMcp) }
        try {
            Push-Location $Root
            try {
                & node ".gitnexus\run.cjs" analyze
                if ($LASTEXITCODE -ne 0) { throw "gitnexus analyze a echoue avec le code $LASTEXITCODE." }
            } finally {
                Pop-Location
            }

            $after = Get-WorkingTreeFingerprint
            if ($after -ne $before) {
                Write-Output "Des fichiers ont change pendant l'analyse; seconde passe de stabilisation."
                Push-Location $Root
                try {
                    & node ".gitnexus\run.cjs" analyze
                    if ($LASTEXITCODE -ne 0) { throw "Seconde analyse GitNexus en echec ($LASTEXITCODE)." }
                } finally {
                    Pop-Location
                }
                $after = Get-WorkingTreeFingerprint
            }
            Write-SyncState $after
            Write-Output "GitNexus synchronise: empreinte $after."
        } finally {
            if ($mcpWasRunning) { Start-TeamMcp }
        }
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

switch ($Action) {
    "sync" { Sync-TeamIndex }
    "status" {
        $fingerprint = Get-WorkingTreeFingerprint
        $state = Read-SyncState
        $mcp = Get-TeamMcpProcess
        [pscustomobject]@{
            repo = $RepoName
            fingerprint = $fingerprint
            synced = [bool]($state -and $state.fingerprint -eq $fingerprint)
            last_sync_utc = if ($state) { $state.synced_at_utc } else { $null }
            mcp_url = "http://127.0.0.1:$Port/mcp"
            mcp_running = [bool]($mcp -and $mcp.IsGitNexus)
            mcp_pid = if ($mcp) { $mcp.ProcessId } else { $null }
        } | ConvertTo-Json -Depth 4
        & node (Join-Path $Root ".gitnexus\run.cjs") status
    }
    "serve-start" { Start-TeamMcp }
    "serve-stop" { [void](Stop-TeamMcp) }
    "cli" {
        if (-not $GitNexusArgs -or $GitNexusArgs.Count -eq 0) {
            throw "Action cli: fournir une commande GitNexus (query, context, impact, detect-changes...)."
        }
        Push-Location $Root
        try {
            & node (Get-GitNexusCliPath) @GitNexusArgs
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } finally {
            Pop-Location
        }
    }
}
