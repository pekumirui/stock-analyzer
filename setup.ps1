$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = $null
foreach ($cmd in @("py", "python", "python3")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $v = & $cmd --version 2>&1
        if ($v -match "Python 3\.(\d+)") { $py = $cmd; break }
    }
}
if (-not $py) {
    Write-Host "Python が見つかりません。https://www.python.org/downloads/ から入手し、" -ForegroundColor Red
    Write-Host "インストール時に 'Add python.exe to PATH' に必ずチェックを入れてください。" -ForegroundColor Red
    Write-Host "または: winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
Write-Host "使用する Python: $py ($(& $py --version 2>&1))" -ForegroundColor Green

if (-not (Test-Path ".venv")) {
    Write-Host "仮想環境を作成中 (.venv)..."
    & $py -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
Write-Host "依存パッケージをインストール中..."
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet

if ((Test-Path "stock.db") -and ((Get-Item "stock.db").Length -eq 0)) {
    Remove-Item "stock.db", "stock.db-journal" -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "セットアップ完了。次:" -ForegroundColor Green
Write-Host "  Copy-Item .env.example .env   # JQUANTS_API_KEY を記入" -ForegroundColor Cyan
Write-Host "  .\run.ps1 backfill            # 初回データ取得" -ForegroundColor Cyan
Write-Host "  .\run.ps1 serve               # サーバー起動 → http://localhost:8000" -ForegroundColor Cyan
