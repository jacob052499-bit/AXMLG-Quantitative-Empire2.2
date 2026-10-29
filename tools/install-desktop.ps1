param([string]$Version = '45.0.0')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid Electron version.' }
$quantProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$quantDesktopRoot = Join-Path $quantProjectRoot '.desktop'
$quantRuntimePath = Join-Path $quantDesktopRoot ('runtime-v' + $Version)
$quantExePath = Join-Path $quantRuntimePath 'QuantitativeEmpire.exe'
$quantPlatform = if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq 'Arm64') { 'arm64' } else { 'x64' }
$quantAssetName = "electron-v$Version-win32-$quantPlatform.zip"
$quantReleaseUrl = "https://github.com/electron/electron/releases/download/v$Version"
$quantDownloadPath = Join-Path $quantDesktopRoot 'downloads'
New-Item -ItemType Directory -Path $quantDownloadPath -Force | Out-Null
if (-not (Test-Path -LiteralPath $quantExePath)) {
    $quantZipPath = Join-Path $quantDownloadPath $quantAssetName
    $quantSumsPath = Join-Path $quantDownloadPath ("SHASUMS256-v$Version.txt")
    Write-Output "Downloading official Electron v$Version ($quantPlatform)..."
    Invoke-WebRequest -Uri "$quantReleaseUrl/SHASUMS256.txt" -OutFile $quantSumsPath
    $quantChecksumLine = @(Get-Content -LiteralPath $quantSumsPath | Where-Object { $_ -match ('\s+\*?' + [regex]::Escape($quantAssetName) + '$') })
    if ($quantChecksumLine.Count -ne 1) { throw 'Official checksum entry missing or ambiguous.' }
    $quantExpectedHash = ($quantChecksumLine[0] -split '\s+')[0].ToLowerInvariant()
    if (-not (Test-Path -LiteralPath $quantZipPath) -or (Get-FileHash -LiteralPath $quantZipPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $quantExpectedHash) {
        $quantPartialPath = $quantZipPath + '.part'
        Invoke-WebRequest -Uri "$quantReleaseUrl/$quantAssetName" -OutFile $quantPartialPath
        if ((Get-FileHash -LiteralPath $quantPartialPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $quantExpectedHash) { throw 'Electron download checksum verification failed.' }
        Move-Item -LiteralPath $quantPartialPath -Destination $quantZipPath -Force
    }
    if (Test-Path -LiteralPath $quantRuntimePath) { throw "Incomplete runtime already exists. Preserve it for inspection: $quantRuntimePath" }
    Expand-Archive -LiteralPath $quantZipPath -DestinationPath $quantRuntimePath
    Move-Item -LiteralPath (Join-Path $quantRuntimePath 'electron.exe') -Destination $quantExePath
    @{ version=$Version; architecture=$quantPlatform; asset=$quantAssetName; sha256=$quantExpectedHash; source="$quantReleaseUrl/$quantAssetName"; installedAt=(Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $quantRuntimePath 'quant-runtime.json') -Encoding utf8
}
$quantResourcesApp = Join-Path $quantRuntimePath 'resources\app'
New-Item -ItemType Directory -Path $quantResourcesApp -Force | Out-Null
$quantDesktopPackage = Get-Content -LiteralPath (Join-Path $quantProjectRoot 'desktop\package.json') -Raw | ConvertFrom-Json
$quantPackagedEntry = @{ name=$quantDesktopPackage.name; productName=$quantDesktopPackage.productName; version=$quantDesktopPackage.version; main='bootstrap.cjs'; private=$true }
[IO.File]::WriteAllText((Join-Path $quantResourcesApp 'package.json'), ($quantPackagedEntry | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath (Join-Path $quantProjectRoot 'desktop\bootstrap.cjs') -Destination (Join-Path $quantResourcesApp 'bootstrap.cjs') -Force
$quantIconPath = Join-Path $quantProjectRoot 'desktop\icon.ico'
if (-not (Test-Path -LiteralPath $quantIconPath)) { throw 'Build the desktop icon before installing the shortcut.' }
$quantDesktopPath = [Environment]::GetFolderPath('Desktop')
$quantDistribution = Get-Content -LiteralPath (Join-Path $quantProjectRoot 'distribution.json') -Raw | ConvertFrom-Json
$quantShortcutName = if (($quantDistribution.profile -eq 'portable' -or $quantDistribution.mode -eq 'customer-local')) { 'AXMLG Quantitative Empire 本機版.lnk' } else { 'AXMLG Quantitative Empire.lnk' }
$quantShortcutPath = Join-Path $quantDesktopPath $quantShortcutName
$quantBackupPath = Join-Path $quantDesktopRoot 'shortcut-backups'
if (Test-Path -LiteralPath $quantShortcutPath) {
    New-Item -ItemType Directory -Path $quantBackupPath -Force | Out-Null
    Copy-Item -LiteralPath $quantShortcutPath -Destination (Join-Path $quantBackupPath ('AXMLG Quantitative Empire-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.lnk'))
}
$quantShell = New-Object -ComObject WScript.Shell
$quantShortcut = $quantShell.CreateShortcut($quantShortcutPath)
$quantShortcut.TargetPath = $quantExePath
$quantShortcut.Arguments = ''
$quantShortcut.WorkingDirectory = $quantProjectRoot
$quantShortcut.IconLocation = $quantIconPath + ',0'
$quantShortcut.Description = 'AXMLG Quantitative Empire桌面 App · 自動啟動引擎與獨立研究視窗'
$quantShortcut.WindowStyle = 1
$quantShortcut.Save()
$quantInstall = @{ executable=$quantExePath; app=(Join-Path $quantProjectRoot 'desktop'); shortcut=$quantShortcutPath; runtimeVersion=$Version; installedAt=(Get-Date).ToUniversalTime().ToString('o') }
$quantInstall | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $quantDesktopRoot 'installation.json') -Encoding utf8
Write-Output ($quantInstall | ConvertTo-Json)
