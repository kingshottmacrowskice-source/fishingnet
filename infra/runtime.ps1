# Shared helpers for the project-local start/stop/launch scripts.
# PID files are JSON records (pid + start time + marker) so a reused PID is never
# mistaken for one of our services.

$script:ProjectRoot = Split-Path $PSScriptRoot -Parent
$script:RuntimeDir = Join-Path $script:ProjectRoot '.runtime'
$script:ServiceSpecs = @{
    api = @{
        Name = 'api'; Port = 8000; Marker = 'app\.main:app'
        Url = 'http://127.0.0.1:8000/api/v1/health/ready'
        Log = 'api.log'; ErrorLog = 'api.error.log'
    }
    worker = @{
        Name = 'worker'; Port = 0; Marker = 'app\.jobs\.worker|app\\jobs\\worker\.py'
        Log = 'worker.log'; ErrorLog = 'worker.error.log'
    }
    web = @{
        Name = 'web'; Port = 5173; Marker = 'vite'
        Url = 'http://127.0.0.1:5173/'
        Log = 'web.log'; ErrorLog = 'web.error.log'
    }
}

function Get-ServicePidFile([string]$Name) {
    Join-Path $script:RuntimeDir ($Name + '.pid')
}

function Read-ServiceRecord([string]$Name) {
    $file = Get-ServicePidFile $Name
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    $raw = (Get-Content -LiteralPath $file -Raw -ErrorAction SilentlyContinue)
    if (-not $raw) { return $null }
    $trim = $raw.Trim()
    if ($trim -match '^\d+$') {
        # Legacy plain PID file; identity still has to match before it is used.
        return [pscustomobject]@{ pid = [int]$trim; started = $null; marker = $null }
    }
    try {
        $record = $trim | ConvertFrom-Json
        if ($record.pid) { return $record }
    } catch {
        return $null
    }
    return $null
}

function Write-ServiceRecord([string]$Name, $Process) {
    $info = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $Process.Id) -ErrorAction SilentlyContinue
    $started = (Get-Date).ToString('o')
    if ($info) { $started = ([datetime]$info.CreationDate).ToString('o') }
    $record = [ordered]@{
        pid = $Process.Id
        name = $Name
        started = $started
        marker = $script:ServiceSpecs[$Name].Marker
        root = $script:ProjectRoot
    }
    $record | ConvertTo-Json -Compress | Set-Content -LiteralPath (Get-ServicePidFile $Name) -Encoding UTF8
}

function Get-ProcessInfo([int]$ProcessId) {
    if (-not $ProcessId) { return $null }
    return Get-CimInstance Win32_Process -Filter ("ProcessId=" + $ProcessId) -ErrorAction SilentlyContinue
}

function Test-ServiceIdentity([string]$Name, $Info, $Record) {
    if (-not $Info) { return $false }
    $spec = $script:ServiceSpecs[$Name]
    $command = [string]$Info.CommandLine
    if (-not $command) { return $false }
    if (-not [regex]::IsMatch($command, $spec.Marker, [Text.RegularExpressions.RegexOptions]::IgnoreCase)) { return $false }
    if ($command.IndexOf($script:ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    if ($Record -and $Record.started) {
        try {
            $expected = [datetime]::Parse($Record.started)
            $actual = [datetime]$Info.CreationDate
            if ([math]::Abs(($actual - $expected).TotalSeconds) -gt 5) { return $false }
        } catch {
            return $false
        }
    }
    return $true
}

function Get-OwnedService([string]$Name) {
    $record = Read-ServiceRecord $Name
    if (-not $record) { return $null }
    $info = Get-ProcessInfo ([int]$record.pid)
    if (-not $info) { return $null }
    if (-not (Test-ServiceIdentity $Name $info $record)) { return $null }
    return [pscustomobject]@{ Record = $record; Info = $info }
}

function Remove-ServiceRecord([string]$Name) {
    Remove-Item -LiteralPath (Get-ServicePidFile $Name) -Force -ErrorAction SilentlyContinue
}

function Stop-OwnedService([string]$Name) {
    $owned = Get-OwnedService $Name
    if (-not $owned) {
        Remove-ServiceRecord $Name
        return $false
    }
    # The root was verified (command line, project path and start time); a tree kill
    # here cannot reach an unrelated program even if the PID was reused.
    & taskkill.exe /PID ([string]$owned.Record.pid) /T /F *> $null
    for ($i = 0; $i -lt 40; $i++) {
        if (-not (Get-ProcessInfo ([int]$owned.Record.pid))) { break }
        Start-Sleep -Milliseconds 100
    }
    Remove-ServiceRecord $Name
    return $true
}

function Get-PortOwner([int]$Port) {
    if (-not $Port) { return $null }
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return $null }
    return Get-ProcessInfo ([int]$conn.OwningProcess)
}

function Test-HttpReady([string]$Url, [string]$ContentMatch = '') {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -ne 200) { return $false }
        if ($ContentMatch -and ($response.Content -notmatch $ContentMatch)) { return $false }
        return $true
    } catch {
        return $false
    }
}

function Test-ServiceReady([string]$Name) {
    $spec = $script:ServiceSpecs[$Name]
    if ($Name -eq 'worker') {
        return [bool](Get-OwnedService $Name)
    }
    return Test-HttpReady $spec.Url $(if ($Name -eq 'web') { 'FishingNet|root' } else { '' })
}

function Wait-ServicesReady([int]$TimeoutSeconds = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ((Test-ServiceReady 'api') -and (Test-ServiceReady 'web') -and (Test-ServiceReady 'worker')) {
            return $true
        }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

function Get-LogTail([string]$FileName, [int]$Lines = 15) {
    $path = Join-Path $script:RuntimeDir $FileName
    if (-not (Test-Path -LiteralPath $path)) { return '' }
    return (Get-Content -LiteralPath $path -Tail $Lines -ErrorAction SilentlyContinue) -join [Environment]::NewLine
}

function Show-StartupLogs {
    foreach ($name in @('api', 'worker', 'web')) {
        foreach ($file in @($script:ServiceSpecs[$name].ErrorLog, $script:ServiceSpecs[$name].Log)) {
            $tail = Get-LogTail $file 10
            if ($tail) { Write-Host ("--- .runtime\" + $file + " ---"); Write-Host $tail }
        }
    }
    $pg = Get-LogTail 'postgres.log' 10
    if ($pg) { Write-Host '--- .runtime\postgres.log ---'; Write-Host $pg }
}
