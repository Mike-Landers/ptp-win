$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$venvPath = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$activateScript = Join-Path $venvPath "Scripts\Activate.ps1"

if (-not (Test-Path $venvPython)) {
    if (Test-Path $venvPath) {
        throw "The existing .venv is incomplete (missing Scripts\python.exe). Remove or repair it, then run this script again."
    }

    $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonLauncher) {
        & $pythonLauncher.Source -3 -m venv $venvPath
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw "Python 3 was not found. Install Python 3.10 or newer and ensure it is available on PATH."
        }

        & $pythonCommand.Source -m venv $venvPath
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the virtual environment."
    }
}

. $activateScript
Set-Location $repoRoot

python -m pip install -r (Join-Path $repoRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the application requirements."
}

python -m src.main
if ($LASTEXITCODE -ne 0) {
    throw "The application exited with an error."
}
