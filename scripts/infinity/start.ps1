# CROWN INFINITY — khởi động trên Windows 10/11 (PowerShell 5.1 hoặc 7).
# Dùng: powershell -ExecutionPolicy Bypass -File scripts\infinity\start.ps1 [-Port 8765] [-DataDir <thư mục>] [-NoBrowser]
# Trạng thái kiểm thử: CHƯA chạy thử trên Windows (NOT VERIFIED). Lệnh Python bên dưới đã kiểm trên Linux.
param(
  [int]$Port = 8765,
  [string]$DataDir = "$env:LOCALAPPDATA\CrownInfinity",
  [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Repo

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  Write-Host "Chưa có lệnh 'py'. Cài Python 3.10+ từ https://www.python.org/downloads/windows/ rồi chạy lại." -ForegroundColor Red
  exit 1
}
py -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { Write-Host "Cần Python 3.10 trở lên." -ForegroundColor Red; exit 1 }

$status = (py -m crown_infinity --data-dir "$DataDir" status) | Out-String | ConvertFrom-Json
$hasUser = $status.db_exists -and $status.counts -and ($status.counts.users -gt 0)
if (-not $hasUser) {
  Write-Host "Chưa có tài khoản OWNER. Tạo ngay (mật khẩu ≥ 10 ký tự, không hiện trên màn hình):"
  $u = Read-Host "Tên đăng nhập OWNER"
  py -m crown_infinity --data-dir "$DataDir" init-owner --username $u
  if ($LASTEXITCODE -ne 0) { exit 1 }
}

if (-not $NoBrowser) {
  Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile -Command Start-Sleep -Seconds 2; Start-Process 'http://localhost:$Port/'"
}
Write-Host "Dừng máy chủ: bấm Ctrl+C trong cửa sổ này." -ForegroundColor Yellow
py -m crown_infinity --data-dir "$DataDir" serve --port $Port
