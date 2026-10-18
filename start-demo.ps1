<#
.SYNOPSIS
  Levanta la demo del asistente bancario con un solo comando.

.DESCRIPTION
  Modo completo (por defecto): Neo4j + Ollama (qwen2.5:3b en GPU) en Docker, router LoRA,
  Graph RAG y frontend. Verifica cada pieza y, si falta algo (modelo de Ollama, grafo sin cargar,
  adaptador LoRA), lo resuelve o degrada con aviso en lugar de fallar.

  Abre dos ventanas (backend y frontend) para ver sus logs, y el navegador cuando todo responde.

.EXAMPLE
  .\start-demo.ps1             # demo completa
  .\start-demo.ps1 -Lite       # sin Docker ni GPU: reglas + BM25 + plantillas
  .\start-demo.ps1 -Stop       # detiene servidores y contenedores de la demo
  .\start-demo.ps1 -NoBrowser  # no abre el navegador
#>
param(
    [switch]$Lite,
    [switch]$Stop,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Uvicorn = Join-Path $Root ".venv\Scripts\uvicorn.exe"
$Adapter = Join-Path $Root "finetune\adapters\qwen2.5-0.5b-intent-lora"
$BackendPort = 8000
$FrontendPort = 5173
$OllamaModel = "qwen2.5:3b"

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "    $Message" -ForegroundColor Green }
function Write-Note([string]$Message) { Write-Host "    $Message" -ForegroundColor Yellow }

function Stop-Port([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}

function Wait-Url([string]$Url, [int]$Seconds) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        try {
            Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

# Los comandos nativos se ejecutan vía cmd para que PowerShell 5.1 no convierta su stderr
# (donde docker escribe el progreso) en errores rojos
function Invoke-Native([string]$Command) {
    cmd /c "$Command 2>&1" | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    return $LASTEXITCODE
}

function Test-Python([string]$Code) {
    Push-Location $Root
    & $Python -c $Code *> $null
    $ok = ($LASTEXITCODE -eq 0)
    Pop-Location
    return $ok
}

# ---------------------------------------------------------------------------
# -Stop
# ---------------------------------------------------------------------------
if ($Stop) {
    Write-Step "Deteniendo backend y frontend"
    Stop-Port $BackendPort
    Stop-Port $FrontendPort
    # Cierra las ventanas que abrió este script. Se buscan por línea de comandos y no por título:
    # en Windows Terminal el título no queda en el proceso de PowerShell.
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like "*asistente bancario*" -and $_.ProcessId -ne $PID } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Step "Deteniendo contenedores de la demo (neo4j, ollama)"
    Push-Location $Root
    Invoke-Native "docker compose stop neo4j ollama" | Out-Null
    Pop-Location
    Write-Ok "Listo. Los demás contenedores de Docker no se tocaron."
    exit 0
}

# ---------------------------------------------------------------------------
# 1. Requisitos básicos
# ---------------------------------------------------------------------------
Write-Step "Verificando requisitos"
if (-not (Test-Path $Python)) {
    Write-Host "No existe el entorno .venv. Créalo con:" -ForegroundColor Red
    Write-Host "  python -m venv .venv; .\.venv\Scripts\pip install -r backend\requirements.txt"
    exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "No se encontró npm. Instala Node.js 20 o superior." -ForegroundColor Red
    exit 1
}
Write-Ok "Python (.venv) y Node.js disponibles"

# Una instancia anterior ocupando los puertos se reemplaza
Stop-Port $BackendPort
Stop-Port $FrontendPort

$envVars = [ordered]@{ HF_HUB_OFFLINE = "1" }

if ($Lite) {
    Write-Note "Modo liviano: router por reglas, BM25 en memoria y respuestas por plantilla"
    $envVars["ROUTER_BACKEND"] = "rules"
    $envVars["KNOWLEDGE_BACKEND"] = "keyword"
    $envVars["LLM_PROVIDER"] = "fake"
} else {
    # -----------------------------------------------------------------------
    # 2. Docker: Neo4j + Ollama
    # -----------------------------------------------------------------------
    Write-Step "Docker"
    cmd /c "docker info >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        $dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
        if (Test-Path $dockerDesktop) {
            Write-Note "Docker no responde: iniciando Docker Desktop (puede tardar un minuto)..."
            Start-Process $dockerDesktop
            for ($i = 0; $i -lt 60; $i++) {
                Start-Sleep -Seconds 3
                cmd /c "docker info >nul 2>&1"
                if ($LASTEXITCODE -eq 0) { break }
            }
        }
        cmd /c "docker info >nul 2>&1"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Docker no está disponible. Ábrelo o usa: .\start-demo.ps1 -Lite" -ForegroundColor Red
            exit 1
        }
    }
    Write-Ok "Docker disponible"

    Write-Step "Levantando Neo4j y Ollama"
    Push-Location $Root
    Invoke-Native "docker compose up -d neo4j ollama" | Out-Null
    Pop-Location
    if (-not (Wait-Url "http://localhost:7474" 120)) {
        Write-Host "Neo4j no respondió en 2 minutos. Revisa: docker logs bank-neo4j" -ForegroundColor Red
        exit 1
    }
    Write-Ok "Neo4j listo (http://localhost:7474)"
    if (-not (Wait-Url "http://localhost:11434/api/version" 60)) {
        Write-Host "Ollama no respondió. Revisa: docker logs bank-ollama" -ForegroundColor Red
        exit 1
    }
    Write-Ok "Ollama listo"

    # Modelo de Ollama
    $models = (Invoke-RestMethod "http://localhost:11434/api/tags").models | ForEach-Object { $_.name }
    if ($models -notcontains $OllamaModel) {
        Write-Note "Descargando $OllamaModel (~1.9 GB, solo la primera vez)..."
        docker exec bank-ollama ollama pull $OllamaModel
    }
    Write-Ok "Modelo $OllamaModel disponible"

    # Grafo de conocimiento cargado
    if (-not (Test-Python "import sys; sys.path.insert(0, 'backend'); from app.rag.neo4j_client import neo4j_available; sys.exit(0 if neo4j_available() else 1)")) {
        Write-Note "El grafo está vacío: cargando productos, políticas y quejas..."
        Push-Location (Join-Path $Root "backend")
        & $Python scripts/seed_neo4j.py
        Pop-Location
    }
    Write-Ok "Grafo de conocimiento cargado"

    # Router LoRA: requiere el adaptador entrenado y torch/peft instalados
    $router = "rules"
    if ((Test-Path $Adapter) -and (Test-Python "import torch, peft")) {
        $router = "cascade"  # reglas para lo obvio, LoRA para el resto (finetune/README.md)
        Write-Ok "Router en cascada: reglas + LoRA"
    } else {
        Write-Note "Router LoRA no disponible (falta el adaptador o torch/peft): se usa el router por reglas."
        Write-Note "Para habilitarlo: pip install -r finetune\requirements.txt; python finetune\train_lora.py"
    }

    $envVars["ROUTER_BACKEND"] = $router
    $envVars["KNOWLEDGE_BACKEND"] = "neo4j"
    $envVars["LLM_PROVIDER"] = "ollama"
}

