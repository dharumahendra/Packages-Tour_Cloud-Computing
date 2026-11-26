# Demo masalah MONOLIT (Gambar 3) - jalan di laptop, tanpa AWS.
# Pakai (dari root repo):  powershell -ExecutionPolicy Bypass -File scripts\monolith-demo.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $root ".data\monolith"
if (Test-Path $data) { Remove-Item -Recurse -Force $data }

$env:PACKAGES_DIR = $data
$env:PACKAGES_BUCKET = ""
$env:PORT = "8080"
$server = Start-Process python -ArgumentList "`"$root\monolith\src\app.py`"" -PassThru -WindowStyle Hidden
try {
    $M = "http://localhost:8080"
    for ($i = 0; $i -lt 20; $i++) {
        try { Invoke-RestMethod "$M/health" | Out-Null; break } catch { Start-Sleep -Milliseconds 300 }
    }

    $body = '{"name":"Tur Kawah Ijen","destination":"Banyuwangi","durationDays":1,"price":450000,"costPrice":300000,"quota":15,"operatorNotes":"Jemput jam 00.30"}'
    $pkg = Invoke-RestMethod -Method Post "$M/packages" -ContentType "application/json" -Body $body
    $id = $pkg.packageId

    Write-Host "== 1. Data internal bocor ke publik (costPrice, operatorNotes)" -ForegroundColor Yellow
    curl.exe -s "$M/packages"; Write-Host ""

    Write-Host "== 2. Sisa kuota 999 diterima walau kuota 15" -ForegroundColor Yellow
    curl.exe -s -X PUT "$M/packages/$id" -H "Content-Type: application/json" -d '{\"remainingQuota\":999}'; Write-Host ""

    Write-Host "== 3. Hapus tanpa izin apa pun (HTTP status)" -ForegroundColor Yellow
    curl.exe -s -o NUL -w "%{http_code}`n" -X DELETE "$M/packages/$id"
}
finally {
    Stop-Process -Id $server.Id -Force
}
