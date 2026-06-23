#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Creates a dedicated local Windows account for the read-only VB365 MCP reviewer
    and grants it VB365 REST API access by adding it to the local Administrators
    group, then self-tests the credential against the VB365 REST API.

.DESCRIPTION
    RUN THIS ON THE VB365 SERVER ITSELF, in an ELEVATED PowerShell.

    Why local Administrators? Veeam Backup for Microsoft 365 grants full access to
    members of the server's local Administrators group, and VB365 has no native
    "read-only" API role. TractorBeeam365 MCP ships READ-ONLY by default — it
    exposes only HTTP GET tools and cannot change anything unless you explicitly
    opt in to the action/restore tier (TB_ENABLE_ACTIONS + TB_ALLOW_* flags), in
    which case writes are confirm-token gated and audited. Use this account for
    nothing else; store its password only in the git-ignored .env on the workstation.

    The script:
      * is idempotent (re-running resets the password + re-checks membership),
      * generates a strong random password with a cryptographic RNG,
      * works on Windows PowerShell 5.1 and PowerShell 7,
      * prints the exact VB365_USERNAME / VB365_PASSWORD values to use,
      * self-tests the credential against https://localhost:<port>/<ver>/token.

.PARAMETER AccountName
    Local account to create. Default: svc-vb365-review

.PARAMETER RestPort
    VB365 REST API port for the self-test. Default: 4443

.PARAMETER ApiVersion
    VB365 REST API version for the self-test. Default: v8
#>
[CmdletBinding()]
param(
    [string]$AccountName = 'svc-vb365-review',
    [int]   $RestPort    = 4443,
    [string]$ApiVersion  = 'v8'
)

$ErrorActionPreference = 'Stop'

# --- must be elevated --------------------------------------------------------
$wid = [Security.Principal.WindowsIdentity]::GetCurrent()
$wp  = New-Object Security.Principal.WindowsPrincipal($wid)
if (-not $wp.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an ELEVATED PowerShell (Run as administrator).'
}

# --- strong password (crypto RNG; works on .NET Framework 4.x and .NET 5+) ---
function New-StrongPassword {
    param([int]$Length = 24)
    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    try {
        function Get-RandIndex([int]$max) {
            $b = New-Object byte[] 4
            $rng.GetBytes($b)
            return [int]([BitConverter]::ToUInt32($b, 0) % $max)
        }
        # Ambiguous-free character classes.
        $upper   = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
        $lower   = 'abcdefghijkmnpqrstuvwxyz'
        $digit   = '23456789'
        $special = '!@#%^*-_=+?'
        $all     = $upper + $lower + $digit + $special

        $chars = New-Object System.Collections.Generic.List[char]
        foreach ($set in @($upper, $lower, $digit, $special)) {
            $chars.Add($set[(Get-RandIndex $set.Length)])   # guarantee each class
        }
        while ($chars.Count -lt $Length) {
            $chars.Add($all[(Get-RandIndex $all.Length)])
        }
        # Fisher-Yates shuffle so the guaranteed chars are not at fixed positions.
        $arr = $chars.ToArray()
        for ($i = $arr.Length - 1; $i -gt 0; $i--) {
            $j = Get-RandIndex ($i + 1)
            $tmp = $arr[$i]; $arr[$i] = $arr[$j]; $arr[$j] = $tmp
        }
        return -join $arr
    } finally {
        $rng.Dispose()
    }
}

$plainPw = New-StrongPassword -Length 24
$secPw   = ConvertTo-SecureString $plainPw -AsPlainText -Force
$desc    = 'Read-only VB365 config review (Claude MCP)'  # <=48 chars: Set/New-LocalUser limit

# --- create or update the local account --------------------------------------
$existing = Get-LocalUser -Name $AccountName -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    New-LocalUser -Name $AccountName -Password $secPw -FullName 'VB365 Review (read-only)' `
        -Description $desc -PasswordNeverExpires -AccountNeverExpires -UserMayNotChangePassword | Out-Null
    Write-Host "[+] Created local user '$AccountName'." -ForegroundColor Green
} else {
    Set-LocalUser -Name $AccountName -Password $secPw -Description $desc -PasswordNeverExpires $true
    Write-Host "[~] User '$AccountName' already existed - password reset." -ForegroundColor Yellow
}

# --- add to local Administrators (idempotent) --------------------------------
$adminGroup = (Get-LocalGroup -SID 'S-1-5-32-544').Name   # localized name of BUILTIN\Administrators
$isMember = Get-LocalGroupMember -Group $adminGroup -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "*\$AccountName" -or $_.Name -eq $AccountName }
if (-not $isMember) {
    Add-LocalGroupMember -Group $adminGroup -Member $AccountName
    Write-Host "[+] Added '$AccountName' to '$adminGroup'." -ForegroundColor Green
} else {
    Write-Host "[=] '$AccountName' is already in '$adminGroup'." -ForegroundColor DarkGray
}

$restUser = "$env:COMPUTERNAME\$AccountName"

# --- self-test the credential against the VB365 REST API ---------------------
Write-Host "`n[*] Testing the credential against the VB365 REST API ..." -ForegroundColor Cyan
$tokenUrl = "https://localhost:$RestPort/$ApiVersion/token"
$body = @{ grant_type = 'password'; username = $restUser; password = $plainPw }
$tokenOk = $false
try {
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        $resp = Invoke-RestMethod -Uri $tokenUrl -Method Post -Body $body `
            -ContentType 'application/x-www-form-urlencoded' -SkipCertificateCheck -TimeoutSec 20
    } else {
        # Windows PowerShell 5.1: bypass self-signed cert validation + force TLS 1.2.
        Add-Type @"
using System.Net;
using System.Security.Cryptography.X509Certificates;
public class VboCertTrust : ICertificatePolicy {
    public bool CheckValidationResult(ServicePoint s, X509Certificate c, WebRequest r, int p) { return true; }
}
"@ -ErrorAction SilentlyContinue
        [System.Net.ServicePointManager]::CertificatePolicy = New-Object VboCertTrust
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
        $resp = Invoke-RestMethod -Uri $tokenUrl -Method Post -Body $body `
            -ContentType 'application/x-www-form-urlencoded' -TimeoutSec 20
    }
    if ($resp.access_token) { $tokenOk = $true }
} catch {
    Write-Host "[!] REST self-test could not get a token: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "    (The account is still created. If the REST API listens on a different" -ForegroundColor Yellow
    Write-Host "     port/version, re-run with -RestPort / -ApiVersion, or just test from the" -ForegroundColor Yellow
    Write-Host "     workstation. Note REST access must be enabled in the VB365 console.)" -ForegroundColor Yellow
}
if ($tokenOk) {
    Write-Host "[OK] Got an access token - the account works for the VB365 REST API." -ForegroundColor Green
}

# --- output the values to put in .env ----------------------------------------
$bar = '=' * 64
Write-Host "`n$bar" -ForegroundColor Cyan
Write-Host ' Put these into  C:\Dev\TractorBeeam-MCP\.env  on the workstation:' -ForegroundColor Cyan
Write-Host $bar -ForegroundColor Cyan
Write-Host ("  VB365_USERNAME={0}" -f $restUser)
Write-Host ("  VB365_PASSWORD={0}" -f $plainPw)
Write-Host $bar -ForegroundColor Cyan
Write-Host ' This password is shown ONCE. Copy it now, then clear your screen' -ForegroundColor Yellow
Write-Host ' (Clear-Host) so it is not left on display.' -ForegroundColor Yellow
Write-Host "$bar`n" -ForegroundColor Cyan
