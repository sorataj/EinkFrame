<#
.SYNOPSIS
    Deploys EinkFrame to GitHub and the Raspberry Pi.

.DESCRIPTION
    1. Commits and pushes any local changes to GitHub (origin/main).
    2. SSHes into the Raspberry Pi (inkyimpression.local) and pulls the latest
       changes into /home/pi/inky-photo-frame.
    3. Restarts the inky-photo-frame and inky-webui services on the Pi.

.PARAMETER Message
    Commit message to use if there are uncommitted changes. If omitted and
    there are uncommitted changes, you'll be prompted.

.PARAMETER SkipCommit
    Skip committing/pushing and just pull + restart on the Pi.

.EXAMPLE
    ./deploy.ps1 -Message "Fix display refresh bug"

.EXAMPLE
    ./deploy.ps1 -SkipCommit
#>

param(
    [string]$Message,
    [switch]$SkipCommit
)

$ErrorActionPreference = "Stop"
$PiHost = "pi@inkyimpression.local"
$PiInstallDir = "/home/pi/inky-photo-frame"
$PiWebuiDir = "/home/pi/inky-webui"

function Write-Step($text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Cyan
}

Push-Location $PSScriptRoot
try {
    if (-not $SkipCommit) {
        Write-Step "Checking local git status"
        $status = git status --porcelain
        if ($status) {
            Write-Host $status
            if (-not $Message) {
                $Message = Read-Host "Enter a commit message"
            }
            Write-Step "Committing changes"
            git add -A
            git commit -m "$Message"
        } else {
            Write-Host "No local changes to commit."
        }

        Write-Step "Pushing to GitHub (origin)"
        git push origin HEAD
    } else {
        Write-Host "Skipping commit/push step (-SkipCommit specified)."
    }

    Write-Step "Pulling latest changes on Raspberry Pi"
    ssh $PiHost "cd $PiInstallDir && git pull"

    Write-Step "Syncing webui/inky_webui.py to its standalone service folder"
    # The inky-webui.service runs from /home/pi/inky-webui, which is a separate
    # copy outside the git repo, so it needs to be refreshed explicitly here.
    Get-Content "$PSScriptRoot\webui\inky_webui.py" -Raw | ssh $PiHost "cat > $PiWebuiDir/inky_webui.py"

    Write-Step "Restarting services on Raspberry Pi"
    ssh $PiHost "sudo systemctl restart inky-photo-frame inky-webui"

    Write-Step "Checking service status"
    ssh $PiHost "systemctl is-active inky-photo-frame inky-webui"

    Write-Host ""
    Write-Host "Deployment complete." -ForegroundColor Green
}
finally {
    Pop-Location
}
