# Standalone restore for Windows PowerShell 5.1; no Python required.
param([string]$DataRoot)
$ErrorActionPreference = 'Stop'
$stagePath = $null
$backupPath = $null
try {
    if (-not $DataRoot) {
        if (Get-Process -Name Kandu -ErrorAction SilentlyContinue) {
            throw 'Close Kandu before restoring, then run this script again.'
        }
        $DataRoot = if ($env:KANDU_DATA_DIR) { $env:KANDU_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'KanduDesktop' }
    }
    $destination = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
    $parent = Split-Path -Parent $destination
    if (-not $parent -or $destination -eq [IO.Path]::GetPathRoot($destination).TrimEnd('\')) {
        throw 'The data directory must not be a drive root.'
    }
    $source = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
    if ($source.StartsWith($destination + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $source -eq $destination -or $destination.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Extract the backup outside the destination data directory.'
    }
    $manifest = Get-Content -LiteralPath (Join-Path $source 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.format -ne 'kandu-reading-data' -or $manifest.version -ne 1) { throw 'Unsupported backup format.' }
    $files = @($manifest.files)
    $seen = @{}
    Write-Host ('Verifying {0} files...' -f $files.Count)
    foreach ($file in $files) {
        $relative = [string]$file.path
        if ($relative -notmatch '^data/' -or $relative.Contains('\') -or $relative.Contains(':') -or
            @($relative.Split('/') | Where-Object { $_ -eq '..' -or $_ -eq '.' -or $_ -eq '' }).Count -gt 0 -or $seen.ContainsKey($relative)) {
            throw 'Invalid or duplicate path in backup manifest.'
        }
        $seen[$relative] = $true
        $path = Join-Path $source $relative
        $item = Get-Item -LiteralPath $path
        if ($item.PSIsContainer -or $item.Length -ne $file.size -or
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $file.sha256) {
            throw ('Backup file is incomplete or changed: ' + $relative)
        }
    }
    foreach ($required in @('data/books.json', 'data/settings.json')) {
        if (-not $seen.ContainsKey($required)) { throw ('Missing required file: ' + $required) }
    }
    $parsedLibrary = Get-Content -LiteralPath (Join-Path $source 'data/books.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $library = @($parsedLibrary)
    if ($library.Count -ne $manifest.books) { throw 'Book count does not match manifest.' }
    foreach ($book in $library) {
        $expected = 'books/{0}.{1}' -f $book.id, $book.ext
        if ($book.id -notmatch '^[A-Za-z0-9._-]+$' -or $book.id -in @('.', '..') -or
            $book.ext -notin @('epub', 'pdf') -or $book.path -ne $expected -or -not $seen.ContainsKey('data/' + $expected)) {
            throw 'Invalid book metadata.'
        }
        $book.path = Join-Path $destination $expected
    }
    $settings = Get-Content -LiteralPath (Join-Path $source 'data/settings.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    # Preserve only model configuration already present on the receiving machine.
    $existingSettings = Join-Path $destination 'settings.json'
    if (Test-Path -LiteralPath $existingSettings) {
        $existing = Get-Content -LiteralPath $existingSettings -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($key in @('llm', 'llms', 'defaultLlm')) {
            if ($existing.PSObject.Properties[$key]) { $settings | Add-Member -NotePropertyName $key -NotePropertyValue $existing.$key -Force }
        }
    }
    $settings | Add-Member -NotePropertyName booksDir -NotePropertyValue (Join-Path $destination 'books') -Force
    $settings | Add-Member -NotePropertyName libraryDir -NotePropertyValue '' -Force
    $settings | Add-Member -NotePropertyName migratedLibrary -NotePropertyValue $true -Force
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $stagePath = Join-Path $parent ('KanduDesktop-restore-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stagePath | Out-Null
    Write-Host 'Restoring library and cached reading results...'
    foreach ($file in $files) {
        $target = Join-Path $stagePath $file.path.Substring(5)
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $source $file.path) -Destination $target
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $stagePath 'books.json'), (ConvertTo-Json -InputObject $library -Depth 100), $utf8)
    [IO.File]::WriteAllText((Join-Path $stagePath 'settings.json'), (ConvertTo-Json -InputObject $settings -Depth 100), $utf8)
    if (Test-Path -LiteralPath $destination) {
        $backupPath = $destination + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)
        Move-Item -LiteralPath $destination -Destination $backupPath
    }
    try {
        Move-Item -LiteralPath $stagePath -Destination $destination
        $stagePath = $null
    } catch {
        if ($backupPath -and -not (Test-Path -LiteralPath $destination)) {
            Move-Item -LiteralPath $backupPath -Destination $destination
            $backupPath = $null
        }
        throw
    }
    Write-Host ('RESTORE COMPLETE: {0} books -> {1}' -f $library.Count, $destination)
    if ($backupPath) { Write-Host ('Previous library backup: ' + $backupPath) }
    Write-Host 'Open Kandu v0.8.18 or later. Existing summaries and vocabulary are ready.'
    Write-Host 'Configure your LLM if this computer has no model settings yet.'
} catch {
    Write-Host ('RESTORE FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    if ($stagePath) { Write-Host ('Incomplete staged copy retained at: ' + $stagePath) }
    if ($backupPath) { Write-Host ('Previous data retained at: ' + $backupPath) }
    exit 1
}
