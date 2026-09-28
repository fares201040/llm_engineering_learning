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
    # Windows PowerShell turns native stderr into terminating errors under Stop.
    # Keep the complete Colab traceback so the actual failure remains visible.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & wsl.exe -d $WslDistro -- $ColabBinary --auth=adc @CommandArguments 2>&1
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
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

$GpuChoices = @{
    "1" = $null # CPU
    "2" = "T4"
    "3" = "L4"
    "4" = "G4"
    "5" = "A100"
    "6" = "H100"
}
Write-Host "Choose a Colab runtime:"
Write-Host "  1) CPU    2) T4    3) L4    4) G4    5) A100    6) H100"
while ($true) {
    $choice = (Read-Host "Runtime [1-6, Enter=A100]").Trim()
    if ($choice -eq "") { $choice = "5" }
    if ($GpuChoices.ContainsKey($choice)) { break }
    Write-Host "Enter a number from 1 to 6."
}
$Gpu = $GpuChoices[$choice]
$RuntimeLabel = if ($null -eq $Gpu) { "CPU" } else { $Gpu }

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project Python environment not found at $Python"
}

$OpenAiApiKey = $env:OPENAI_API_KEY
if ([string]::IsNullOrWhiteSpace($OpenAiApiKey)) {
    $OpenAiApiKey = & $Python -c "from dotenv import dotenv_values; import sys; sys.stdout.write(dotenv_values(sys.argv[1]).get('OPENAI_API_KEY') or '')" (Join-Path $RepoRoot ".env")
    if ($LASTEXITCODE -ne 0) { throw "Could not read OPENAI_API_KEY from the project .env file." }
}
if ([string]::IsNullOrWhiteSpace($OpenAiApiKey)) {
    throw "OPENAI_API_KEY is required. Set it in the PowerShell environment or the project .env file."
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

Write-Host "3/6 Creating or reusing the Colab session ($RuntimeLabel requested)..."
$sessions = Invoke-Colab -CommandArguments @("sessions") -Capture
$escapedName = [regex]::Escape($SessionName)
$reused = $sessions -match "\[$escapedName\]"
if ($reused) {
    Write-Host "Session '$SessionName' already exists. Reusing it keeps its current hardware."
    while ($true) {
        $action = (Read-Host "[R]euse it or [N]ew session with $RuntimeLabel (stops the existing session) [R/N, Enter=R]").Trim().ToUpperInvariant()
        if ($action -eq "" -or $action -eq "R") { break }
        if ($action -eq "N") {
            Invoke-Colab -CommandArguments @("stop", "--session", $SessionName)
            $reused = $false
            break
        }
        Write-Host "Enter R or N."
    }
}
if (-not $reused) {
    $newArguments = @("new", "--session", $SessionName)
    if ($null -ne $Gpu) { $newArguments += @("--gpu", $Gpu) }
    Invoke-Colab -CommandArguments $newArguments
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
$ApiKeyFile = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($ApiKeyFile, $OpenAiApiKey)
    $ApiKeyWsl = Convert-ToWslPath $ApiKeyFile
    Invoke-Colab -CommandArguments @(
        "upload", "--session", $SessionName,
        $ApiKeyWsl, "/content/.attendance_openai_api_key"
    )
} finally {
    Remove-Item -LiteralPath $ApiKeyFile -Force -ErrorAction SilentlyContinue
    $OpenAiApiKey = $null
}

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
