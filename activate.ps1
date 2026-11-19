python -m venv venv
& "$PSScriptRoot\venv\Scripts\Activate.ps1"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PSScriptRoot "pw-browsers"
$env:PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright"
python -m pip install -r requirements.txt
python -m playwright install chromium