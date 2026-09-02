[CmdletBinding()]
param(
    [string]$SharedRoot,
    [int]$Port = 8443,
    [Alias("Host")]
    [string]$BindHost = "0.0.0.0",
    [string]$ServerIp,
    [switch]$ForceCert
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-ServerIPv4 {
    $defaultRoute = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric, InterfaceMetric |
        Select-Object -First 1

    $addresses = if ($defaultRoute) {
        Get-NetIPAddress -InterfaceIndex $defaultRoute.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
    } else {
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue
    }

    $address = $addresses |
        Where-Object {
            $_.IPAddress -ne "127.0.0.1" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.AddressState -eq "Preferred"
        } |
        Select-Object -First 1 -ExpandProperty IPAddress

    if (-not $address) {
        throw "Nao foi possivel descobrir o IP local. Execute com -ServerIp 192.168.1.50."
    }

    return $address
}

function Get-PythonCommand {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        return @{ Path = $launcher.Source; Prefix = @("-3") }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{ Path = $python.Source; Prefix = @() }
    }

    throw "Python 3 nao foi encontrado. Instale-o e marque a opcao para adiciona-lo ao PATH."
}

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "A porta deve estar entre 1 e 65535."
}

$projectDir = $PSScriptRoot
Set-Location $projectDir

foreach ($requiredFile in @("app\\main.py", "requirements.txt")) {
    if (-not (Test-Path (Join-Path $projectDir $requiredFile))) {
        throw "Arquivo obrigatorio ausente: $requiredFile. Este script precisa do backend FastAPI e de requirements.txt."
    }
}

if (-not $SharedRoot) {
    $SharedRoot = if ($env:FILE_SERVER_ROOT) {
        $env:FILE_SERVER_ROOT
    } else {
        Join-Path $projectDir "shared_files"
    }
}

$SharedRoot = [System.IO.Path]::GetFullPath($SharedRoot)
New-Item -ItemType Directory -Path $SharedRoot -Force | Out-Null

if (-not $ServerIp) {
    $ServerIp = if ($env:SERVER_IP) { $env:SERVER_IP } else { Get-ServerIPv4 }
}
$ServerIp = $ServerIp.Trim()

$parsedIp = [System.Net.IPAddress]::None
if (-not [System.Net.IPAddress]::TryParse($ServerIp, [ref]$parsedIp) -or
    $parsedIp.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    throw "-ServerIp deve ser um endereco IPv4 valido."
}

$pythonCommand = Get-PythonCommand
$venvPython = Join-Path $projectDir ".venv\\Scripts\\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Ambiente virtual nao encontrado. Preparando .venv..."
    & $pythonCommand.Path @($pythonCommand.Prefix) -m venv ".venv"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar o ambiente virtual." }
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Falha ao atualizar o pip." }
}

Write-Host "Garantindo dependencias do Python..."
& $venvPython -m pip install -r "requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependencias." }

$openSsl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $openSsl) {
    throw "OpenSSL nao foi encontrado no PATH. Instale o OpenSSL para gerar o certificado HTTPS."
}

$certDir = Join-Path $projectDir "certs"
$certPath = Join-Path $certDir "server.crt"
$keyPath = Join-Path $certDir "server.key"
$certificateReason = $null

if ($ForceCert -or $env:FORCE_CERT -eq "1") {
    $certificateReason = "certificado forcado"
} elseif (-not (Test-Path $certPath) -or -not (Test-Path $keyPath)) {
    $certificateReason = "certificado ou chave ausente"
} else {
    $subjectAltName = (& $openSsl.Source x509 -in $certPath -noout -ext subjectAltName 2>$null) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $subjectAltName -notmatch [regex]::Escape("IP Address:$ServerIp")) {
        $certificateReason = "certificado nao contem o IP atual $ServerIp"
    }
}

if ($certificateReason) {
    Write-Host "Gerando certificado HTTPS para $ServerIp ($certificateReason)..."
    New-Item -ItemType Directory -Path $certDir -Force | Out-Null
    & $openSsl.Source req -x509 -newkey rsa:4096 -nodes `
        -keyout $keyPath `
        -out $certPath `
        -days 365 `
        -subj "/CN=$ServerIp" `
        -addext "subjectAltName=IP:$ServerIp,DNS:localhost,IP:127.0.0.1"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar o certificado HTTPS." }
}

$env:FILE_SERVER_ROOT = $SharedRoot
$tokensCsv = & $venvPython -c 'import secrets; print(",".join(secrets.token_urlsafe(16) for _ in range(16)))'
if ($LASTEXITCODE -ne 0 -or -not $tokensCsv) { throw "Falha ao gerar tokens temporarios." }
$env:FILE_SERVER_TOKENS = $tokensCsv.Trim()

$tokenFile = Join-Path $SharedRoot "tokenList"
$tokenLines = @(
    "Tokens desta execucao do servidor"
    "Use um deles no header HTTP:"
    "Authorization: Bearer TOKEN"
    ""
)
$tokenIndex = 1
foreach ($token in $env:FILE_SERVER_TOKENS -split ",") {
    $tokenLines += ("token_{0:D2}={1}" -f $tokenIndex, $token)
    $tokenIndex++
}
[System.IO.File]::WriteAllLines($tokenFile, $tokenLines, [System.Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "Servidor de arquivos HTTPS"
Write-Host "Pasta compartilhada: $SharedRoot"
Write-Host "Endereco local:       https://127.0.0.1:$Port"
Write-Host "Endereco na rede:     https://${ServerIp}:$Port"
Write-Host ""
Write-Host "Documentacao:         https://${ServerIp}:$Port/docs"
Write-Host "Teste de saude:       https://${ServerIp}:$Port/health"
Write-Host "Lista de tokens:      $tokenFile"
Write-Host ""
Get-Content $tokenFile
Write-Host ""
Write-Host "Para desligar, pressione Ctrl+C."
Write-Host ""

& $venvPython -m uvicorn app.main:app `
    --host $BindHost `
    --port $Port `
    --ssl-keyfile $keyPath `
    --ssl-certfile $certPath

exit $LASTEXITCODE
