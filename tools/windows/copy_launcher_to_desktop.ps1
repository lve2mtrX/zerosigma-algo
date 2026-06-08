[CmdletBinding()]
param(
    [switch]$Force
)

$source = Join-Path $PSScriptRoot "Launch_ZeroSigma_Algo_Cockpit.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$destination = Join-Path $desktop "Launch_ZeroSigma_Algo_Cockpit.bat"

if (-not (Test-Path -LiteralPath $source)) {
    throw "Launcher not found: $source"
}

if (Test-Path -LiteralPath $destination) {
    if (-not $Force) {
        $answer = Read-Host "Launcher already exists at '$destination'. Overwrite? [y/N]"
        if ($answer -notmatch "^(?i:y|yes)$") {
            Write-Host "Launcher was not copied."
            exit 0
        }
    }
}

Copy-Item -LiteralPath $source -Destination $destination -Force
Write-Host "Copied launcher to: $destination"
