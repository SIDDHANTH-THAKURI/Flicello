# The daily improvement routine (see docs/improvement.md). Safe to run by hand: `powershell -File scripts\improve.ps1`.
# It only starts Claude when there is something new to learn from, so a quiet day costs nothing.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$py = Join-Path $env:LOCALAPPDATA "VidoSuper\venv\Scripts\python.exe"
$out = & $py -m vidosuper studio digest --quiet 2>&1 | Out-String
$improveDir = Join-Path $env:LOCALAPPDATA "VidoSuper\studio\improve"
$digest = Get-ChildItem $improveDir -Filter "digest-*.md" | Sort-Object LastWriteTime | Select-Object -Last 1
$text = Get-Content $digest.FullName -Raw
$news = ($text -match "## Each rating") -or ($text -match "## Things that broke") -or ($text -match "## Samples they turned down")
if (-not $news) { Write-Output "Nothing new: no ratings, no failures. Not starting Claude."; exit 0 }
$day = Get-Date -Format "yyyy-MM-dd"
git checkout -B "improve/$day" | Out-Null
$prompt = Get-Content (Join-Path $repo "docs\improve-prompt.md") -Raw
claude -p $prompt --permission-mode acceptEdits --max-budget-usd 5 --allowedTools "Read" "Edit" "Write" "Glob" "Grep" "Bash"
git checkout main | Out-Null
