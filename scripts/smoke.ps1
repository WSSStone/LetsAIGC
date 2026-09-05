[CmdletBinding()]
param(
    [ValidateSet('static', 'runtime', 'sd15', 'sdxl', 'train')]
    [string]$Profile = 'static'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot
try {
    & conda run --no-capture-output -n letsaigc-core python -m pytest tests/unit tests/contract tests/integration
    if ($LASTEXITCODE -ne 0) { throw 'Automated tests failed.' }
    & conda run --no-capture-output -n letsaigc-core letsaigc --json doctor
    if ($LASTEXITCODE -ne 0 -and $Profile -ne 'static') { throw 'Doctor found blocking readiness failures.' }

    switch ($Profile) {
        'runtime' { & conda run --no-capture-output -n letsaigc-core letsaigc --json comfy status }
        'sd15' { & conda run --no-capture-output -n letsaigc-core letsaigc workflow run sd15-smoke }
        'sdxl' { & conda run --no-capture-output -n letsaigc-core letsaigc workflow run sdxl-smoke }
        'train' { & conda run --no-capture-output -n letsaigc-core letsaigc train sdxl-lora --config configs/training/sdxl-lora-smoke.yaml }
    }
    if ($LASTEXITCODE -ne 0 -and $Profile -ne 'static') { throw "Smoke profile failed: $Profile" }
}
finally {
    Pop-Location
}
