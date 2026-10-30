$ErrorActionPreference = 'Stop'
$taskName = 'CompanionCore_Runtime'
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\config\runtime_root.local.json'))) { throw 'Configure the resolved private runtime path before registering startup.' }
$taskPython = 'F:\chatbot-runtime\AstrBot\.venv\Scripts\python.exe'
$taskLegacyPython = 'F:\chatbot-runtime\AstrBot\.venv\Scripts\pythonw.exe'
$taskWatchdog = Join-Path $PSScriptRoot 'runtime_watchdog.py'
$taskWrapper = Join-Path $PSScriptRoot 'supervise_runtime.ps1'
$taskPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskArguments = "-NoProfile -WindowStyle Hidden -File `"$taskWrapper`""
if (-not (Test-Path -LiteralPath $taskPython) -or -not (Test-Path -LiteralPath $taskWatchdog)) { throw 'The tested runtime and watchdog must exist before registration.' }
$taskOld = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$taskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($taskOld) {
    try {
        $taskOldUserSid = if ($taskOld.Principal.UserId -match '^S-1-') { $taskOld.Principal.UserId } else { (New-Object Security.Principal.NTAccount($taskOld.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value }
    } catch { throw 'The previous task owner could not be resolved; no task was replaced.' }
    if (@($taskOld.Actions).Count -ne 1 -or $taskOldUserSid -ne $taskUserSid) { throw 'The task does not belong to this user and single runtime action; no task was replaced.' }
}
$taskOldFormat = $taskOld -and $taskOld.Actions.Execute -eq $taskLegacyPython -and $taskOld.Actions.Arguments -eq "`"$taskWatchdog`""
$taskNewFormat = $taskOld -and $taskOld.Actions.Execute -eq $taskPowerShell -and $taskOld.Actions.Arguments -eq $taskArguments
if ($taskOld -and -not ($taskOldFormat -or $taskNewFormat)) { throw 'A different task already uses the chosen name; no task was replaced.' }
$taskAction = New-ScheduledTaskAction -Execute $taskPowerShell -Argument $taskArguments -WorkingDirectory $PSScriptRoot
$taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
# A logon launch can fail while F: is absent. Retry after it returns without
# changing storage, power or network settings. Interactive principal stays in
# force; IgnoreNew and the watchdog file lock prevent parallel recovery loops.
$taskRetryTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
if ($taskOld) {
    $taskRuntimeRoot = (Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\config\runtime_root.local.json') -Raw -Encoding UTF8 | ConvertFrom-Json).runtime_root
    $taskBackup = Join-Path $taskRuntimeRoot ('data\plugin_data\companion_core\startup-task-before-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff') + '.local.xml')
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath $taskBackup -Encoding Unicode
}
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger @($taskTrigger, $taskRetryTrigger) -Principal $taskPrincipal -Settings $taskSettings -Description 'Owner-only Companion Core runtime recovery; retry every five minutes after storage returns; respects STOP and sends no test messages.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output 'Current-user hidden logon and five-minute recovery task registered and started. Computer sleep and network settings were not changed.'
