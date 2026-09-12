[CmdletBinding()]
param(
    [string]$PostgresPassword = "",
    [int]$Port = 5432,
    [string]$Database = "apdc_attendance",
    [string]$User = "postgres",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

function Resolve-PostgresTool {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $programFiles = @(
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)}
    ) | Where-Object { $_ }

    $candidate = Get-ChildItem -Path $programFiles -Filter "$Name.exe" `
        -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1

    if ($candidate) {
        return $candidate.FullName
    }

    return $null
}

if ($Database -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
    throw "Database must contain only letters, numbers, and underscores, and cannot start with a number."
}

if ($User -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
    throw "User must contain only letters, numbers, and underscores, and cannot start with a number."
}

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}

$psql = Resolve-PostgresTool "psql"

if (-not $PostgresPassword) {
    $PostgresPassword = $env:APDC_POSTGRES_PASSWORD
}

if ($psql -and -not $PostgresPassword) {
    throw "PostgreSQL is already installed. Provide -PostgresPassword (or APDC_POSTGRES_PASSWORD) to connect safely."
}

if (-not $psql -and -not $PostgresPassword) {
    $alphabet = (48..57) + (65..90) + (97..122)
    $PostgresPassword = -join (1..24 | ForEach-Object {
        [char]($alphabet | Get-Random)
    })
    Write-Host "Generated a local PostgreSQL password and saved it in .env.postgres."
}

if (-not $psql -and -not $SkipInstall) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "PostgreSQL is not installed and winget is unavailable. Install PostgreSQL 17 manually, then rerun with -SkipInstall."
    }

    # The EDB installer accepts these unattended options. The generated
    # password avoids an interactive prompt, which makes this script portable
    # across fresh Windows environments.
    $override = "--mode unattended --superpassword `"$PostgresPassword`" --serverport $Port --unattendedmodeui none"
    & $winget.Source install --id PostgreSQL.PostgreSQL.17 --exact --silent `
        --accept-package-agreements --accept-source-agreements --override $override
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL installation failed with exit code $LASTEXITCODE."
    }

    $psql = Resolve-PostgresTool "psql"
}

if (-not $psql) {
    throw "Could not find psql.exe. Install PostgreSQL 17 or provide it on PATH."
}

$oldPgPassword = $env:PGPASSWORD
$env:PGPASSWORD = $PostgresPassword

try {
    $baseArgs = @(
        "-h", "localhost",
        "-p", $Port.ToString(),
        "-U", $User,
        "-v", "ON_ERROR_STOP=1"
    )

    $ready = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        & $psql @baseArgs -d "postgres" -c "SELECT 1" *> $null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 2
    }

    if (-not $ready) {
        throw "PostgreSQL did not become ready on localhost:$Port. Check the PostgreSQL service and installer logs."
    }

    $exists = ((& $psql @baseArgs -d "postgres" -Atqc `
        "SELECT 1 FROM pg_database WHERE datname = '$Database';") -join "").Trim()

    if ($exists -ne "1") {
        & $psql @baseArgs -d "postgres" -c "CREATE DATABASE `"$Database`""
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create database '$Database'."
        }
    }

    $encodedPassword = [Uri]::EscapeDataString($PostgresPassword)
    $envPath = Join-Path $PSScriptRoot ".env.postgres"
    $envContents = @"
ENABLE_POSTGRES=true
POSTGRES_DSN=postgresql://${User}:${encodedPassword}@localhost:${Port}/${Database}
ENABLE_PGVECTOR=false
CHROMA_ANONYMIZED_TELEMETRY=false
SOURCE_CSV_GLOB=*.csv
INGESTION_FORMAT_VERSION=3
ALLOW_ATTENDANCE_SOURCE_REMOVAL=false
"@.Trim() + "`n"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($envPath, $envContents, $utf8NoBom)

    & $psql @baseArgs -d $Database -c `
        "SELECT current_database() AS database, current_user AS user, current_setting('server_version') AS server_version;"
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL health check failed for database '$Database'."
    }

    Write-Host "PostgreSQL is ready: $envPath"
    Write-Host "Structured ingestion is enabled; pgvector remains disabled unless a pgvector-capable server is used."
}
finally {
    if ($null -eq $oldPgPassword) {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    else {
        $env:PGPASSWORD = $oldPgPassword
    }
}
