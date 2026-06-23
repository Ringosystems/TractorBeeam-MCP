#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs classic 64-bit Microsoft Outlook (Microsoft 365 Apps, Outlook ONLY)
    on the Veeam Backup for Microsoft 365 server so VB365 can "Export to PST".

.DESCRIPTION
    RUN THIS ON THE VB365 SERVER ITSELF (e.g. VEEAMBACKUP), in an ELEVATED PowerShell.

    Why: VB365's Export-to-PST drives 64-bit Outlook via MAPI. The free "New Outlook"
    for Windows has NO MAPI and does not satisfy this; no open-source tool can make
    VB365 produce a PST. Only classic 64-bit Outlook works, and it needs a desktop
    Office license (included with Microsoft 365 Business Standard/Premium and E3/E5).

    This script (idempotent):
      * verifies it is elevated and that 64-bit Outlook is not already installed,
      * acquires the Office Deployment Tool (ODT) setup.exe (winget -> web -> manual),
      * writes an ODT configuration that installs ONLY 64-bit Outlook,
      * runs the silent install and verifies Outlook registered its MAPI/App path.

    AFTER it finishes you must ACTIVATE once (interactive): launch Outlook on the
    server, sign in with a LICENSED Microsoft 365 account, let it activate, close it.
    Then VB365 Export-to-PST will work. (Restores back to a mailbox/site/Teams do
    NOT need Outlook — only PST export does.)

.PARAMETER Product
    ODT product ID matching your license:
      O365BusinessRetail  -> Microsoft 365 Business Standard / Business Premium (default)
      O365ProPlusRetail   -> Microsoft 365 E3 / E5 / Apps for enterprise

.PARAMETER Channel
    Update channel. Default: Current.

.PARAMETER WorkDir
    Scratch folder for ODT + config. Default: C:\ODT-Outlook.

.PARAMETER SetupExe
    Optional path to an already-downloaded ODT setup.exe (skips acquisition).

.EXAMPLE
    .\Install-OutlookForVB365.ps1
.EXAMPLE
    .\Install-OutlookForVB365.ps1 -Product O365ProPlusRetail   # if you're on E3/E5
#>
[CmdletBinding()]
param(
    [ValidateSet('O365BusinessRetail','O365ProPlusRetail')]
    [string]$Product = 'O365BusinessRetail',
    [string]$Channel = 'Current',
    [string]$WorkDir = 'C:\ODT-Outlook',
    [string]$SetupExe = ''
)
$ErrorActionPreference = 'Stop'
function Step($m){ Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Ok($m){ Write-Host "  [OK] $m" -ForegroundColor Green }
function Note($m){ Write-Host "  [!] $m" -ForegroundColor Yellow }

# --- 0) already installed? ----------------------------------------------------
Step 'Checking for existing 64-bit Outlook'
$cfgKey = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -ErrorAction SilentlyContinue
$olkPath = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE' -ErrorAction SilentlyContinue).'(default)'
if ($olkPath -and $cfgKey.Platform -eq 'x64') {
    Ok "64-bit Outlook already present: $olkPath"
    Note 'If VB365 still reports it missing, confirm it activates (launch + sign in) and is 64-bit.'
    return
}
if ($olkPath -and $cfgKey.Platform -eq 'x86') {
    Note 'A 32-bit Office/Outlook is installed. VB365 needs 64-bit. You must uninstall the'
    Note '32-bit Office first (Settings > Apps) — mixing bitness is not supported. Aborting.'
    throw '32-bit Office detected; remove it, then re-run.'
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

# --- 1) acquire the Office Deployment Tool (setup.exe) ------------------------
Step 'Acquiring the Office Deployment Tool (setup.exe)'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$setup = ''
if ($SetupExe -and (Test-Path $SetupExe)) { $setup = $SetupExe; Ok "Using provided setup.exe: $setup" }

if (-not $setup) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        try {
            Note 'Trying winget (Microsoft.OfficeDeploymentTool)...'
            & winget install --id Microsoft.OfficeDeploymentTool -e --silent `
                --accept-package-agreements --accept-source-agreements 2>$null
            $found = Get-ChildItem 'C:\Program Files*\Microsoft Office Deployment Tool*','C:\Program Files*\*OfficeDeploymentTool*' `
                        -Filter setup.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) { $setup = $found.FullName; Ok "winget provided: $setup" }
        } catch { Note "winget path failed: $($_.Exception.Message)" }
    }
}

