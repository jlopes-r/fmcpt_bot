# Deploy Script - Super Bot to GCP
$INSTANCIA = "super-bot"
$ZONA      = "us-central1-f"
$PROJETO   = ""
$HOME_VM   = "/home/juanl/bot"
$SRC       = Join-Path $PSScriptRoot ".."

Write-Host "Deploying Super Bot to GCP..."
Write-Host ""

Write-Host "[1/6] Stopping bot on VM..."
gcloud compute ssh $INSTANCIA --zone=$ZONA --project=$PROJETO --command="sudo systemctl stop superbot.service" 2>$null
Start-Sleep -Seconds 2

Write-Host "[2/6] Sending files to VM..."
gcloud compute scp --recurse "$SRC\apps" "${INSTANCIA}:${HOME_VM}/" --zone=$ZONA --project=$PROJETO 2>$null
gcloud compute scp --recurse "$SRC\packages" "${INSTANCIA}:${HOME_VM}/" --zone=$ZONA --project=$PROJETO 2>$null
gcloud compute scp --recurse "$SRC\assets" "${INSTANCIA}:${HOME_VM}/" --zone=$ZONA --project=$PROJETO 2>$null
gcloud compute scp --recurse "$SRC\scripts" "${INSTANCIA}:${HOME_VM}/scripts/" --zone=$ZONA --project=$PROJETO 2>$null
gcloud compute scp "$SRC\.gitignore" "${INSTANCIA}:${HOME_VM}/" --zone=$ZONA --project=$PROJETO 2>$null
gcloud compute scp "$SRC\COOKIES_SETUP.md" "${INSTANCIA}:${HOME_VM}/" --zone=$ZONA --project=$PROJETO 2>$null

$COOKIE_LOCAL = Join-Path $SRC "data\instagram_cookies.txt"
if (Test-Path $COOKIE_LOCAL) {
    Write-Host "  Sending Instagram cookies..."
    gcloud compute scp "$COOKIE_LOCAL" "${INSTANCIA}:${HOME_VM}/data/instagram_cookies.txt" --zone=$ZONA --project=$PROJETO 2>$null
}

gcloud compute ssh $INSTANCIA --zone=$ZONA --project=$PROJETO --command="mkdir -p ~/bot/data/downloads ~/bot/data/logs ~/bot/data/sessions" 2>$null

Write-Host "[3/6] Installing dependencies..."
gcloud compute ssh $INSTANCIA --zone=$ZONA --project=$PROJETO --command="cd ~/bot && source venv/bin/activate && pip install -q -r apps/telegram_bot/requirements.txt" 2>$null

Write-Host "[4/6] Syncing .env credentials..."
gcloud compute ssh $INSTANCIA --zone=$ZONA --project=$PROJETO --command="cp ~/bot/apps/telegram_bot/.env ~/bot/.env 2>/dev/null" 2>$null

Write-Host "[5/6] Setting up cron job..."
gcloud compute ssh $INSTANCIA --zone=$ZONA --project=$PROJETO --command="chmod +x ~/bot/scripts/renew_ig_cookies.py" 2>$null
gcloud compute ssh $INSTANCIA --zone=$ZONA --project=$PROJETO --command="export IG_USERNAME=\$(grep IG_USERNAME ~/bot/.env | cut -d= -f2 | tr -d '\x27\x22') && export IG_PASSWORD=\$(grep IG_PASSWORD ~/bot/.env | cut -d= -f2- | tr -d '\x27\x22') && echo IG_USERNAME=\$IG_PASSWORD 2>/dev/null" 2>$null

Write-Host "[6/6] Starting bot..."
gcloud compute ssh $INSTANCIA --zone=$ZONA --project=$PROJETO --command="sudo systemctl daemon-reload && sudo systemctl start superbot.service" 2>$null
Start-Sleep -Seconds 5

Write-Host "Checking bot health..."
$STATUS = gcloud compute ssh $INSTANCIA --zone=$ZONA --project=$PROJETO --command="sudo systemctl is-active superbot.service" 2>&1
if ($STATUS -eq "active") {
    Write-Host "SUCCESS - Bot is running!" -ForegroundColor Green
    gcloud compute ssh $INSTANCIA --zone=$ZONA --project=$PROJETO --command="tail -10 ~/bot/data/logs/bot.log" 2>$null
} else {
    Write-Host "FAILED - Bot not running. Logs:" -ForegroundColor Red
    gcloud compute ssh $INSTANCIA --zone=$ZONA --project=$PROJETO --command="sudo journalctl -u superbot.service --no-pager -n 20" 2>$null
}

Write-Host ""
Write-Host "Deploy complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  View logs:   gcloud compute ssh $INSTANCIA --zone=$ZONA --command='tail -50 ~/bot/data/logs/bot.log'"
Write-Host "  Stop bot:    gcloud compute ssh $INSTANCIA --zone=$ZONA --command='sudo systemctl stop superbot.service'"
Write-Host "  Status:      gcloud compute ssh $INSTANCIA --zone=$ZONA --command='sudo systemctl status superbot.service'"
Write-Host "  Redeploy:    .\scripts\deploy.ps1"
