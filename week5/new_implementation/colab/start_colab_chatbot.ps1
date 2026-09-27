[CmdletBinding()]
param(
    [string]$SessionName = "attendance-chatbot",
    [string]$WslDistro = "Ubuntu-24.04",
    [string]$ColabBinary = "/home/faris/.local/bin/colab"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$SourceArchive = Join-Path $PSScriptRoot "attendance_chatbot_source.zip"
$PrivatePayload = Join-Path $RepoRoot "week5\new_evaluation\results\attendance_private_payload.zip"
$Bootstrap = Join-Path $PSScriptRoot "bootstrap_chatbot.py"

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

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project Python environment not found at $Python"
}

Write-Host "1/6 Packaging the current application code..."
& $Python -m week5.new_implementation.colab.package_chatbot_source
if ($LASTEXITCODE -ne 0) { throw "Application packaging failed." }

Write-Host "2/6 Exporting the current authorized attendance data..."
& $Python -m week5.new_implementation.colab.export_private_payload
if ($LASTEXITCODE -ne 0) { throw "Attendance payload export failed." }
$PayloadHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PrivatePayload).Hash.ToLowerInvariant()

$SourceWsl = Convert-ToWslPath $SourceArchive
$PayloadWsl = Convert-ToWslPath $PrivatePayload
$BootstrapWsl = Convert-ToWslPath $Bootstrap

Write-Host "3/6 Creating or reusing the Colab A100 session..."
$sessions = Invoke-Colab -CommandArguments @("sessions") -Capture
$escapedName = [regex]::Escape($SessionName)
$reused = $sessions -match "\[$escapedName\]"
if (-not $reused) {
    Invoke-Colab -CommandArguments @("new", "--session", $SessionName, "--gpu", "A100")
}

Write-Host "4/6 Uploading the current code and attendance payload..."
Invoke-Colab -CommandArguments @(
    "upload", "--session", $SessionName,
    $SourceWsl, "/content/attendance_chatbot_source.zip"
)
Invoke-Colab -CommandArguments @(
    "upload", "--session", $SessionName,
    $PayloadWsl, "/content/attendance_private_payload.next.zip"
)

if ($reused) {
    Write-Host "5/6 Restarting the reused Colab kernel..."
    Invoke-Colab -CommandArguments @("restart-kernel", "--session", $SessionName)
} else {
    Write-Host "5/6 Colab kernel is ready."
}

Write-Host "6/6 Installing the runtime and starting the chatbot..."
Write-Host "The first run downloads the models and can take several minutes."
$launchOutput = Invoke-Colab -CommandArguments @(
    "exec", "--session", $SessionName,
    "--file", $BootstrapWsl,
    "--env", "ATTENDANCE_PRIVATE_PAYLOAD_SHA256=$PayloadHash",
    "--timeout", "3600"
) -Capture
Write-Host $launchOutput
if ($launchOutput -notmatch "CHATBOT_READY") {
    throw "Colab did not report that the chatbot is ready. Review the error above."
}

Write-Host ""
Write-Host "Open the public Gradio URL printed above and ask your questions."
Write-Host "When finished, run:"
Write-Host ".\week5\new_implementation\colab\stop_colab_chatbot.ps1"
