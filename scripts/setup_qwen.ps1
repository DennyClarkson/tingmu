param([switch]$DownloadModels)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$qwenEnvironment = Join-Path $projectRoot '.cache/qwen-venv'
$qwenPython = Join-Path $qwenEnvironment 'Scripts/python.exe'
$qwenCacheEnvironment = @{
    UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.cache/python'
    UV_PYTHON_BIN_DIR = Join-Path $projectRoot '.cache/python-bin'
    UV_CACHE_DIR = Join-Path $projectRoot '.cache/uv-qwen'
    PIP_CACHE_DIR = Join-Path $projectRoot '.cache/pip'
    HF_HOME = Join-Path $projectRoot '.cache/huggingface'
    TORCH_HOME = Join-Path $projectRoot '.cache/torch'
    TEMP = Join-Path $projectRoot '.cache/tmp'
    TMP = Join-Path $projectRoot '.cache/tmp'
    TMPDIR = Join-Path $projectRoot '.cache/tmp'
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
    $src = Join-Path $projectRoot 'src'
    & $qwenPython -I -B -c "import sys; sys.path.insert(0, sys.argv[1]); import tingmu, torch; from qwen_asr import Qwen3ASRModel; assert torch.__version__ == '2.7.1+cu128', torch.__version__; print('torch', torch.__version__, 'CUDA', torch.version.cuda, 'GPU available', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU fallback only')" $src
    if ($LASTEXITCODE -ne 0) { throw 'Qwen import/device verification failed.' }
    if ($DownloadModels) {
        & $qwenPython -I -B -c "import sys; sys.path.insert(0, sys.argv[1]); from tingmu.media import _qwen_snapshot; from tingmu.runtime import cache_root, confined_path; cache=confined_path(cache_root(), 'qwen-models'); [_qwen_snapshot(m,str(cache)) for m in ['Qwen/Qwen3-ASR-1.7B','Qwen/Qwen3-ForcedAligner-0.6B']]" $src
        if ($LASTEXITCODE -ne 0) { throw 'Model download failed; rerun to resume.' }
    }
}
finally {
    foreach ($entry in $qwenPreviousEnvironment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
    }
}
