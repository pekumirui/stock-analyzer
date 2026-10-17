param(
    [Parameter(Position = 0)][string]$cmd = "serve",
    [Parameter(Position = 1)][string]$arg = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Host "先に .\setup.ps1 を実行してください" -ForegroundColor Red; exit 1 }

if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"'), "Process")
        }
    }
}

switch ($cmd) {
    "migrate" { & $python jobs\migrate.py }

    "golden" { & $python jobs\golden_db.py }

    "check" {
        if ($arg) { & $python tools\market\check_api.py $arg } else { & $python tools\market\check_api.py }
    }

    "serve" {
        $port = if ($arg) { $arg } else { "8000" }
        Write-Host "http://localhost:$port を開いてください" -ForegroundColor Cyan
        & $python -m uvicorn app.main:app --reload --port $port
    }

    "reset" {
        if (-not (Test-Path "stock.db")) { Write-Host "DBはありません。そのまま golden / backfill できます。" -ForegroundColor Green; break }
        $ans = Read-Host "stock.db を削除します。よろしいですか? (y/N)"
        if ($ans -ne "y") { Write-Host "中止しました"; break }
        Remove-Item "stock.db", "stock.db-journal", "stock.db-wal", "stock.db-shm" -ErrorAction SilentlyContinue
        Write-Host "削除しました。次: .\run.ps1 golden（数秒）か .\run.ps1 backfill" -ForegroundColor Green
    }

    "batch" {
        & $python jobs\market\check_demo.py
        if ($LASTEXITCODE -ne 0) { break }
        if ($arg) { & $python jobs\market\run_batch.py --date $arg } else { & $python jobs\market\run_batch.py }
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
        if ($years -lt 1 -or $years -gt 20) {
            Write-Host "年数は1〜20の範囲で指定してください" -ForegroundColor Red
            break
        }
        & $python jobs\market\check_demo.py
        if ($LASTEXITCODE -ne 0) { break }
        Write-Host "過去 $years 年分を取得します" -ForegroundColor Cyan
        & $python jobs\market\run_batch.py --backfill $years
    }

    "lint" {
        & $python -m pip install -r requirements-dev.txt --quiet
        & $python -m ruff check --config config/pyproject.toml app jobs tests tools analyzer deploy mcp
        if ($LASTEXITCODE -ne 0) { break }
        & $python -m bandit -c config/pyproject.toml -r app jobs tests tools analyzer deploy mcp -x app/xbrlp,.cursor,tmp,.venv
    }

    default { Write-Host "使い方: .\run.ps1 [serve|check|reset|migrate|golden|batch|backfill|lint] [引数]" }
}