if (-not $setup) {
    try {
        Note 'Resolving the latest ODT download from the Microsoft Download Center...'
        $page = Invoke-WebRequest 'https://www.microsoft.com/en-us/download/details.aspx?id=49117' -UseBasicParsing
        $link = ([regex]'https://download\.microsoft\.com/[^"'']+officedeploymenttool[^"'']+\.exe').Matches($page.Content) |
                    Select-Object -First 1 -ExpandProperty Value
        if ($link) {
            $odtExe = Join-Path $WorkDir 'odt.exe'
            Note "Downloading ODT: $link"
            Invoke-WebRequest $link -OutFile $odtExe -UseBasicParsing
            & $odtExe /quiet /extract:$WorkDir | Out-Null
            Start-Sleep 3
            $setup = Join-Path $WorkDir 'setup.exe'
            if (Test-Path $setup) { Ok "Extracted: $setup" } else { $setup = '' }
        }
    } catch { Note "Web acquisition failed: $($_.Exception.Message)" }
}

if (-not $setup -or -not (Test-Path $setup)) {
    Note 'Could not auto-acquire the ODT. Manual step:'
    Note '  1) On this server, download the Office Deployment Tool:'
    Note '       https://www.microsoft.com/en-us/download/details.aspx?id=49117'
    Note "  2) Run it to extract setup.exe into $WorkDir"
    Note "  3) Re-run:  .\Install-OutlookForVB365.ps1 -SetupExe $WorkDir\setup.exe -Product $Product"
    throw 'Office Deployment Tool (setup.exe) not available.'
}

# --- 2) write an Outlook-only, 64-bit ODT configuration -----------------------
Step 'Writing ODT configuration (Outlook only, 64-bit)'
$exclude = 'Access','Excel','Groove','Lync','OneDrive','OneNote','PowerPoint','Publisher','Teams','Word','Bing'
$excludeXml = ($exclude | ForEach-Object { "      <ExcludeApp ID=`"$_`" />" }) -join "`n"
$configXml = @"
<Configuration>
  <Add OfficeClientEdition="64" Channel="$Channel">
    <Product ID="$Product">
      <Language ID="en-us" />
$excludeXml
    </Product>
  </Add>
  <Property Name="SharedComputerLicensing" Value="0" />
  <Property Name="AUTOACTIVATE" Value="0" />
  <Display Level="None" AcceptEULA="TRUE" />
  <Updates Enabled="TRUE" />
</Configuration>
"@
$configPath = Join-Path $WorkDir 'configuration-outlook.xml'
Set-Content -Path $configPath -Value $configXml -Encoding UTF8
Ok "Config: $configPath (Product=$Product, 64-bit, Channel=$Channel)"

# --- 3) run the install -------------------------------------------------------
Step 'Installing 64-bit Outlook (this downloads ~1-2 GB and can take 10-30 min)'
$proc = Start-Process -FilePath $setup -ArgumentList "/configure `"$configPath`"" -Wait -PassThru -WindowStyle Hidden
if ($proc.ExitCode -ne 0) { throw "ODT setup.exe exited with code $($proc.ExitCode)." }

# --- 4) verify ----------------------------------------------------------------
Step 'Verifying'
$cfgKey = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -ErrorAction SilentlyContinue
$olkPath = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE' -ErrorAction SilentlyContinue).'(default)'
if ($olkPath -and $cfgKey.Platform -eq 'x64') {
    Ok "64-bit Outlook installed: $olkPath"
} else {
    throw "Install finished but 64-bit Outlook was not detected (Platform=$($cfgKey.Platform))."
}

$bar = '=' * 70
Write-Host "`n$bar" -ForegroundColor Cyan
Write-Host ' NEXT (one-time, interactive): ACTIVATE Outlook' -ForegroundColor Cyan
Write-Host $bar -ForegroundColor Cyan
Write-Host '  1) Launch Outlook on this server (Start > Outlook / classic).'
Write-Host '  2) Sign in with a LICENSED Microsoft 365 account (desktop Office rights).'
Write-Host '  3) Let it activate, then close Outlook.'
Write-Host '  4) In the Veeam Explorer / VB365, Export-to-PST will now work.'
Write-Host "$bar`n" -ForegroundColor Cyan
