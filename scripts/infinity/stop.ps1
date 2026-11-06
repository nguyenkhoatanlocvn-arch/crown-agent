# CROWN INFINITY — dừng máy chủ đang chạy nền (ưu tiên Ctrl+C trong cửa sổ máy chủ).
# Trạng thái kiểm thử: CHƯA chạy thử trên Windows (NOT VERIFIED).
param([string]$DataDir = "$env:LOCALAPPDATA\CrownInfinity")
$lock = Join-Path $DataDir 'server.lock'
if (-not (Test-Path $lock)) { Write-Host "Không thấy máy chủ đang chạy ($lock)."; exit 0 }
$info = Get-Content $lock -Raw | ConvertFrom-Json
$proc = Get-Process -Id $info.pid -ErrorAction SilentlyContinue
if ($proc) {
  Stop-Process -Id $info.pid
  Write-Host "Đã dừng tiến trình $($info.pid). SQLite (WAL) giữ toàn vẹn dữ liệu đã ghi."
} else {
  Write-Host "Tiến trình $($info.pid) không còn chạy."
}
Remove-Item $lock -ErrorAction SilentlyContinue
