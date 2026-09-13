$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AgentForge = Join-Path $ProjectRoot ".venv\Scripts\agentforge.exe"
$ArtifactRoot = Join-Path $ProjectRoot "artifacts"

if (-not (Test-Path -LiteralPath $AgentForge)) {
    throw "Project virtual environment is missing. Create .venv before running the demo."
}

& $AgentForge credentials-check
& $AgentForge sandbox-health --backend all
& $AgentForge benchmark-suite --history (Join-Path $ArtifactRoot "demo-suite-history.jsonl")
& $AgentForge benchmark-suite-report --history (Join-Path $ArtifactRoot "demo-suite-history.jsonl")

Write-Host "AgentForge personal demo completed. No live model or cloud sandbox was used."
