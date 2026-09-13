param(
    [string]$Instance = "super-bot-gratis",
    [string]$Zone = "us-west1-a",
    [string]$Project = "superbot-project",
    [string]$RemoteRoot = "/home/juanl/bot",
    [string]$Branch = "main",
    [string]$Service = "superbot.service",
    [ValidateRange(10, 300)]
    [int]$HealthTimeout = 45,
    [ValidateRange(3, 120)]
    [int]$StabilitySeconds = 12,
    [ValidateRange(2, 20)]
    [int]$RetainVenvs = 4,
    [switch]$SkipDependencies,
    [switch]$SkipTests,
    [switch]$ForceRestart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Matches([string]$Name, [string]$Value, [string]$Pattern) {
    if ($Value -notmatch $Pattern) {
        throw "Invalid $Name value: $Value"
    }
}

function Invoke-Remote([string]$Command, [switch]$CaptureOutput) {
    if ($CaptureOutput) {
        $output = & gcloud compute ssh $Instance --zone=$Zone --project=$Project --command=$Command
        if ($LASTEXITCODE -ne 0) {
            throw "Remote command failed with exit code $LASTEXITCODE."
        }
        return ($output -join "`n").Trim()
    }

    & gcloud compute ssh $Instance --zone=$Zone --project=$Project --command=$Command
    if ($LASTEXITCODE -ne 0) {
        throw "Remote deploy failed with exit code $LASTEXITCODE."
    }
}

Assert-Matches "instance" $Instance '^[A-Za-z0-9._-]+$'
Assert-Matches "zone" $Zone '^[A-Za-z0-9._-]+$'
Assert-Matches "project" $Project '^[A-Za-z0-9._:-]+$'
Assert-Matches "remote root" $RemoteRoot '^/[A-Za-z0-9._/-]+$'
Assert-Matches "branch" $Branch '^[A-Za-z0-9._/-]+$'
Assert-Matches "service" $Service '^[A-Za-z0-9_.@-]+\.service$'

Write-Host "Fetching the deployment candidate (the running bot stays online)..."
$fetchCommand = "cd '$RemoteRoot' && git fetch --prune origin '+refs/heads/${Branch}:refs/remotes/origin/${Branch}' && git rev-parse 'refs/remotes/origin/${Branch}'"
$target = Invoke-Remote $fetchCommand -CaptureOutput
if ($target -notmatch '^[0-9a-f]{40,64}$') {
    throw "The VM returned an invalid deployment revision: $target"
}

$arguments = @(
    "--repo '$RemoteRoot'",
    "--service '$Service'",
    "--branch '$Branch'",
    "--target '$target'",
    "--health-timeout '$HealthTimeout'",
    "--stability-seconds '$StabilitySeconds'",
    "--retain-venvs '$RetainVenvs'"
)
if ($SkipDependencies) { $arguments += "--skip-dependencies" }
if ($SkipTests) { $arguments += "--skip-tests" }
if ($ForceRestart) { $arguments += "--force-restart" }

$remoteArguments = $arguments -join " "
$deployCommand = "cd '$RemoteRoot' && git cat-file -e '${target}:scripts/deploy_remote.sh' && git show '${target}:scripts/deploy_remote.sh' | bash -s -- $remoteArguments"

Write-Host "Deploying commit $target with preflight tests and automatic rollback..."
Invoke-Remote $deployCommand
Write-Host "SUCCESS - the new release passed the VM health check." -ForegroundColor Green
