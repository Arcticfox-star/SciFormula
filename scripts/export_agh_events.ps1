# export_agh_events.ps1
#
# Export AGH event streams (JSONL) for the 7 sessions whose official HTML
# exports are already committed to this repo.
#
# Why this matters: the model name used during development appears ONLY in these
# JSONL originals (session/start -> modelSettings[*].model). The committed HTML
# exports do NOT contain any model field (verified: grep "agnes" hits zero across
# all 7 files). Without the JSONL files, "which model was used" stays a
# second-hand claim by the author instead of something a reviewer can recompute.
#
# Usage, from anywhere:
#     powershell -ExecutionPolicy Bypass -File scripts/export_agh_events.ps1
#
# Safe to re-run: existing files are overwritten with a fresh export.

$ErrorActionPreference = "Continue"

# --- locate repo root (this script lives in <repo>/scripts) -------------------
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repoRoot
Write-Host "repo root: $repoRoot"

# --- locate the agh CLI ------------------------------------------------------
if (-not (Get-Command agh -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "!! 'agh' was not found in PATH."
    Write-Host "   Run this in the terminal where AGH works (it also needs the credential"
    Write-Host "   store, which is unavailable inside sandboxed shells)."
    exit 1
}

# --- output directory --------------------------------------------------------
$outDir = Join-Path $repoRoot "results\agh-event"
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    Write-Host "created: results\agh-event"
}

# --- session id -> the official HTML export already in the repo --------------
$sessions = @(
    @{ id = "161ea3fb-7f17-4a89-9ba7-d6c70d46c6be"; html = "agh-session.html" },
    @{ id = "438d81fd-c1b5-4efc-b739-d238864a3d3a"; html = "agh-session-demo.html" },
    @{ id = "39d31a09-7e75-42d0-934e-911e92cf6e74"; html = "agh-session-feynman.html" },
    @{ id = "6501b3a2-515a-4ce2-bce6-6dc59d6a954a"; html = "agh-session-data.html" },
    @{ id = "9d469c46-1842-49b0-8dc3-21a9f04c6888"; html = "agh-session-data2.html" },
    @{ id = "6ea6a59e-2c25-47f8-96b9-e000383a5a93"; html = "agh-session-sim.html" },
    @{ id = "06df62f6-f32f-41dc-a78b-81c5eb4531be"; html = "agh-session-unitinfer.html" }
)

$ok = 0
$fail = 0
foreach ($s in $sessions) {
    $dest = Join-Path $outDir ($s.id + ".jsonl")
    Write-Host ("export {0}  <-  {1}" -f $s.id, $s.html)
    & agh export $s.id --format agnes -o $dest
    if ($LASTEXITCODE -eq 0 -and (Test-Path $dest)) {
        $size = (Get-Item $dest).Length
        Write-Host ("    ok  ({0} bytes)" -f $size)
        $ok++
    } else {
        Write-Host ("    !! failed: {0} (session may no longer exist on the server)" -f $s.id)
        $fail++
    }
}

Write-Host ""
Write-Host ("exported {0} / {1}   failed {2}" -f $ok, $sessions.Count, $fail)
if ($fail -gt 0) {
    Write-Host "Sessions that failed may have been pruned server-side. Export the rest ASAP -"
    Write-Host "once a session is gone, that model record can never be recovered."
}

Write-Host ""
Write-Host "next steps:"
Write-Host "  python agh_provenance.py"
if ($ok -gt 0) {
    Write-Host "  python scripts/resign_evidence.py --reason `"add AGH event originals: model provenance becomes first-hand`""
}
