[CmdletBinding()]
param(
    [ValidateSet('static', 'runtime', 'sd15', 'sdxl', 'train')]
    [string]$Profile = 'static'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot
try {
    & mamba run -n letsaigc-core python -m pytest tests/unit tests/contract tests/integration
    if ($LASTEXITCODE -ne 0) { throw 'Automated tests failed.' }
    & mamba run -n letsaigc-core letsaigc --json doctor
    if ($LASTEXITCODE -ne 0 -and $Profile -ne 'static') { throw 'Doctor found blocking readiness failures.' }

    switch ($Profile) {
        'runtime' { & mamba run -n letsaigc-core letsaigc --json comfy status }
        'sd15' { & mamba run -n letsaigc-core letsaigc workflow run sd15-smoke }
        'sdxl' { & mamba run -n letsaigc-core letsaigc workflow run sdxl-smoke }
        'train' { & mamba run -n letsaigc-core letsaigc train sdxl-lora --config configs/training/sdxl-lora-smoke.yaml }
    }
    if ($LASTEXITCODE -ne 0 -and $Profile -ne 'static') { throw "Smoke profile failed: $Profile" }
}
finally {
    Pop-Location
}
