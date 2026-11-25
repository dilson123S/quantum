# ==============================================================================
# Quantum Trading Bot — Script de Entrenamiento de IA Cualitativa con Soup
# Modelo: Qwen2.5-3B-Instruct con Layer Streaming en NVIDIA RTX 3050 Laptop GPU
# ==============================================================================

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "   QUANTUM TRADING BOT -- ENTRENAMIENTO DE MODELO SLM CON SOUP" -ForegroundColor Yellow
Write-Host "==================================================================" -ForegroundColor Cyan

# 1. Regenerar dataset con datos recientes
Write-Host "`n[1/4] Verificando y actualizando datasets de entrenamiento..." -ForegroundColor Green
python ml/quantum-slm/generate_quantum_dataset.py

# 2. Localizar Python con Soup CLI y CUDA
$venvPython = "C:\Users\Asus\Documents\NexusERP\ml\tacti-definitive\.venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Error "No se encontro el entorno con Soup CLI en $venvPython"
    exit 1
}

# 3. Lanzar Entrenamiento LoRA en GPU
Write-Host "`n[2/4] Iniciando entrenamiento LoRA en GPU RTX 3050..." -ForegroundColor Green
Write-Host "   Layer Streaming activo, consumo VRAM menor a 3.5 GB..." -ForegroundColor Gray
& $venvPython ml/quantum-slm/train_runner.py train --config ml/quantum-slm/soup_config.yaml --yes

if ($LASTEXITCODE -ne 0) {
    Write-Error "El entrenamiento con Soup fallo con codigo $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "`n[3/4] Exportando modelo entrenado a formato GGUF para Ollama..." -ForegroundColor Green
New-Item -ItemType Directory -Force -Path "ml/quantum-slm/outputs" | Out-Null
& $venvPython ml/quantum-slm/train_runner.py export --model ml/quantum-slm/outputs/quantum-slm-qwen2.5-3b --format gguf --quant f16 --output ml/quantum-slm/outputs/quantum-slm-qwen2.5-3b-f16.gguf

# 4. Registrar en Ollama
Write-Host "`n[4/4] Registrando modelo en Ollama local como quantum..." -ForegroundColor Green
$ollamaExe = "C:\Users\Asus\AppData\Local\Programs\Ollama\ollama.exe"
if (Test-Path $ollamaExe) {
    Set-Location -Path "ml/quantum-slm"
    & $ollamaExe create quantum -f Modelfile
    Set-Location -Path "..\.."
    Write-Host "`n[EXITO TOTAL] El modelo quantum ya esta activo en Ollama!" -ForegroundColor Cyan
    Write-Host "Puedes probarlo con: ollama run quantum" -ForegroundColor Yellow
} else {
    Write-Host "Ollama CLI no encontrado en ruta habitual." -ForegroundColor Yellow
}
Write-Host "==================================================================" -ForegroundColor Cyan
