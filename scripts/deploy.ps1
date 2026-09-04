param(
    [string]$Instance = "super-bot-gratis",
    [string]$Zone = "us-west1-a",
    [string]$Project = "superbot-project",
    [string]$RemoteRoot = "/home/juanl/bot",
    [switch]$SkipDependencies
)

$ErrorActionPreference = "Stop"

function Invoke-Remote([string]$Command) {
    & gcloud compute ssh $Instance --zone=$Zone --project=$Project --command=$Command
    if ($LASTEXITCODE -ne 0) {
        throw "Remote command failed: $Command"
    }
}

Write-Host "Deploying Super Bot from Git..."
$serviceStopped = $false

try {
    Invoke-Remote "sudo systemctl stop superbot.service"
    $serviceStopped = $true

    Invoke-Remote "cd $RemoteRoot && git pull --ff-only origin main"

    if (-not $SkipDependencies) {
        Invoke-Remote "cd $RemoteRoot && venv/bin/python -m pip install -q -r apps/telegram_bot/requirements.txt"
    }

    Invoke-Remote "sudo systemctl daemon-reload && sudo systemctl start superbot.service"
    $serviceStopped = $false

    $status = (& gcloud compute ssh $Instance --zone=$Zone --project=$Project --command="sudo systemctl is-active superbot.service").Trim()
    if ($LASTEXITCODE -ne 0 -or $status -ne "active") {
        throw "superbot.service did not become active."
    }

    Write-Host "SUCCESS - Bot is running!" -ForegroundColor Green
    Invoke-Remote "sudo journalctl -u superbot.service --no-pager -n 20"
}
finally {
    if ($serviceStopped) {
        Write-Warning "Restarting superbot.service after an interrupted deploy."
        & gcloud compute ssh $Instance --zone=$Zone --project=$Project --command="sudo systemctl start superbot.service"
    }
}
