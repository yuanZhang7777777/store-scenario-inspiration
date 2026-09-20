param([string]$RuntimeRoot = 'E:\Project\store-assortment-copilot\var\services\qdrant')
$ErrorActionPreference = 'Stop'
$qdrantExe = Join-Path $RuntimeRoot 'v1.19.1\qdrant.exe'
$qdrantConfig = Join-Path $RuntimeRoot 'local.yaml'
if (!(Test-Path -LiteralPath $qdrantExe) -or !(Test-Path -LiteralPath $qdrantConfig)) {
    throw "Qdrant executable/config missing in $RuntimeRoot"
}
$existingListener = Get-NetTCPConnection -State Listen -LocalPort 6333 -ErrorAction SilentlyContinue
if ($existingListener) {
    $owner = Get-Process -Id $existingListener[0].OwningProcess
    if ($owner.Path -ne $qdrantExe) { throw 'Port 6333 belongs to another program' }
    Invoke-RestMethod 'http://127.0.0.1:6333/'
    exit
}
$qdrantProcess = Start-Process -FilePath $qdrantExe -WorkingDirectory $RuntimeRoot `
    -ArgumentList @('--disable-telemetry', '--config-path', ('"' + $qdrantConfig + '"')) `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $RuntimeRoot 'stdout.log') `
    -RedirectStandardError (Join-Path $RuntimeRoot 'stderr.log')
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if ($qdrantProcess.HasExited) { throw "Qdrant exited; see $RuntimeRoot\stderr.log" }
    try {
        $qdrantInfo = Invoke-RestMethod 'http://127.0.0.1:6333/' -TimeoutSec 2
        $qdrantInfo
        Write-Output "PID=$($qdrantProcess.Id); local-only; telemetry disabled"
        exit
    } catch { Start-Sleep -Milliseconds 500 }
}
throw "Qdrant did not become ready; see $RuntimeRoot\stdout.log"