# ---------------------------------------------------------------------------
# 3. Backend (ventana propia para ver los logs)
# ---------------------------------------------------------------------------
Write-Step "Iniciando backend (puerto $BackendPort)"
$envCommand = ($envVars.GetEnumerator() | ForEach-Object { "`$env:$($_.Key)='$($_.Value)'" }) -join "; "
$backendCommand = "`$host.UI.RawUI.WindowTitle = 'Backend - asistente bancario'; $envCommand; " +
    "Set-Location '$Root\backend'; & '$Uvicorn' app.main:app --port $BackendPort"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCommand | Out-Null

# Con LoRA + LLM el arranque precarga ambos modelos (~30 s)
if (-not (Wait-Url "http://localhost:$BackendPort/health" 180)) {
    Write-Host "El backend no respondió en 3 minutos. Revisa la ventana 'Backend'." -ForegroundColor Red
    exit 1
}
$health = Invoke-RestMethod "http://localhost:$BackendPort/health"
Write-Ok "Backend $($health.status) - router=$($health.components.router), conocimiento=$($health.components.knowledge), LLM=$($health.components.llm_provider)"

# ---------------------------------------------------------------------------
# 4. Frontend
# ---------------------------------------------------------------------------
Write-Step "Iniciando frontend (puerto $FrontendPort)"
$frontendDir = Join-Path $Root "frontend"
if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Write-Note "Instalando dependencias del frontend (solo la primera vez)..."
    Push-Location $frontendDir
    Invoke-Native "npm install --no-fund --no-audit" | Out-Null
    Pop-Location
}
$frontendCommand = "`$host.UI.RawUI.WindowTitle = 'Frontend - asistente bancario'; Set-Location '$frontendDir'; npm run dev"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCommand | Out-Null
if (-not (Wait-Url "http://localhost:$FrontendPort" 60)) {
    Write-Host "El frontend no respondió. Revisa la ventana 'Frontend'." -ForegroundColor Red
    exit 1
}
Write-Ok "Frontend listo"

# ---------------------------------------------------------------------------
# 5. Listo
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Demo lista: http://localhost:$FrontendPort" -ForegroundColor Green
Write-Host "  API (Swagger):   http://localhost:$BackendPort/docs"
if (-not $Lite) { Write-Host "  Neo4j Browser:   http://localhost:7474  (neo4j / bankdemo123)" }
Write-Host "  Detener todo:    .\start-demo.ps1 -Stop"
Write-Host ""
if (-not $NoBrowser) { Start-Process "http://localhost:$FrontendPort" }
