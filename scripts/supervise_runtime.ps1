$ErrorActionPreference = 'Stop'
$taskRootConfig = Join-Path $PSScriptRoot '..\config\runtime_root.local.json'
if (-not (Test-Path -LiteralPath $taskRootConfig)) { throw 'A resolved private runtime path must be configured first.' }
$taskRuntimeRoot = (Get-Content -LiteralPath $taskRootConfig -Raw -Encoding UTF8 | ConvertFrom-Json).runtime_root
$taskData = Join-Path $taskRuntimeRoot 'data'
$taskLogRoot = Join-Path $taskData 'companion_logs'
New-Item -ItemType Directory -Path $taskLogRoot -Force | Out-Null
$taskStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff') + '-' + [Guid]::NewGuid().ToString('N')
$taskOut = Join-Path $taskLogRoot "watchdog-stdout-$taskStamp.local.log"
$taskErr = Join-Path $taskLogRoot "watchdog-stderr-$taskStamp.local.log"
$taskSupervisor = Start-Process -FilePath 'F:\chatbot-runtime\AstrBot\.venv\Scripts\python.exe' -ArgumentList @((Join-Path $PSScriptRoot 'runtime_watchdog.py')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $taskOut -RedirectStandardError $taskErr -PassThru
$taskSupervisor.WaitForExit()
exit $taskSupervisor.ExitCode
