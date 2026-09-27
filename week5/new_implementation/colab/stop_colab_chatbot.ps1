[CmdletBinding()]
param(
    [string]$SessionName = "attendance-chatbot",
    [string]$WslDistro = "Ubuntu-24.04",
    [string]$ColabBinary = "/home/faris/.local/bin/colab"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..")).Path
$CleanupScript = Join-Path $PSScriptRoot "cleanup_private_runtime.py"
$SourceArchive = Join-Path $PSScriptRoot "attendance_chatbot_source.zip"
$PrivatePayload = Join-Path $RepoRoot "week5\new_evaluation\results\attendance_private_payload.zip"

function Invoke-Colab {
    param(
        [Parameter(Mandatory)] [string[]]$CommandArguments,
        [switch]$Capture
    )
    $output = & wsl.exe -d $WslDistro -- $ColabBinary --auth=adc @CommandArguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($output | Out-String)
    }
    if ($Capture) {
        return ($output | Out-String)
    }
    $output | ForEach-Object { Write-Host $_ }
}

function Convert-ToWslPath {
    param([Parameter(Mandatory)] [string]$WindowsPath)
    $resolved = (Resolve-Path -LiteralPath $WindowsPath).Path
    $portablePath = $resolved.Replace("\", "/")
    $converted = & wsl.exe -d $WslDistro -- wslpath -a $portablePath 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($converted | Out-String)
    }
    return ($converted | Select-Object -Last 1).Trim()
}

$sessions = Invoke-Colab -CommandArguments @("sessions") -Capture
$escapedName = [regex]::Escape($SessionName)
if ($sessions -match "\[$escapedName\]") {
    try {
        $CleanupWsl = Convert-ToWslPath $CleanupScript
        Invoke-Colab -CommandArguments @(
            "exec", "--session", $SessionName,
            "--file", $CleanupWsl,
            "--timeout", "600"
        )
    } finally {
        Invoke-Colab -CommandArguments @("stop", "--session", $SessionName)
    }
} else {
    Write-Host "The Colab chatbot session is already stopped."
}

Remove-Item -LiteralPath $SourceArchive -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PrivatePayload -Force -ErrorAction SilentlyContinue
Write-Host "Colab chatbot stopped and temporary local payloads removed."
