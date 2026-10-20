$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime.ps1')

Set-Location -LiteralPath $script:ProjectRoot
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
New-Item -ItemType Directory -Force -Path $script:RuntimeDir | Out-Null

$pythonPath = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '未找到 .venv\Scripts\python.exe。请先按 README 安装 Python 依赖。'
}
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    throw '未找到 node.exe。请安装 Node.js 22/24 并确认它在 PATH 中。'
}
$apiDir = Join-Path $script:ProjectRoot 'apps\api'
$env:PYTHONPATH = $apiDir
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'

Write-Host '开始检查本地 PostgreSQL…'
& $pythonPath (Join-Path $PSScriptRoot 'init-local.py')
if ($LASTEXITCODE -ne 0) {
    throw '数据库启动失败。上方或 .runtime\postgres.log 中有具体原因；未删除数据库。'
}

Write-Host '应用数据库迁移…'
& $pythonPath -m alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    throw '数据库迁移失败。已停止启动；未修改或删除现有数据。'
}

$definitions = @(
    @{
        Name = 'api'; File = $pythonPath
        Arguments = ('-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --app-dir "' + $apiDir + '"')
        Described = '后端 API'
    },
    @{
        Name = 'worker'; File = $pythonPath
        Arguments = ('"' + (Join-Path $apiDir 'app\jobs\worker.py') + '"')
        Described = '任务处理进程'
    },
    @{
        Name = 'web'; File = $nodeCommand.Source
        Arguments = ('"' + (Join-Path $script:ProjectRoot 'apps\web\node_modules\vite\bin\vite.js') +
            '" --config "' + (Join-Path $script:ProjectRoot 'apps\web\vite.config.ts') +
            '" "' + (Join-Path $script:ProjectRoot 'apps\web') +
            '" --host 127.0.0.1 --port 5173 --strictPort')
        Described = '前端开发服务器'
    }
)

foreach ($definition in $definitions) {
    $name = $definition.Name
    $spec = $script:ServiceSpecs[$name]
    $owned = Get-OwnedService $name
    if ($owned) {
        if (Test-ServiceReady $name) {
            Write-Host ($definition.Described + ' 已在运行 (PID ' + $owned.Record.pid + ')，复用现有进程。')
            continue
        }
        Write-Host ($definition.Described + ' 的旧进程 (PID ' + $owned.Record.pid + ') 未就绪，停止后重新启动。')
        Stop-OwnedService $name | Out-Null
    } else {
        Remove-ServiceRecord $name
    }

    $portOwner = Get-PortOwner $spec.Port
    if ($portOwner) {
        throw ('端口 ' + $spec.Port + ' 已被 PID ' + $portOwner.ProcessId + '（' + $portOwner.Name +
            '）占用。该进程不属于本项目，脚本不会停止它。请关闭该程序或修改端口后重试。')
    }

    $stdout = Join-Path $script:RuntimeDir $spec.Log
    $stderr = Join-Path $script:RuntimeDir $spec.ErrorLog
    $process = Start-Process -FilePath $definition.File -ArgumentList $definition.Arguments `
        -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Write-ServiceRecord $name $process
    Start-Sleep -Milliseconds 700
    if ($process.HasExited) {
        Remove-ServiceRecord $name
        throw ($definition.Described + ' 启动后立即退出（退出码 ' + $process.ExitCode + '）。请查看 .runtime\' + $spec.ErrorLog + '。')
    }
    Write-Host ($definition.Described + ' 已启动 (PID ' + $process.Id + ')。')
}

if (-not (Wait-ServicesReady 30)) {
    Show-StartupLogs
    throw '服务进程已创建，但 30 秒内未全部就绪。未显示为启动成功；请查看上方日志。'
}

Write-Host 'FishingNet 服务已就绪：http://127.0.0.1:5173'
Write-Host '首次初始化口令在本地 .env 的 SETUP_TOKEN 中（不要复制到聊天或日志）。'
exit 0
