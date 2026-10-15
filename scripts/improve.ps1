# The improvement routine (see docs/improvement.md). The studio starts it after every 5 new ratings (improve_auto in
# studio.yaml); safe to run by hand too: `powershell -File scripts\improve.ps1`.
# It works in its own git worktree on a branch, so the live studio's files are never touched, and it never pushes main.
# It only starts Claude when there is something new to learn from, so a quiet run costs nothing.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $env:LOCALAPPDATA "VidoSuper\venv\Scripts\python.exe"
$improveDir = Join-Path $env:LOCALAPPDATA "VidoSuper\studio\improve"
$lock = Join-Path $improveDir "running.lock"
New-Item -ItemType Directory -Force $improveDir | Out-Null
Set-Content -Path $lock -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
try {
    Set-Location $repo
    & $py -m vidosuper studio digest --quiet | Out-Null
    $digest = Get-ChildItem $improveDir -Filter "digest-*.md" | Sort-Object LastWriteTime | Select-Object -Last 1
    $text = Get-Content $digest.FullName -Raw
    $news = ($text -match "## Each rating") -or ($text -match "## Things that broke") -or ($text -match "## Samples they turned down")
    if (-not $news) { Write-Output "Nothing new: no ratings, no failures. Not starting Claude."; exit 0 }
    $stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
    $branch = "improve/$stamp"
    $work = Join-Path $env:LOCALAPPDATA "VidoSuper\improve-work\$stamp"
    git worktree add -B $branch $work main | Out-Null
    Set-Location $work
    $env:PYTHONPATH = Join-Path $work "src"   # this copy's code, not the live studio's
    $prompt = (Get-Content (Join-Path $work "docs\improve-prompt.md") -Raw) + "`n`nYou are in a git worktree at $work on branch $branch. The live studio runs from ${repo}; never edit files there."
    claude -p $prompt --permission-mode acceptEdits --max-budget-usd 5 --allowedTools "Read" "Edit" "Write" "Glob" "Grep" "Bash"
    # put the branch live if every gate passes (see src/vidosuper/studio/deploy.py); otherwise it waits for the owner
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Set-Location $repo
    & $py -m vidosuper studio deploy $branch --worktree $work
    # keep only the last few worktrees
    Get-ChildItem (Join-Path $env:LOCALAPPDATA "VidoSuper\improve-work") -Directory | Sort-Object CreationTime -Descending |
        Select-Object -Skip 4 | ForEach-Object { git -C $repo worktree remove --force $_.FullName }
}
finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
    Set-Location $repo
}
