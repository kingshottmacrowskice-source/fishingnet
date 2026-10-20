param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'runtime.ps1')
$appUrl = 'http://127.0.0.1:5173'

try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

try {
    Write-Host '正在启动 FishingNet…'
    & (Join-Path $PSScriptRoot 'start.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'start.ps1 返回失败。' }

    $deadline = (Get-Date).AddSeconds(45)
    $ready = $false
    do {
        $apiReady = Test-HttpReady 'http://127.0.0.1:8000/api/v1/health/ready'
        $webReady = Test-HttpReady $appUrl 'FishingNet|root'
        $workerReady = [bool](Get-OwnedService 'worker')
        $dbReady = [bool](Get-NetTCPConnection -State Listen -LocalPort 55432 -ErrorAction SilentlyContinue)
        if ($apiReady -and $webReady -and $workerReady -and $dbReady) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    if (-not $ready) {
        Write-Host ('启动未完成：API=' + $apiReady + ' Web=' + $webReady + ' Worker=' + $workerReady + ' 数据库=' + $dbReady)
        Show-StartupLogs
        throw '服务未在时限内全部就绪，未显示为可访问状态。'
    }

    Write-Host "FishingNet 已就绪：$appUrl"
    Write-Host '停止服务：双击 停止FishingNet.cmd，或运行 infra\stop.ps1。'
    if (-not $NoBrowser) {
        Start-Process -FilePath $appUrl -WindowStyle Hidden
    }
    exit 0
} catch {
    Write-Host ('启动失败：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
