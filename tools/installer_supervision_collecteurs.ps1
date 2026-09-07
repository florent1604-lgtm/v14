# Installe uniquement une verification/reprise du collecteur PUBLIC. Aucun MT5.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$interpreter = Join-Path $repo '.venv\Scripts\pythonw.exe'
$script = Join-Path $repo 'tools\superviser_collecteurs.py'
if (-not (Test-Path -LiteralPath $interpreter)) { throw 'pythonw du venv absent' }
$taskName = 'V14-Collecteurs-Publics'
$arguments = '-X utf8 "' + $script + '" --apply-public --watch'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    if (@($existing.Actions).Count -ne 1 -or
        $existing.Actions[0].Execute -ne $interpreter -or
        $existing.Actions[0].Arguments -ne $arguments -or
        $existing.Actions[0].WorkingDirectory -ne $repo) {
        throw 'Tache homonyme differente : conservation, intervention humaine requise'
    }
    Write-Output 'Tache deja presente et compatible; aucune modification.'
    return
}
$action = New-ScheduledTaskAction -Execute $interpreter -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -Hidden `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description 'V14: surveillance publique 1 min; aucun moteur ni ordre MT5.' | Out-Null
Write-Output 'Supervision publique installee (session utilisateur ouverte, toutes les minutes).'
