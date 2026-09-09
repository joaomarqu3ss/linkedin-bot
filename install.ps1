# Instalador do linkedin-bot para Windows.
# Uso:  .\install.ps1 [-Yes]
#
# Se o PowerShell bloquear a execução, rode uma vez:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

param([switch]$Yes)

$ErrorActionPreference = 'Stop'

function Write-Ok   { param($m) Write-Host "  " -NoNewline; Write-Host "OK" -ForegroundColor Green -NoNewline; Write-Host "  $m" }
function Write-Warn { param($m) Write-Host "  " -NoNewline; Write-Host " ! " -ForegroundColor Yellow -NoNewline; Write-Host " $m" }
function Write-Step { param($m) Write-Host ""; Write-Host $m -ForegroundColor White }
function Die {
    param($m)
    Write-Host "  " -NoNewline; Write-Host "X" -ForegroundColor Red -NoNewline; Write-Host "  $m"
    exit 1
}

$Repo     = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalBin = Join-Path $env:USERPROFILE ".local\bin"

Write-Host ""
Write-Host "linkedin-bot - instalacao" -ForegroundColor White
Write-Host $Repo -ForegroundColor DarkGray

# ---------------------------------------------------------------- 1/5
Write-Step "[1/5] Verificando o ambiente"

$os = [System.Environment]::OSVersion.Version
$arch = $env:PROCESSOR_ARCHITECTURE
Write-Ok "sistema: Windows $($os.Major).$($os.Minor) ($arch)"

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Die "PowerShell 5.1 ou superior necessario (atual: $($PSVersionTable.PSVersion))"
}
Write-Ok "PowerShell $($PSVersionTable.PSVersion)"

if (-not (Test-Path (Join-Path $Repo "pyproject.toml"))) {
    Die "pyproject.toml nao encontrado em $Repo"
}
Write-Ok "projeto encontrado"

$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    $pyv = (& python -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null)
    Write-Ok "python presente ($pyv)"
} else {
    Write-Warn "python nao encontrado - o uv instalara o interpretador"
}

if (Get-Command linkedin-bot -ErrorAction SilentlyContinue) {
    Write-Warn "ja instalado - sera atualizado"
}

# ---------------------------------------------------------------- 2/5
Write-Step "[2/5] Instalando o uv"

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Ok "uv ja instalado ($((uv --version) -split ' ' | Select-Object -Index 1))"
} else {
    Write-Host "  baixando uv..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Die "falha ao instalar o uv: $_`n     Instale manualmente: https://docs.astral.sh/uv/"
    }
    $env:PATH = "$LocalBin;$env:PATH"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Die "uv instalado mas nao encontrado em $LocalBin"
    }
    Write-Ok "uv instalado"
}

# ---------------------------------------------------------------- 3/5
Write-Step "[3/5] Instalando o linkedin-bot"

$out = & uv tool install --editable $Repo --force 2>&1
if ($LASTEXITCODE -ne 0) {
    Die "falha na instalacao. Rode para ver o erro:`n     uv tool install --editable `"$Repo`" --force"
}
Write-Ok "instalado (modo editavel: alteracoes no codigo valem na hora)"

# ---------------------------------------------------------------- 4/5
Write-Step "[4/5] Conferindo o PATH"

$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -and ($userPath -split ';' | Where-Object { $_.TrimEnd('\') -ieq $LocalBin.TrimEnd('\') })) {
    Write-Ok "$LocalBin ja esta no PATH do usuario"
} else {
    Write-Warn "$LocalBin nao esta no PATH do usuario"
    $add = $Yes
    if (-not $Yes) {
        $ans = Read-Host "    adicionar ao PATH do usuario? [S/n]"
        $add = ($ans -eq '' -or $ans -match '^[sSyY]')
    }
    if ($add) {
        $novo = if ($userPath) { "$userPath;$LocalBin" } else { $LocalBin }
        [Environment]::SetEnvironmentVariable("PATH", $novo, "User")
        $env:PATH = "$env:PATH;$LocalBin"
        Write-Ok "adicionado (abra um terminal novo para valer em outras sessoes)"
    } else {
        Write-Warn "adicione manualmente ao PATH do usuario:"
        Write-Host "      $LocalBin"
    }
}

# ---------------------------------------------------------------- 5/5
Write-Step "[5/5] Diagnostico do ambiente"

$bot = Join-Path $LocalBin "linkedin-bot.exe"
if (-not (Test-Path $bot)) {
    $cmd = Get-Command linkedin-bot -ErrorAction SilentlyContinue
    if ($cmd) { $bot = $cmd.Source } else { Die "linkedin-bot nao encontrado apos a instalacao" }
}
& $bot doctor

Write-Host ""
Write-Host "Proximo passo:" -ForegroundColor White
Write-Host "  linkedin-bot auth setup" -NoNewline
Write-Host "    # cadastra o app LinkedIn e autentica" -ForegroundColor DarkGray
Write-Host ""
