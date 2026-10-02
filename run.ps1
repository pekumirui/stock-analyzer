# 実行ヘルパー
#   .\run.ps1 serve          開発サーバー起動 (http://localhost:8000)
#   .\run.ps1 serve 8080     ポート指定
#   .\run.ps1 check          J-Quants疎通・項目マッピング確認 (要 .env)
#   .\run.ps1 check 2026-08-14   日付を指定して確認
#   .\run.ps1 reset          DBを完全削除
#   .\run.ps1 migrate        スキーマ変更後、既存DBに不足列を追加
#   .\run.ps1 batch          日次バッチ (要 .env)
#   .\run.ps1 batch 2026-08-14   特定日を取り込み
#   .\run.ps1 backfill       初回バックフィル (既定5年分, 要 .env)
#   .\run.ps1 backfill 2     過去2年分
#   .\run.ps1 lint           Ruff + Bandit
param(
    [Parameter(Position = 0)][string]$cmd = "serve",
    [Parameter(Position = 1)][string]$arg = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Host "先に .\setup.ps1 を実行してください" -ForegroundColor Red; exit 1 }

# .env があれば環境変数に読み込む
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"'), "Process")
        }
    }
}

switch ($cmd) {
    "migrate" { & $python jobs\migrate.py }

    "check" {
        if ($arg) { & $python tools\check_api.py $arg } else { & $python tools\check_api.py }
    }

    "serve" {
        $port = if ($arg) { $arg } else { "8000" }
        Write-Host "http://localhost:$port を開いてください" -ForegroundColor Cyan
        & $python -m uvicorn app.main:app --reload --port $port
    }

    "reset" {
        if (-not (Test-Path "stock.db")) { Write-Host "DBはありません。そのまま backfill できます。" -ForegroundColor Green; break }
        $ans = Read-Host "stock.db を削除します。よろしいですか? (y/N)"
        if ($ans -ne "y") { Write-Host "中止しました"; break }
        Remove-Item "stock.db", "stock.db-journal", "stock.db-wal", "stock.db-shm" -ErrorAction SilentlyContinue
        Write-Host "削除しました。次: .\run.ps1 backfill" -ForegroundColor Green
    }

    "batch" {
        & $python jobs\check_demo.py
        if ($LASTEXITCODE -ne 0) { break }
        if ($arg) { & $python jobs\run_batch.py --date $arg } else { & $python jobs\run_batch.py }
    }

    "backfill" {
        $years = 5
        if ($arg) {
            if ($arg -notmatch '^\d+$') {
                Write-Host "年数は整数で指定してください (例: .\run.ps1 backfill 2)" -ForegroundColor Red
                break
            }
            $years = [int]$arg
        }
        # Lightプランは5年、Standardは10年、Premiumは20年が上限
        if ($years -lt 1 -or $years -gt 20) {
            Write-Host "年数は1〜20の範囲で指定してください" -ForegroundColor Red
            break
        }
        & $python jobs\check_demo.py
        if ($LASTEXITCODE -ne 0) { break }
        Write-Host "過去 $years 年分を取得します" -ForegroundColor Cyan
        & $python jobs\run_batch.py --backfill $years
    }

    "lint" {
        & $python -m pip install -r requirements-dev.txt --quiet
        & $python -m ruff check --config config/pyproject.toml app jobs tests tools analyzer deploy
        if ($LASTEXITCODE -ne 0) { break }
        & $python -m bandit -c config/pyproject.toml -r app jobs tests tools analyzer deploy -x app/xbrlp,.cursor,tmp,.venv
    }

    default { Write-Host "使い方: .\run.ps1 [serve|check|reset|migrate|batch|backfill|lint] [引数]" }
}
