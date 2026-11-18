<#
.SYNOPSIS
    Installs hearing_bot for the current Windows user, or removes it with -Uninstall.
.DESCRIPTION
    Creates a Python virtual environment, installs the packages, saves your Groq API key as the
    GROQ_API_KEY user environment variable, makes hearing_bot start when you sign in, adds the
    `git aic` command and starts the bot. Run it again after moving the folder or changing settings.
#>
param([switch]$Uninstall)

$Root = $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
$Bot = Join-Path $Root 'hearing_bot.py'
$Shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'hearing_bot.lnk'
$HasGit = [bool](Get-Command git -ErrorAction SilentlyContinue)

function Fail([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    exit 1
}

function Stop-Bot {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*hearing_bot.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Find-Python {
    # A real Python 3.10+; the Microsoft Store "python" placeholder doesn't count.
    foreach ($candidate in @(@{ Exe = 'py'; Args = @('-3') }, @{ Exe = 'python'; Args = @() })) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        $pyArgs = $candidate.Args + @('-c', 'import sys; print(sys.version_info >= (3, 10))')
        $answer = & $candidate.Exe @pyArgs 2>$null
        if ($LASTEXITCODE -eq 0 -and $answer -eq 'True') { return $candidate }
    }
    return $null
}

function Read-Secret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Test-GroqKey([string]$Key) {
    # $true = Groq accepted the key, $false = Groq rejected it, $null = couldn't check (e.g. offline)
    try {
        Invoke-RestMethod -Uri 'https://api.groq.com/openai/v1/models' -TimeoutSec 20 `
            -Headers @{ Authorization = "Bearer $Key" } | Out-Null
        return $true
    } catch {
        $status = $_.Exception.Response.StatusCode
        if ($status -and [int]$status -eq 401) { return $false }
        return $null
    }
}

if ($Uninstall) {
    Stop-Bot
    Remove-Item $Shortcut -ErrorAction SilentlyContinue
    if ($HasGit) { git config --global --unset alias.aic 2>$null }
    Write-Host 'hearing_bot is stopped, it no longer starts when you sign in, and git aic is removed.'
    Write-Host 'Your Groq API key is still saved. To remove it as well, run this in PowerShell:'
    Write-Host '    [Environment]::SetEnvironmentVariable(''GROQ_API_KEY'', $null, ''User'')'
    Write-Host 'You can now delete this folder.'
    exit 0
}

Write-Host 'Installing hearing_bot...'
Stop-Bot  # a running copy would keep the old code and lock files in .venv

$py = Find-Python
if (-not $py) {
    Fail ('Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ ' +
          '(or run: winget install Python.Python.3.13), then run this installer again.')
}
if (-not (Test-Path $Pythonw)) {  # delete the .venv folder to rebuild it
    $venvArgs = $py.Args + @('-m', 'venv', (Join-Path $Root '.venv'))
    & $py.Exe @venvArgs
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create the Python virtual environment.' }
}
& $Python -m pip install --disable-pip-version-check -q -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'Installing the Python packages failed. Check your internet connection and try again.' }

$key = [Environment]::GetEnvironmentVariable('GROQ_API_KEY', 'User')
while ($true) {
    if (-not $key) {
        Write-Host 'Create a free Groq API key at https://console.groq.com/keys'
        $key = Read-Secret 'Paste your Groq API key'
        if (-not $key) { continue }
    }
    $check = Test-GroqKey $key
    if ($check -eq $false) {
        Write-Host 'Groq rejected that API key.' -ForegroundColor Yellow
        $key = $null
        continue
    }
    if ($null -eq $check) { Write-Host 'Could not reach Groq to check the key, saving it anyway.' -ForegroundColor Yellow }
    break
}
[Environment]::SetEnvironmentVariable('GROQ_API_KEY', $key, 'User')
$env:GROQ_API_KEY = $key

$link = (New-Object -ComObject WScript.Shell).CreateShortcut($Shortcut)
$link.TargetPath = $Pythonw
$link.Arguments = '"' + $Bot + '"'
$link.WorkingDirectory = $Root
$link.Description = 'hearing_bot: hold Right Alt to talk'
$link.Save()

if ($HasGit) {
    $commitScript = Join-Path $Root 'ai_commit.py'
    git config --global alias.aic ("!'" + ($Python -replace '\\', '/') + "' '" + ($commitScript -replace '\\', '/') + "'")
}

Start-Process -FilePath $Pythonw -ArgumentList ('"' + $Bot + '"') -WorkingDirectory $Root

Write-Host ''
Write-Host 'Done. hearing_bot is running and will start every time you sign in.' -ForegroundColor Green
Write-Host 'Click in any text box, hold Right Alt, talk, and let go. See README.md for the other shortcuts.'
if (-not $HasGit) { Write-Host 'Git was not found, so the git aic command was not added.' }
