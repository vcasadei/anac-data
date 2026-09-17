<#
.SYNOPSIS
    Rebuild a large data file from the chunks committed to this repository.

.DESCRIPTION
    Standalone on purpose: no Python, no network, no dependency on the
    anac_data package. Copy this script next to a chunk directory and it still
    works. Decompression uses .NET GzipStream, which ships with Windows
    PowerShell 5.1, because Windows has no gunzip.

    A chunk directory is one holding MANIFEST.json, checksums.sha256 and
    <name>.gz.partNNN files.

.PARAMETER ChunkDir
    The chunk directory to rebuild. Omit to rebuild every chunk set under
    data\dist.

.PARAMETER OutputDir
    Where to write the rebuilt file. Defaults to the matching data\raw\anac
    dataset directory, falling back to the chunk directory itself.

.EXAMPLE
    .\scripts\rebuild.ps1

.EXAMPLE
    .\scripts\rebuild.ps1 -ChunkDir data\dist\estatisticos\Dados_Estatisticos.csv
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $ChunkDir,

    [string] $OutputDir
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ChecksumsName = 'checksums.sha256'
$DefaultDist = Join-Path 'data' 'dist'
$BufferSize = 1MB

function Write-Failure {
    param([string] $Message)
    [Console]::Error.WriteLine("rebuild: $Message")
    exit 1
}

# SHA-256 via .NET rather than Get-FileHash. Get-FileHash is not dependably
# present - it is missing even on some PowerShell 5.1 installations - whereas
# these types ship with the .NET Framework every PowerShell runs on. Streamed,
# so it costs no memory on a large file.
function Get-Sha256 {
    param([string] $Path)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $bytes = $algorithm.ComputeHash($stream)
        } finally {
            $stream.Dispose()
        }
    } finally {
        $algorithm.Dispose()
    }
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

# Read a "# key: value" comment out of checksums.sha256. Keeps this script and
# the shell one reading the same single source of truth.
function Get-Meta {
    param([string[]] $Lines, [string] $Key)
    foreach ($line in $Lines) {
        if ($line -match "^#\s*$([regex]::Escape($Key))\s*:\s*(.+?)\s*$") {
            return $Matches[1]
        }
    }
    return $null
}

function Get-ChunkEntries {
    param([string[]] $Lines)
    # Always an array, so .Count is safe under Set-StrictMode.
    $entries = @()
    foreach ($line in $Lines) {
        if ($line -match '^([0-9a-fA-F]{64})\s\s(.+)$') {
            $entries += [pscustomobject]@{
                Sha256 = $Matches[1].ToLowerInvariant()
                Name   = $Matches[2]
            }
        }
    }
    return $entries
}

# A read-only stream that presents several files as one continuous sequence of
# bytes, which is what the chunks are. Defined before first use.
if (-not ('ConcatenatedStream' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;

public class ConcatenatedStream : Stream
{
    private readonly Queue<Stream> _streams;

    public ConcatenatedStream(IEnumerable<Stream> streams)
    {
        _streams = new Queue<Stream>(streams);
    }

    public override bool CanRead { get { return true; } }
    public override bool CanSeek { get { return false; } }
    public override bool CanWrite { get { return false; } }

    public override int Read(byte[] buffer, int offset, int count)
    {
        int total = 0;
        while (count > 0 && _streams.Count > 0)
        {
            int read = _streams.Peek().Read(buffer, offset, count);
            if (read == 0)
            {
                _streams.Dequeue().Dispose();
                continue;
            }
            total += read;
            offset += read;
            count -= read;
        }
        return total;
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            while (_streams.Count > 0) { _streams.Dequeue().Dispose(); }
        }
        base.Dispose(disposing);
    }

    public override void Flush() { }
    public override long Length { get { throw new NotSupportedException(); } }
    public override long Position
    {
        get { throw new NotSupportedException(); }
        set { throw new NotSupportedException(); }
    }
    public override long Seek(long offset, SeekOrigin origin) { throw new NotSupportedException(); }
    public override void SetLength(long value) { throw new NotSupportedException(); }
    public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
}
'@
}

