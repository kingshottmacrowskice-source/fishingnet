$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime.ps1')

Set-Location -LiteralPath $script:ProjectRoot
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$failed = $false
foreach ($name in @('web', 'api', 'worker')) {
    $owned = Get-OwnedService $name
    if (-not $owned) {
        $record = Read-ServiceRecord $name
        if ($record) {
            Write-Host ($name + ' 的 PID 记录已过期或不属于本项目，仅清理 PID 文件。')
        }
        Remove-ServiceRecord $name
        continue
    }
    if (Stop-OwnedService $name) {
        Write-Host ($name + ' 已停止 (PID ' + $owned.Record.pid + ')。')
    } else {
        Write-Host ($name + ' 停止失败。') -ForegroundColor Red
        $failed = $true
    }
}

$pythonPath = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $pythonPath) {
    & $pythonPath (Join-Path $PSScriptRoot 'stop-local.py')
    if ($LASTEXITCODE -ne 0) {
        Write-Host '本地 PostgreSQL 未能停止；数据库未删除。' -ForegroundColor Red
        $failed = $true
    }
} else {
    Write-Host '.venv 不存在，跳过数据库停止。'
}

foreach ($port in @(8000, 5173, 55432)) {
    $owner = Get-PortOwner $port
    if ($owner) {
        Write-Host ('端口 ' + $port + ' 仍被 PID ' + $owner.ProcessId + '（' + $owner.Name + '）使用。') -ForegroundColor Red
        $failed = $true
    }
}

if ($failed) { exit 1 }
Write-Host 'FishingNet 已停止。'
exit 0
