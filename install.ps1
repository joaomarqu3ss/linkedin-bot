# Instalador do linkedin-bot para Windows.
# A partir de um clone:  .\install.ps1 [-Yes]
# Direto da internet:
#   irm https://raw.githubusercontent.com/joaomarqu3ss/linkedin-bot/main/install.ps1 | iex
# Sem perguntas (via iex nao ha parametros):  $env:LINKEDIN_BOT_YES = 1
# Branch, tag ou commit (padrao: main):       $env:LINKEDIN_BOT_REF = 'v0.1.0'
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

if ($env:LINKEDIN_BOT_YES) { $Yes = [switch]$true }

$Slug = "joaomarqu3ss/linkedin-bot"
$Ref  = if ($env:LINKEDIN_BOT_REF) { $env:LINKEDIN_BOT_REF } else { "main" }
$LocalBin = Join-Path $env:USERPROFILE ".local\bin"

# Modo local: o script e um arquivo dentro de um clone do projeto.
# Modo remoto (irm | iex): nao ha caminho de script nem clone ao lado.
$Repo = $null
$scriptPath = $MyInvocation.MyCommand.Path
if ($scriptPath) {
    $dir = Split-Path -Parent $scriptPath
    $pp  = Join-Path $dir "pyproject.toml"
    if ((Test-Path $pp) -and (Select-String -Path $pp -Pattern '^name = "linkedin-bot"' -Quiet)) {
        $Repo = $dir
    }
}
if ($Repo) {
    $Source = $Repo; $SourceDesc = $Repo
} else {
    $Source = "linkedin-bot @ https://github.com/$Slug/archive/$Ref.zip"
    $SourceDesc = "github.com/$Slug ($Ref)"
}

Write-Host ""
Write-Host "linkedin-bot - instalacao" -ForegroundColor White
Write-Host $SourceDesc -ForegroundColor DarkGray

# ---------------------------------------------------------------- 1/5
Write-Step "[1/5] Verificando o ambiente"

$os = [System.Environment]::OSVersion.Version
$arch = $env:PROCESSOR_ARCHITECTURE
Write-Ok "sistema: Windows $($os.Major).$($os.Minor) ($arch)"

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Die "PowerShell 5.1 ou superior necessario (atual: $($PSVersionTable.PSVersion))"
}
Write-Ok "PowerShell $($PSVersionTable.PSVersion)"

if ($Repo) { Write-Ok "projeto encontrado (clone local)" }
else       { Write-Ok "sem clone local - instalando de github.com/$Slug" }

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

# uv escreve progresso no stderr; no PS 5.1 o 2>&1 vira ErrorRecord e o 'Stop' abortaria.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
if ($Repo) { $uvArgs = @('--editable', $Repo) } else { $uvArgs = @($Source) }
$out = & uv tool install @uvArgs --force 2>&1
$uvExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($uvExit -ne 0) {
    $out | ForEach-Object { Write-Host "     $_" }
    Die "falha na instalacao. Rode para ver o erro:`n     uv tool install $($uvArgs -join ' ') --force"
}
if ($Repo) { Write-Ok "instalado (modo editavel: alteracoes no codigo valem na hora)" }
else       { Write-Ok "instalado de $Slug@$Ref" }

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