# Concatenate the chunks and gunzip them in one streaming pass, so memory stays
# flat no matter how large the file is. Get-Content would mangle the bytes and
# read the whole thing first.
function Expand-Chunks {
    param(
        [string[]] $ChunkPaths,
        [string] $Destination
    )
    $inputs = New-Object 'System.Collections.Generic.List[System.IO.Stream]'
    foreach ($path in $ChunkPaths) {
        $inputs.Add([System.IO.File]::OpenRead($path))
    }
    $concatenated = New-Object ConcatenatedStream -ArgumentList (, $inputs)
    try {
        $gzip = New-Object System.IO.Compression.GzipStream(
            $concatenated,
            [System.IO.Compression.CompressionMode]::Decompress
        )
        try {
            $output = [System.IO.File]::Create($Destination)
            try {
                $buffer = New-Object byte[] $BufferSize
                while (($read = $gzip.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $output.Write($buffer, 0, $read)
                }
            } finally {
                $output.Dispose()
            }
        } finally {
            $gzip.Dispose()
        }
    } finally {
        $concatenated.Dispose()
    }
}

# data\dist\<dataset>\<file>\  ->  data\raw\anac\<dataset>\
function Get-DefaultOutputDir {
    param([string] $Dir)
    $full = (Resolve-Path -LiteralPath $Dir).Path
    $dataset = Split-Path -Leaf (Split-Path -Parent $full)
    $distRoot = Split-Path -Parent (Split-Path -Parent $full)
    $dataRoot = Split-Path -Parent $distRoot
    $rawAnac = Join-Path (Join-Path $dataRoot 'raw') 'anac'
    if (Test-Path -LiteralPath $rawAnac) {
        return (Join-Path $rawAnac $dataset)
    }
    return $full
}

function Invoke-RebuildOne {
    param([string] $Dir, [string] $Destination)

    $listingPath = Join-Path $Dir $ChecksumsName
    if (-not (Test-Path -LiteralPath $listingPath)) {
        Write-Failure "$Dir has no $ChecksumsName - is it a chunk directory?"
    }

    $lines = Get-Content -LiteralPath $listingPath
    $name = Get-Meta -Lines $lines -Key 'original-name'
    $wanted = Get-Meta -Lines $lines -Key 'original-sha256'
    $recordedSize = Get-Meta -Lines $lines -Key 'original-size'
    if (-not $name) { Write-Failure "$listingPath has no original-name" }
    if (-not $wanted) { Write-Failure "$listingPath has no original-sha256" }
    $wanted = $wanted.ToLowerInvariant()

    $target = Join-Path $Destination $name

    if (Test-Path -LiteralPath $target) {
        if ((Get-Sha256 -Path $target) -eq $wanted) {
            Write-Output "${name}: already correct, nothing to do"
            return
        }
        Write-Output "${name}: present but does not match the recorded digest, rebuilding"
    }

    $entries = Get-ChunkEntries -Lines $lines
    if ($entries.Count -eq 0) {
        Write-Failure "${name}: $ChecksumsName lists no chunks"
    }

    $missing = @()
    foreach ($entry in $entries) {
        if (-not (Test-Path -LiteralPath (Join-Path $Dir $entry.Name))) {
            $missing += $entry.Name
        }
    }
    if ($missing.Count -gt 0) {
        Write-Failure "${name}: chunk(s) missing from ${Dir}: $($missing -join ' ')"
    }

    Write-Output "${name}: verifying chunks in $Dir"
    foreach ($entry in $entries) {
        $path = Join-Path $Dir $entry.Name
        $actual = Get-Sha256 -Path $path
        if ($actual -ne $entry.Sha256) {
            Write-Failure "${name}: chunk $($entry.Name) failed verification
  expected $($entry.Sha256)
  got      $actual"
        }
    }

    if (-not (Test-Path -LiteralPath $Destination)) {
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    }

    # Sorted names equal concatenation order - what the zero-padded index is for.
    $ordered = @(
        $entries | Sort-Object -Property Name | ForEach-Object {
            Join-Path $Dir $_.Name
        }
    )

    # Never leave a half-written file where a finished one belongs.
    $temp = "$target.rebuilding"
    try {
        Write-Output "${name}: concatenating and decompressing"
        Expand-Chunks -ChunkPaths $ordered -Destination $temp

        $got = Get-Sha256 -Path $temp
        if ($got -ne $wanted) {
            Write-Failure "${name}: rebuilt file does not match the recorded digest
  expected $wanted
  got      $got
  the chunks verified, so the recorded original digest or the chunk set itself is wrong"
        }
        if ($recordedSize) {
            $actualSize = (Get-Item -LiteralPath $temp).Length
            if ([string] $actualSize -ne [string] $recordedSize) {
                Write-Failure "${name}: rebuilt size $actualSize does not match recorded $recordedSize"
            }
        }

        Move-Item -LiteralPath $temp -Destination $target -Force
        Write-Output "${name}: rebuilt and verified -> $target"
    } finally {
        if (Test-Path -LiteralPath $temp) {
            Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
        }
    }
}

# ---------------------------------------------------------------------------
if ($ChunkDir) {
    if (-not (Test-Path -LiteralPath $ChunkDir -PathType Container)) {
        Write-Failure "$ChunkDir is not a directory"
    }
    $destination = if ($OutputDir) { $OutputDir } else { Get-DefaultOutputDir -Dir $ChunkDir }
    Invoke-RebuildOne -Dir $ChunkDir -Destination $destination
    exit 0
}

if (-not (Test-Path -LiteralPath $DefaultDist -PathType Container)) {
    Write-Failure "$DefaultDist not found - run this from the repository root, or pass -ChunkDir"
}

# @() forces an array: a single result would otherwise be a bare FileInfo, and
# under Set-StrictMode asking a bare object for .Count is an error.
$listings = @(
    Get-ChildItem -LiteralPath $DefaultDist -Recurse -Filter $ChecksumsName |
        Sort-Object -Property FullName
)
if ($listings.Count -eq 0) {
    Write-Failure "no chunk sets found under $DefaultDist"
}

foreach ($listing in $listings) {
    $dir = $listing.DirectoryName
    $destination = if ($OutputDir) { $OutputDir } else { Get-DefaultOutputDir -Dir $dir }
    Invoke-RebuildOne -Dir $dir -Destination $destination
}
Write-Output "rebuilt $($listings.Count) file(s)"
exit 0
