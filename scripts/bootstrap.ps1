[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('core', 'comfy', 'train')]
    [string]$Component
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LocalRoot = Join-Path $RepoRoot '.local'
$RuntimeRoot = Join-Path $LocalRoot 'runtime'
$LocksRoot = Join-Path $LocalRoot 'locks'
$ModelsRoot = Join-Path $LocalRoot 'models'
$ComfyCommit = '169fcf35a2fc163fec31338b816503ddac0d3fcf'
$TrainerCommit = '37a1cbbc5725ed2a3575506e7bd2001c9908ac92'

if (-not (Get-Command mamba -ErrorAction SilentlyContinue)) {
    throw 'mamba was not found on PATH. Install Miniforge/Mambaforge first.'
}

New-Item -ItemType Directory -Force -Path $LocalRoot, $RuntimeRoot, $LocksRoot, $ModelsRoot | Out-Null

function Test-MambaEnvironment([string]$Name) {
    $Payload = (& mamba env list --json | ConvertFrom-Json)
    return @($Payload.envs | ForEach-Object { Split-Path -Leaf $_ }) -contains $Name
}

function Sync-MambaEnvironment([string]$Name, [string]$File) {
    if (Test-MambaEnvironment $Name) {
        & mamba env update --name $Name --file $File --prune
    }
    else {
        & mamba env create --name $Name --file $File
    }
}

Push-Location $RepoRoot
try {
    switch ($Component) {
        'core' {
            Sync-MambaEnvironment 'letsaigc-core' 'environment/core.yml'
            if ($LASTEXITCODE -ne 0) { throw 'Core environment creation failed.' }
            & mamba run -n letsaigc-core python -m pip install -e '.[tracking,dev]'
            if ($LASTEXITCODE -ne 0) { throw 'Core project dependency installation failed.' }
            & mamba run -n letsaigc-core python -m pip freeze |
                Set-Content -Encoding utf8 (Join-Path $LocksRoot 'letsaigc-core.txt')
        }
        'comfy' {
            $ComfyPath = Join-Path $RuntimeRoot 'ComfyUI'
            if (-not (Test-Path (Join-Path $ComfyPath '.git'))) {
                & git clone --filter=blob:none https://github.com/Comfy-Org/ComfyUI.git $ComfyPath
                if ($LASTEXITCODE -ne 0) { throw 'ComfyUI clone failed.' }
            }
            & git -C $ComfyPath fetch --tags origin
            if ($LASTEXITCODE -ne 0) { throw 'ComfyUI fetch failed.' }
            & git -C $ComfyPath checkout --detach $ComfyCommit
            if ($LASTEXITCODE -ne 0) { throw 'ComfyUI checkout failed.' }
            $Actual = (& git -C $ComfyPath rev-parse HEAD).Trim()
            if ($Actual -ne $ComfyCommit) { throw "ComfyUI revision mismatch: $Actual" }

            Sync-MambaEnvironment 'letsaigc-comfy' 'environment/comfy.yml'
            if ($LASTEXITCODE -ne 0) { throw 'Comfy environment creation failed.' }
            & mamba run -n letsaigc-comfy python -m pip install --index-url https://download.pytorch.org/whl/cu130 torch==2.9.1 torchvision==0.24.1 torchaudio==2.9.1
            if ($LASTEXITCODE -ne 0) { throw 'Comfy PyTorch installation failed.' }
            & mamba run -n letsaigc-comfy python -m pip install -r (Join-Path $ComfyPath 'requirements.txt')
            if ($LASTEXITCODE -ne 0) { throw 'ComfyUI dependency installation failed.' }

            $EscapedRoot = $LocalRoot.Replace('\', '/')
            @"
letsaigc:
  base_path: $EscapedRoot
  checkpoints: models/checkpoints
  diffusion_models: models/diffusion_models
  vae: models/vae
  text_encoders: models/text_encoders
  clip: models/text_encoders
  loras: models/loras
  embeddings: models/embeddings
  input: input
  output: output
"@ | Set-Content -Encoding utf8 (Join-Path $ComfyPath 'extra_model_paths.yaml')
            & mamba run -n letsaigc-comfy python -m pip freeze |
                Set-Content -Encoding utf8 (Join-Path $LocksRoot 'letsaigc-comfy.txt')
        }
        'train' {
            $TrainerPath = Join-Path $RuntimeRoot 'sd-scripts'
            if (-not (Test-Path (Join-Path $TrainerPath '.git'))) {
                & git clone --filter=blob:none https://github.com/kohya-ss/sd-scripts.git $TrainerPath
                if ($LASTEXITCODE -ne 0) { throw 'sd-scripts clone failed.' }
            }
            & git -C $TrainerPath fetch origin
            if ($LASTEXITCODE -ne 0) { throw 'sd-scripts fetch failed.' }
            & git -C $TrainerPath checkout --detach $TrainerCommit
            if ($LASTEXITCODE -ne 0) { throw 'sd-scripts checkout failed.' }
            $Actual = (& git -C $TrainerPath rev-parse HEAD).Trim()
            if ($Actual -ne $TrainerCommit) { throw "sd-scripts revision mismatch: $Actual" }

            Sync-MambaEnvironment 'letsaigc-train-sdxl' 'environment/train-sdxl.yml'
            if ($LASTEXITCODE -ne 0) { throw 'Training environment creation failed.' }
            & mamba run -n letsaigc-train-sdxl python -m pip install --index-url https://download.pytorch.org/whl/cu124 torch==2.6.0 torchvision==0.21.0
            if ($LASTEXITCODE -ne 0) { throw 'Training PyTorch installation failed.' }
            Push-Location $TrainerPath
            try {
                & mamba run -n letsaigc-train-sdxl python -m pip install -r requirements.txt
                if ($LASTEXITCODE -ne 0) { throw 'sd-scripts dependency installation failed.' }
            }
            finally {
                Pop-Location
            }
            & mamba run -n letsaigc-train-sdxl python -m pip freeze |
                Set-Content -Encoding utf8 (Join-Path $LocksRoot 'letsaigc-train-sdxl.txt')
        }
    }
}
finally {
    Pop-Location
}

Write-Host "Bootstrap completed: $Component"
