param([switch]$DownloadModels)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$qwenEnvironment = Join-Path $projectRoot '.cache/qwen-venv'
$qwenPython = Join-Path $qwenEnvironment 'Scripts/python.exe'
$qwenCacheEnvironment = @{
    UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.cache/python'
    UV_CACHE_DIR = Join-Path $projectRoot '.cache/uv-qwen'
    PIP_CACHE_DIR = Join-Path $projectRoot '.cache/pip'
    HF_HOME = Join-Path $projectRoot '.cache/huggingface'
    TORCH_HOME = Join-Path $projectRoot '.cache/torch'
    TEMP = Join-Path $projectRoot '.cache/tmp'
    TMP = Join-Path $projectRoot '.cache/tmp'
}
$qwenPreviousEnvironment = @{}
try {
    # Process-scoped only; children inherit these paths. Existing shared Python
    # installations are neither moved nor modified by this cache policy.
    foreach ($entry in $qwenCacheEnvironment.GetEnumerator()) {
        $qwenPreviousEnvironment[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, 'Process')
        [System.IO.Directory]::CreateDirectory($entry.Value) | Out-Null
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
    }
    $qwenPreviousEnvironment['HF_HUB_DISABLE_XET'] = [Environment]::GetEnvironmentVariable('HF_HUB_DISABLE_XET', 'Process')
    [Environment]::SetEnvironmentVariable('HF_HUB_DISABLE_XET', '1', 'Process')
    if (-not (Test-Path -LiteralPath $qwenPython)) {
        uv venv --python 3.12 $qwenEnvironment
        if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated Qwen environment.' }
    }
    uv pip install --python $qwenPython 'torch==2.7.1' --index-url https://download.pytorch.org/whl/cu128
    if ($LASTEXITCODE -ne 0) { throw 'Could not install CUDA 12.8 PyTorch.' }
    uv pip install --python $qwenPython -r (Join-Path $PSScriptRoot 'qwen-requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Qwen ASR dependencies.' }
    & $qwenPython -c "import torch; from qwen_asr import Qwen3ASRModel; assert torch.__version__ == '2.7.1+cu128', torch.__version__; print('torch', torch.__version__, 'CUDA', torch.version.cuda, 'GPU available', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU fallback only')"
    if ($LASTEXITCODE -ne 0) { throw 'Qwen import/device verification failed.' }
    if ($DownloadModels) {
        $modelCache = Join-Path $projectRoot '.cache/qwen-models'
        & $qwenPython -c "from huggingface_hub import snapshot_download; import sys; [snapshot_download(m,cache_dir=sys.argv[1],allow_patterns=['*.json','*.safetensors','*.txt','*.tiktoken']) for m in ['Qwen/Qwen3-ASR-1.7B','Qwen/Qwen3-ForcedAligner-0.6B']]" $modelCache
        if ($LASTEXITCODE -ne 0) { throw 'Model download failed; rerun to resume.' }
    }
}
finally {
    foreach ($entry in $qwenPreviousEnvironment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
    }
}
