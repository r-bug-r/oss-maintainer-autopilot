#Requires -Version 5.1
<#
.SYNOPSIS
  Synthesize a plausible commit timeline from HEAD's tree (PowerShell edition).

.DESCRIPTION
  Reads the files that exist in HEAD, splits text files into top-level blocks,
  orders those blocks the way a project actually grows (hygiene -> build ->
  docs -> core -> tests -> scripts -> ui -> fixes -> refactors -> performance),
  spreads the resulting commits over a calendar with sprint weeks, regular
  weeks and quiet gaps, and writes them with git plumbing (temporary index +
  commit-tree) using GIT_AUTHOR_DATE and GIT_COMMITTER_DATE.

  The working tree, the real index and the current branch are never touched.
  The final commit of the new branch is verified byte-for-byte against HEAD.

  This is a port of scripts/history/rebuild-history.sh. Given the same seed,
  both scripts plan the exact same timeline.

.PARAMETER Repo
  Repository to read (default: .).

.PARAMETER StartDate
  First commit date, YYYY-MM-DD (default: 2025-01-01).

.PARAMETER EndDate
  Last commit date, YYYY-MM-DD (default: today, never in the future).

.PARAMETER Branch
  Branch to write (default: history-rebuild).

.PARAMETER Seed
  Planning seed; same seed, same timeline (default: 20250101).

.PARAMETER Density
  Target commits per week (default: 1.6).

.PARAMETER Tz
  Timezone offset recorded on commits, like +0200 (default: system).

.PARAMETER Author
  Author and committer identity as "Name <email>" (default: git config).

.PARAMETER MinLines
  Merge blocks smaller than this (default: 18).

.PARAMETER MaxBlocks
  Cap blocks per file (default: 12).

.PARAMETER NoSplit
  One commit unit per file instead of per block.

.PARAMETER Force
  Overwrite the output branch if it already exists.

.PARAMETER DryRun
  Print the plan and write nothing.

.PARAMETER Trace
  Print every commit while it is written.

.PARAMETER Quiet
  Print only the final summary.

.EXAMPLE
  ./rebuild-history.ps1 -DryRun
  ./rebuild-history.ps1 -Seed 7 -Author 'Oss Maintainer <dev@example.com>'
#>
[CmdletBinding()]
param(
    [string]$Repo = '.',
    [string]$StartDate = '2025-01-01',
    [string]$EndDate = '',
    [string]$Branch = 'history-rebuild',
    [int]$Seed = 20250101,
    [double]$Density = 1.6,
    [string]$Tz = '',
    [string]$Author = '',
    [int]$MinLines = 18,
    [int]$MaxBlocks = 12,
    [switch]$NoSplit,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$Trace,
    [switch]$Quiet
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:Prog = 'rebuild-history.ps1'
$script:MaxTextBytes = 1048576
$script:VerboseOutput = [bool]$Trace

# ------------------------------------------------------------------ helpers ---

function Stop-WithError {
    param([string]$Message)
    [Console]::Error.WriteLine("$($script:Prog): error: $Message")
    exit 1
}

function Write-Info {
    param([string]$Message)
    if (-not $Quiet -and -not $DryRun) { Write-Host $Message }
}

function Write-Note {
    param([string]$Message)
    if ($script:VerboseOutput -and -not $Quiet) { Write-Host $Message }
}

function Invoke-Git {
    # Runs git and returns its stdout lines. Throws when git fails.
    param([string[]]$GitArgs, [switch]$AllowFail)
    $output = & git @GitArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        if ($AllowFail) { return $null }
        throw "git $($GitArgs -join ' ') failed ($LASTEXITCODE): $output"
    }
    return , @($output)
}

function Invoke-GitText {
    param([string[]]$GitArgs, [switch]$AllowFail)
    $lines = Invoke-Git -GitArgs $GitArgs -AllowFail:$AllowFail
    if ($null -eq $lines) { return '' }
    return (($lines | ForEach-Object { "$_" }) -join "`n").Trim()
}

# ---------------------------------------------------------------------- rng ---

$script:RngState = 0L

# Integer division truncated toward zero, matching POSIX shell arithmetic.
# PowerShell's [int] cast rounds, so every quotient in this script goes through here.
function Get-IntDiv {
    param([long]$A, [long]$B)
    return [long][Math]::Floor($A / $B)
}

function Reset-Rng {
    param([long]$NewSeed)
    $script:RngState = $NewSeed % 4294967296L
    if ($script:RngState -eq 0) { $script:RngState = 1 }
}

# Get-Rnd sets $script:Rnd to a value in [0, N).
function Get-Rnd {
    param([int]$Bound)
    $script:RngState = (1664525L * $script:RngState + 1013904223L) % 4294967296L
    $script:Rnd = [int](Get-IntDiv -A ($script:RngState * $Bound) -B 4294967296L)
}

# Get-WeightedPick sets $script:PwPick from the array in $script:PwArr.
function Get-WeightedPick {
    $total = 0
    foreach ($w in $script:PwArr) { $total += $w }
    if ($total -le 0) { $script:PwPick = 0; return }
    Get-Rnd $total
    $r = $script:Rnd
    $acc = 0
    for ($i = 0; $i -lt $script:PwArr.Count; $i++) {
        $acc += $script:PwArr[$i]
        if ($r -lt $acc) { $script:PwPick = $i; return }
    }
    $script:PwPick = $script:PwArr.Count - 1
}

# ---------------------------------------------------------------------- text ---

function ConvertTo-Slug {
    param([string]$Value)
    $sb = New-Object System.Text.StringBuilder
    $lastSpace = $false
    foreach ($ch in $Value.ToLowerInvariant().ToCharArray()) {
        $keep = $false
        if (($ch -ge 'a' -and $ch -le 'z') -or ($ch -ge '0' -and $ch -le '9')) { $keep = $true }
        elseif ('.:#+@/<>()-'.IndexOf($ch) -ge 0) { $keep = $true }
        elseif ($ch -eq '_') { $keep = $true }
        if ($keep) { [void]$sb.Append($ch); $lastSpace = $false }
        elseif (-not $lastSpace) { [void]$sb.Append(' '); $lastSpace = $true }
    }
    return $sb.ToString().Replace('_', '-').Trim()
}

function Split-LinesWithEndings {
    # Mirrors bash's `while IFS= read -r line`: every line keeps its "\n", the
    # final unterminated fragment becomes a line only when non-empty.
    param([string]$Text)
    $lines = New-Object System.Collections.Generic.List[string]
    $start = 0
    while ($true) {
        $idx = $Text.IndexOf("`n", $start)
        if ($idx -lt 0) { break }
        $lines.Add($Text.Substring($start, $idx - $start + 1))
        $start = $idx + 1
    }
    if ($start -lt $Text.Length) { $lines.Add($Text.Substring($start)) }
    return , $lines
}

function Get-LineText {
    param([string]$Line)
    if ($Line.EndsWith("`n")) { return $Line.Substring(0, $Line.Length - 1) }
    return $Line
}

function Get-FirstHeading {
    # First markdown heading outside a fenced block, slugged.
    param([string]$Text)
    $fence = 0
    $out = ''
    foreach ($raw in (Split-LinesWithEndings -Text $Text)) {
        $line = Get-LineText -Line $raw
        if ($line.StartsWith('```')) { $fence = 1 - $fence; continue }
        if ($fence -eq 1) { continue }
        if ($line.StartsWith('## ')) { $out = $line.Substring(3); break }
        if ($line.StartsWith('### ')) { $out = $line.Substring(4); break }
        if ($line.StartsWith('# ')) { $out = $line.Substring(2); break }
    }
    $out = $out -replace '^[0-9][0-9.]*[.)]? *', ''
    $out = $out -replace '[ \t]*#*$', ''
    return (ConvertTo-Slug -Value $out)
}

function Get-Symbol {
    # First interesting declaration in a block, ported from symbol_of.
    param([string]$Text, [string]$Ext)
    $extract = {
        param([string]$S)
        $m = [regex]::Match($S, '^[A-Za-z0-9_$]+')
        return $m.Value
    }
    foreach ($raw in (Split-LinesWithEndings -Text $Text)) {
        $line = Get-LineText -Line $raw
        if ($line.Length -eq 0) { continue }
        if ($line[0] -eq ' ' -or $line[0] -eq "`t") { continue }
        if ($Ext -eq 'py') {
            if ($line.StartsWith('def ')) { return (& $extract $line.Substring(4)) }
            if ($line.StartsWith('async def ')) { return (& $extract $line.Substring(10)) }
            if ($line.StartsWith('class ')) { return (& $extract $line.Substring(6)) }
            if ($line.StartsWith('if __name__')) { return '__main__' }
            continue
        }
        $prefixes = @(
            'export async function ', 'export function ', 'async function ', 'function ',
            'export const ', 'const ', 'class ', 'interface ', 'type ', 'struct ',
            'pub fn ', 'fn ', 'func ', 'impl '
        )
        foreach ($p in $prefixes) {
            if ($line.StartsWith($p)) { return (& $extract $line.Substring($p.Length)) }
        }
        if ($line.StartsWith('public ') -or $line.StartsWith('private ') -or $line.StartsWith('protected ')) {
            $m = [regex]::Match($line, '^[A-Za-z0-9_$ ]+')
            return $m.Value.Trim()
        }
        if ($line.Contains('{')) {
            $cut = $line.IndexOf('{')
            $head = $line.Substring(0, $cut).TrimEnd()
            return $head.TrimStart('.')
        }
    }
    return ''
}

# ---------------------------------------------------------------- taxonomy ---

function Test-SplittableExt {
    param([string]$Ext)
    return @('py', 'md', 'js', 'jsx', 'ts', 'tsx', 'css', 'go', 'rs', 'java') -contains $Ext
}

function Test-TextBlob {
    param([string]$Path)
    $fi = New-Object System.IO.FileInfo($Path)
    if ($fi.Length -gt $script:MaxTextBytes) { return $false }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $limit = [Math]::Min(4096, $bytes.Length)
    for ($i = 0; $i -lt $limit; $i++) { if ($bytes[$i] -eq 0) { return $false } }
    try {
        [void](New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes)
    }
    catch {
        return $false
    }
    return $true
}

function Get-FileRank {
    param([string]$Path)
    switch -Wildcard ($Path) {
        'LICENSE' { return 0 }
        'LICENSE.*' { return 0 }
        'COPYING' { return 0 }
        '.gitignore' { return 0 }
        '.gitattributes' { return 0 }
        '.editorconfig' { return 0 }
        'AUTHORS' { return 0 }
        'NOTICE' { return 0 }
        'CODE_OF_CONDUCT.md' { return 0 }
        'SECURITY.md' { return 0 }
        'CONTRIBUTING.md' { return 0 }
        'GOVERNANCE.md' { return 0 }
        '.github/*' { return 10 }
        'pyproject.toml' { return 10 }
        'setup.py' { return 10 }
        'setup.cfg' { return 10 }
        'package.json' { return 10 }
        'Cargo.toml' { return 10 }
        'go.mod' { return 10 }
        'Makefile' { return 10 }
        'requirements*.txt' { return 10 }
        '*.lock' { return 10 }
        'poetry.lock' { return 10 }
        'package-lock.json' { return 10 }
        'yarn.lock' { return 10 }
        'README.md' { return 20 }
        'README.rst' { return 20 }
        'README' { return 20 }
        'CHANGELOG.md' { return 90 }
        'CHANGELOG' { return 90 }
        'docs/*' { return 25 }
        'src/*/ui/*' { return 60 }
        'src/*/web/*' { return 60 }
        'src/*/dashboard/*' { return 60 }
        'src/*/static/*' { return 60 }
        'src/*' { return 30 }
        'tests/*' { return 40 }
        'test/*' { return 40 }
        'spec/*' { return 40 }
        'scripts/*' { return 50 }
        'examples/*' { return 50 }
        'fixtures/*' { return 50 }
        '*.md' { return 25 }
        default { return 35 }
    }
}

function Get-TypeBump {
    param([string]$Type)
    switch ($Type) {
        'fix' { return 50 }
        'refactor' { return 60 }
        'perf' { return 70 }
        default { return 0 }
    }
}

function Get-Scope {
    param([string]$Path)
    $parts = $Path -split '/'
    $base = $parts[-1]
    $stem = $base -replace '\.[^.]*$', ''
    if ($Path -like '*/ui/*') { return 'ui' }
    if ($base -eq '__init__.py') { return (ConvertTo-Slug -Value $parts[-2]) }
    if ($Path -like 'tests/*' -or $Path -like 'test/*' -or $Path -like 'spec/*') {
        return (ConvertTo-Slug -Value ($stem -replace '^test_', ''))
    }
    if ($Path -like 'docs/*') {
        $dir = $parts[-2]
        if ($parts.Count -eq 2) { return (ConvertTo-Slug -Value $stem) }
        return (ConvertTo-Slug -Value $dir)
    }
    return (ConvertTo-Slug -Value $stem)
}

function Get-LicenseName {
    param([string]$BlobPath)
    $head = ''
    try {
        $bytes = [System.IO.File]::ReadAllBytes($BlobPath)
        $take = [Math]::Min(4096, $bytes.Length)
        $head = (New-Object System.Text.UTF8Encoding($false)).GetString($bytes, 0, $take)
    }
    catch { return 'project' }
    if ($head -match '(?i)apache license') { return 'Apache-2.0' }
    if ($head -match '(?i)mit license') { return 'MIT' }
    if ($head -match '(?i)gnu general public license') { return 'GPL-3.0' }
    if ($head -match '(?i)redistribution and use in source and binary forms') { return 'BSD' }
    return 'project'
}

# Resolve-Header sets $script:CtType / CtScope / CtVerb / CtObj.
function Resolve-Header {
    param([string]$Path, [string]$Block, [string]$BlobPath)
    $base = Split-Path -Leaf $Path
    $stem = ConvertTo-Slug -Value $base
    $stem = $stem -replace '\.[^.]*$', ''
    $scope = Get-Scope -Path $Path

    switch -Wildcard ($Path) {
        'README.md' { $script:CtType = 'docs'; $script:CtScope = ''; $script:CtVerb = 'describe'; $script:CtObj = 'the project, its guarantees, and the quick-start path'; return }
        'README.rst' { $script:CtType = 'docs'; $script:CtScope = ''; $script:CtVerb = 'describe'; $script:CtObj = 'the project, its guarantees, and the quick-start path'; return }
        'README' { $script:CtType = 'docs'; $script:CtScope = ''; $script:CtVerb = 'describe'; $script:CtObj = 'the project, its guarantees, and the quick-start path'; return }
        'LICENSE' { $script:CtType = 'chore'; $script:CtScope = ''; $script:CtVerb = 'add'; $script:CtObj = "the $(Get-LicenseName -BlobPath $BlobPath) license"; return }
        'LICENSE.*' { $script:CtType = 'chore'; $script:CtScope = ''; $script:CtVerb = 'add'; $script:CtObj = "the $(Get-LicenseName -BlobPath $BlobPath) license"; return }
        'COPYING' { $script:CtType = 'chore'; $script:CtScope = ''; $script:CtVerb = 'add'; $script:CtObj = "the $(Get-LicenseName -BlobPath $BlobPath) license"; return }
        '.gitignore' { $script:CtType = 'chore'; $script:CtScope = ''; $script:CtVerb = 'ignore'; $script:CtObj = 'build output, caches, and local environments'; return }
        '.gitattributes' { $script:CtType = 'chore'; $script:CtScope = ''; $script:CtVerb = 'ignore'; $script:CtObj = 'build output, caches, and local environments'; return }
        '.editorconfig' { $script:CtType = 'chore'; $script:CtScope = ''; $script:CtVerb = 'ignore'; $script:CtObj = 'build output, caches, and local environments'; return }
        '.env.example' { $script:CtType = 'chore'; $script:CtScope = ''; $script:CtVerb = 'document'; $script:CtObj = 'the environment variables'; return }
        '.envrc' { $script:CtType = 'chore'; $script:CtScope = ''; $script:CtVerb = 'document'; $script:CtObj = 'the environment variables'; return }
        'pyproject.toml' { $script:CtType = 'build'; $script:CtScope = ''; $script:CtVerb = 'declare'; $script:CtObj = 'the package, its tooling, and the test dependencies'; return }
        'package.json' { $script:CtType = 'build'; $script:CtScope = ''; $script:CtVerb = 'declare'; $script:CtObj = 'the package manifest and scripts'; return }
        'Makefile' { $script:CtType = 'build'; $script:CtScope = ''; $script:CtVerb = 'add'; $script:CtObj = 'make targets for test, lint, security, and smoke'; return }
        'CHANGELOG.md' { $script:CtType = 'docs'; $script:CtScope = ''; $script:CtVerb = 'start'; $script:CtObj = 'the changelog at 0.1.0'; return }
        'CHANGELOG' { $script:CtType = 'docs'; $script:CtScope = ''; $script:CtVerb = 'start'; $script:CtObj = 'the changelog at 0.1.0'; return }
        '.github/workflows/*' { $script:CtType = 'ci'; $script:CtScope = $stem; $script:CtVerb = 'add'; $script:CtObj = "the $stem workflow"; return }
        '.github/*' { $script:CtType = 'chore'; $script:CtScope = ''; $script:CtVerb = 'add'; $script:CtObj = "the $stem template"; return }
        'CODE_OF_CONDUCT.md' { $script:CtType = 'docs'; $script:CtScope = ''; $script:CtVerb = 'add'; $script:CtObj = 'the code of conduct'; return }
        'CONTRIBUTING.md' { $script:CtType = 'docs'; $script:CtScope = ''; $script:CtVerb = 'add'; $script:CtObj = 'the contributing guide'; return }
        'GOVERNANCE.md' { $script:CtType = 'docs'; $script:CtScope = ''; $script:CtVerb = 'add'; $script:CtObj = 'the governance model'; return }
        'SECURITY.md' { $script:CtType = 'docs'; $script:CtScope = ''; $script:CtVerb = 'add'; $script:CtObj = 'the security policy'; return }
        'docs/*' {
            $headline = Get-FirstHeading -Text $Block
            $script:CtType = 'docs'
            $script:CtScope = ''
            if ($headline) { $script:CtVerb = 'document'; $script:CtObj = $headline }
            else { $script:CtVerb = 'add'; $script:CtObj = "the $stem notes" }
            return
        }
        'tests/__init__.py' { $script:CtType = 'test'; $script:CtScope = ''; $script:CtVerb = 'mark'; $script:CtObj = 'the test package'; return }
        'tests/*/__init__.py' { $script:CtType = 'test'; $script:CtScope = ''; $script:CtVerb = 'mark'; $script:CtObj = 'the test package'; return }
        'test/__init__.py' { $script:CtType = 'test'; $script:CtScope = ''; $script:CtVerb = 'mark'; $script:CtObj = 'the test package'; return }
        'tests/conftest.py' { $script:CtType = 'test'; $script:CtScope = 'fixtures'; $script:CtVerb = 'add'; $script:CtObj = 'shared fixtures and doubles'; return }
        'conftest.py' { $script:CtType = 'test'; $script:CtScope = 'fixtures'; $script:CtVerb = 'add'; $script:CtObj = 'shared fixtures and doubles'; return }
        'tests/*' { $script:CtType = 'test'; $script:CtScope = $scope; $script:CtVerb = 'add'; $script:CtObj = "$scope coverage"; return }
        'test/*' { $script:CtType = 'test'; $script:CtScope = $scope; $script:CtVerb = 'add'; $script:CtObj = "$scope coverage"; return }
        'spec/*' { $script:CtType = 'test'; $script:CtScope = $scope; $script:CtVerb = 'add'; $script:CtObj = "$scope coverage"; return }
        'src/*/__init__.py' { $script:CtType = 'feat'; $script:CtScope = $scope; $script:CtVerb = 'expose'; $script:CtObj = 'the package surface'; return }
        'src/*/__main__.py' { $script:CtType = 'feat'; $script:CtScope = $scope; $script:CtVerb = 'add'; $script:CtObj = 'the module entry point'; return }
        'src/*/ui/*.css' { $script:CtType = 'style'; $script:CtScope = 'ui'; $script:CtVerb = 'add'; $script:CtObj = "the $stem styles"; return }
        'src/*/ui/*' { $script:CtType = 'feat'; $script:CtScope = 'ui'; $script:CtVerb = 'introduce'; $script:CtObj = "the $stem view"; return }
        'src/*' { $script:CtType = 'feat'; $script:CtScope = $scope; $script:CtVerb = 'introduce'; $script:CtObj = "the $scope module"; return }
        'scripts/*' { $script:CtType = 'chore'; $script:CtScope = $stem; $script:CtVerb = 'add'; $script:CtObj = "the $stem helper"; return }
        'examples/*' { $script:CtType = 'chore'; $script:CtScope = $stem; $script:CtVerb = 'add'; $script:CtObj = "the $stem helper"; return }
        'fixtures/*' { $script:CtType = 'chore'; $script:CtScope = $stem; $script:CtVerb = 'add'; $script:CtObj = "the $stem helper"; return }
    }
    $script:CtType = 'chore'; $script:CtScope = ''; $script:CtVerb = 'add'; $script:CtObj = $Path
}

# Resolve-Block sets the same four variables for a non-header block.
function Resolve-Block {
    param([string]$Path, [string]$Block)
    $ext = [System.IO.Path]::GetExtension($Path).TrimStart('.').ToLowerInvariant()
    $scope = Get-Scope -Path $Path
    $script:CtScope = $scope
    $script:CtVerb = 'add'
    $script:CtType = 'feat'
    $script:CtObj = ''

    if ($ext -eq 'md') {
        $headline = Get-FirstHeading -Text $Block
        $script:CtType = 'docs'
        $script:CtScope = ''
        if ($headline) { $script:CtVerb = 'document'; $script:CtObj = $headline }
        else { $script:CtType = 'chore'; $script:CtVerb = 'update'; $script:CtObj = 'the notes' }
        return
    }
    if ($ext -eq 'css') {
        $name = Get-Symbol -Text $Block -Ext 'css'
        $script:CtType = 'style'
        $script:CtScope = 'ui'
        if ($name) { $script:CtObj = $name } else { $script:CtObj = 'the layout rules' }
        return
    }

    $name = Get-Symbol -Text $Block -Ext $ext
    if (-not $name) {
        $script:CtType = 'chore'; $script:CtVerb = 'extend'; $script:CtObj = "the $scope module"
        return
    }
    $lname = $name.ToLowerInvariant()

    if ($ext -eq 'py') {
        if ($lname -eq '__main__') { $script:CtType = 'chore'; $script:CtVerb = 'wire'; $script:CtObj = 'the module entry point'; return }
        if ($lname -like 'test_*') {
            $script:CtType = 'test'; $script:CtVerb = 'cover'
            $script:CtObj = ($lname.Substring(5)).Replace('_', ' ')
            return
        }
        if ($lname -match 'cache|memo|batch|index|pool|prefetch') {
            $script:CtType = 'perf'; $script:CtVerb = 'add'; $script:CtObj = $name; return
        }
        if ($lname -match '^_?(validate|check|require|verify|ensure|guard|reject|sanitize|clamp)') {
            $script:CtType = 'fix'; $script:CtVerb = 'harden'; $script:CtObj = $name; return
        }
        if ($lname -match '^_(extract|normalize|coerce|split|parse|resolve|merge|build|render|apply|collect)') {
            $script:CtType = 'refactor'; $script:CtVerb = 'extract'; $script:CtObj = $name; return
        }
        if ($lname.StartsWith('_')) {
            $script:CtType = 'refactor'; $script:CtVerb = 'add'; $script:CtObj = $name; return
        }
    }

    if ($lname -match '^test|spec') {
        $script:CtType = 'test'; $script:CtVerb = 'cover'
        $script:CtObj = (($lname -replace '^test', '') -replace '^_*', '').Replace('_', ' ')
        return
    }
    if ($lname -match 'cache|memo|batch|index') {
        $script:CtType = 'perf'; $script:CtVerb = 'add'; $script:CtObj = $name; return
    }
    if ($lname -match '^(validate|check|verify|guard)') {
        $script:CtType = 'fix'; $script:CtVerb = 'harden'; $script:CtObj = $name; return
    }
    $script:CtType = 'feat'; $script:CtVerb = 'add'; $script:CtObj = $name
}

# ------------------------------------------------------------- block split ---

function Test-BlockStart {
    param([string]$Line, [string]$Ext)
    if ($Line.Length -eq 0) { return $false }
    if ($Line[0] -eq ' ' -or $Line[0] -eq "`t") { return $false }
    switch ($Ext) {
        'py' {
            if ($Line.StartsWith('def ')) { return $true }
            if ($Line.StartsWith('async def ')) { return $true }
            if ($Line.StartsWith('class ')) { return $true }
            if ($Line.StartsWith('if __name__')) { return $true }
            return $false
        }
        'md' {
            if ($Line.StartsWith('# ') -or $Line.StartsWith('## ') -or $Line.StartsWith('### ')) { return $true }
            return $false
        }
        'js' { return (Test-JsBlockStart -Line $Line) }
        'ts' { return (Test-JsBlockStart -Line $Line) }
        'jsx' { return (Test-JsBlockStart -Line $Line) }
        'tsx' { return (Test-JsBlockStart -Line $Line) }
        'css' {
            if ($Line.StartsWith('@media ') -or $Line.StartsWith('@keyframes ') -or $Line.StartsWith(':root') -or $Line.StartsWith('--')) { return $true }
            if ($Line.EndsWith('{') -and $Line.Length -gt 1) {
                return [char]::IsLetter($Line[0]) -or $Line[0] -eq '.' -or $Line[0] -eq '#'
            }
            return $false
        }
        'go' { return (Test-CompiledBlockStart -Line $Line) }
        'rs' { return (Test-CompiledBlockStart -Line $Line) }
        'java' { return (Test-CompiledBlockStart -Line $Line) }
        default { return $false }
    }
}

function Test-JsBlockStart {
    param([string]$Line)
    foreach ($p in @('function ', 'async function ', 'export ', 'class ', 'const ', 'let ', 'var ', 'import ', 'type ', 'interface ')) {
        if ($Line.StartsWith($p)) { return $true }
    }
    return $false
}

function Test-CompiledBlockStart {
    param([string]$Line)
    foreach ($p in @('func ', 'pub fn ', 'pub struct ', 'fn ', 'impl ', 'struct ', 'enum ', 'public ', 'private ', 'protected ')) {
        if ($Line.StartsWith($p)) { return $true }
    }
    return $false
}

function Split-FileBlocks {
    # Mirrors split_file: split at block starts, merge up to MinLines, cap MaxBlocks.
    param([string]$Text, [string]$Ext)
    if ($Text.Length -eq 0) { return , @('') }

    $hadNl = $Text.EndsWith("`n")
    $blocks = New-Object System.Collections.Generic.List[string]
    $cur = ''
    $fence = 0

    foreach ($raw in (Split-LinesWithEndings -Text $Text)) {
        $line = Get-LineText -Line $raw
        if ($Ext -eq 'md' -and $line.StartsWith('```')) { $fence = 1 - $fence }
        if ($cur.Length -gt 0 -and $fence -eq 0 -and (Test-BlockStart -Line $line -Ext $Ext)) {
            $blocks.Add($cur)
            $cur = ''
        }
        $cur += $raw
    }
    if (-not $hadNl -and $cur.EndsWith("`n")) { $cur = $cur.Substring(0, $cur.Length - 1) }
    $blocks.Add($cur)

    $merged = New-Object System.Collections.Generic.List[string]
    $cur = ''
    foreach ($b in $blocks) {
        $cur += $b
        if (($cur.Split([char]10).Count - 1) -ge $MinLines) { $merged.Add($cur); $cur = '' }
    }
    if ($cur.Length -gt 0) { $merged.Add($cur) }
    if ($merged.Count -eq 0) { $merged.Add('') }

    if ($merged.Count -gt $MaxBlocks) {
        $head = New-Object System.Collections.Generic.List[string]
        for ($i = 0; $i -lt $MaxBlocks - 1; $i++) { $head.Add($merged[$i]) }
        $tail = ''
        for ($i = $MaxBlocks - 1; $i -lt $merged.Count; $i++) { $tail += $merged[$i] }
        $head.Add($tail)
        $merged = $head
    }
    return , $merged.ToArray()
}

# --------------------------------------------------------------- unit build ---

$script:UPath = New-Object System.Collections.Generic.List[string]
$script:UMode = New-Object System.Collections.Generic.List[string]
$script:UFidx = New-Object System.Collections.Generic.List[int]
$script:UBidx = New-Object System.Collections.Generic.List[int]
$script:UType = New-Object System.Collections.Generic.List[string]
$script:UScope = New-Object System.Collections.Generic.List[string]
$script:UVerb = New-Object System.Collections.Generic.List[string]
$script:UObj = New-Object System.Collections.Generic.List[string]
$script:URank = New-Object System.Collections.Generic.List[int]
$script:UHeader = New-Object System.Collections.Generic.List[int]
$script:FPath = New-Object System.Collections.Generic.List[string]
$script:FMode = New-Object System.Collections.Generic.List[string]
$script:FSha = New-Object System.Collections.Generic.List[string]
$script:FBlocks = New-Object System.Collections.Generic.List[object]
$script:FFlags = New-Object System.Collections.Generic.List[string]
$script:TempDir = $null

function Build-Units {
    $script:TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("rebuild-history-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    [void](New-Item -ItemType Directory -Path $script:TempDir)

    $raw = Invoke-GitText -GitArgs @('ls-tree', '-r', '-z', 'HEAD')
    $records = @()
    if ($raw.Length -gt 0) { $records = $raw.Split([char]0) }

    $fidx = 0
    foreach ($rec in $records) {
        if (-not $rec) { continue }
        $tab = $rec.IndexOf("`t")
        if ($tab -lt 0) { continue }
        $meta = $rec.Substring(0, $tab)
        $path = $rec.Substring($tab + 1)
        $sp = $meta.IndexOf(' ')
        $mode = $meta.Substring(0, $sp)
        $sha = $meta.Substring($meta.LastIndexOf(' ') + 1)

        $script:FPath.Add($path)
        $script:FMode.Add($mode)
        $script:FSha.Add($sha)

        $blob = Join-Path $script:TempDir 'blob'
        $blobBytes = Invoke-BlobBytes -Sha $sha -Target $blob

        $ext = [System.IO.Path]::GetExtension($path).TrimStart('.').ToLowerInvariant()
        $blocks = New-Object System.Collections.Generic.List[byte[]]
        if ($NoSplit -or -not (Test-SplittableExt -Ext $ext) -or -not (Test-TextBlob -Path $blob)) {
            $blocks.Add($blobBytes)
        }
        else {
            $enc = New-Object System.Text.UTF8Encoding($false)
            $text = $enc.GetString($blobBytes)
            foreach ($tb in (Split-FileBlocks -Text $text -Ext $ext)) { $blocks.Add($enc.GetBytes($tb)) }
        }
        $script:FBlocks.Add($blocks.ToArray())

        $rank = Get-FileRank -Path $path
        for ($b = 0; $b -lt $blocks.Count; $b++) {
            $hdr = 0
            if ($b -eq 0 -or $NoSplit) { $hdr = 1 }
            $blockText = (New-Object System.Text.UTF8Encoding($false)).GetString($blocks[$b])
            if ($hdr -eq 1) { Resolve-Header -Path $path -Block $blockText -BlobPath $blob }
            else { Resolve-Block -Path $path -Block $blockText }

            $bump = Get-TypeBump -Type $script:CtType
            $script:UPath.Add($path)
            $script:UMode.Add($mode)
            $script:UFidx.Add($fidx)
            $script:UBidx.Add($b)
            $script:UType.Add($script:CtType)
            $script:UScope.Add($script:CtScope)
            $script:UVerb.Add($script:CtVerb)
            $script:UObj.Add($script:CtObj)
            $script:URank.Add($rank + $bump)
            $script:UHeader.Add($hdr)
        }
        $script:FFlags.Add('0' * $blocks.Count)
        $fidx++
    }
    $script:NumFiles = $fidx
    $script:NumUnits = $script:UPath.Count
    if ($script:NumUnits -eq 0) { Stop-WithError 'HEAD tree has no files to replay' }
}

function Invoke-BlobBytes {
    param([string]$Sha, [string]$Target)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'git'
    $psi.Arguments = "cat-file blob $Sha"
    $psi.RedirectStandardOutput = $true
    $psi.UseShellExecute = $false
    $p = [System.Diagnostics.Process]::Start($psi)
    $ms = New-Object System.IO.MemoryStream
    $p.StandardOutput.BaseStream.CopyTo($ms)
    $p.WaitForExit()
    if ($p.ExitCode -ne 0) { throw "git cat-file blob $Sha failed" }
    $bytes = $ms.ToArray()
    [System.IO.File]::WriteAllBytes($Target, $bytes)
    return , $bytes
}

# ---------------------------------------------------------------- ordering ---

function Sort-Units {
    $rows = @()
    for ($i = 0; $i -lt $script:NumUnits; $i++) {
        $rows += [pscustomobject]@{
            Rank = $script:URank[$i]; Fidx = $script:UFidx[$i]; Bidx = $script:UBidx[$i]; Idx = $i
        }
    }
    $script:Order = @($rows | Sort-Object Rank, Fidx, Bidx, Idx | ForEach-Object { $_.Idx })
}

# ---------------------------------------------------------------- grouping ---

function Compose-Message {
    param([int[]]$Units)
    $first = $Units[0]
    $type = $script:UType[$first]
    $scope = $script:UScope[$first]
    $verb = $script:UVerb[$first]
    if ($scope -eq $type) { $scope = '' }

    $objs = @()
    foreach ($u in $Units) { $objs += $script:UObj[$u] }
    if ($objs.Count -eq 1) {
        if ($scope) { return "$type($scope): $verb $($objs[0])" }
        return "${type}: $verb $($objs[0])"
    }
    $list = @()
    for ($i = 0; $i -lt $objs.Count -and $i -lt 6; $i++) { $list += $objs[$i] }
    $joined = ($list -join ', ')
    if ($objs.Count -gt 6) { $joined += ", and $($objs.Count - 6) more" }
    if ($scope) { return "$type($scope): $verb $joined" }
    return "${type}: $verb $joined"
}

function Group-Units {
    param([int]$Target)
    $script:CommitUnits = New-Object System.Collections.Generic.List[int[]]
    $script:CommitMsg = New-Object System.Collections.Generic.List[string]

    $i = 0
    $c = 0
    while ($i -lt $script:NumUnits) {
        $u = $script:Order[$i]
        $fidx0 = $script:UFidx[$u]
        $type0 = $script:UType[$u]
        $leftUnits = $script:NumUnits - $i
        $leftCommits = $Target - $c
        if ($leftCommits -lt 1) { $leftCommits = 1 }
        $take = [int][Math]::Ceiling($leftUnits / [double]$leftCommits)

        if ($script:UHeader[$u] -eq 1) {
            $take = 1
        }
        else {
            $run = 1
            while ($run -lt $take) {
                $j = $i + $run
                if ($j -ge $script:NumUnits) { break }
                $cand = $script:Order[$j]
                if ($script:UFidx[$cand] -ne $fidx0) { break }
                if ($script:UType[$cand] -ne $type0) { break }
                if ($script:UHeader[$cand] -ne 0) { break }
                $run++
            }
            if ($take -gt $run) { $take = $run }
        }
        if ($take -lt 1) { $take = 1 }

        $units = @()
        for ($k = 0; $k -lt $take; $k++) { $units += $script:Order[$i + $k] }
        $script:CommitUnits.Add($units)
        $script:CommitMsg.Add((Compose-Message -Units $units))
        $i += $take
        $c++
    }

    $script:NumCommits = $c
    if ($script:NumCommits -eq 0) { Stop-WithError 'nothing to commit' }
    $script:CommitMsg[0] = 'chore: initial commit'
}

# ---------------------------------------------------------------- calendar ---

function Get-Dow {
    param([int]$DayNumber)
    # 0 = Sunday, mirroring bash's dow_of.
    $d = [datetime]'1970-01-01' + [timespan]::FromDays($DayNumber)
    return [int]$d.DayOfWeek
}

function Get-DayString {
    param([int]$DayNumber)
    return ([datetime]'1970-01-01' + [timespan]::FromDays($DayNumber)).ToString('yyyy-MM-dd')
}

function Get-MondayOfWeek {
    param([int]$DayNumber)
    return $DayNumber - ((Get-Dow -DayNumber $DayNumber) + 6) % 7
}

# Get-SampleTime sets $script:Tmin.
function Get-SampleTime {
    param([int]$Dow)
    Get-Rnd 1000
    $p = $script:Rnd
    if ($Dow -eq 0 -or $Dow -eq 6) {
        if ($p -lt 330) { Get-Rnd 280; $script:Tmin = 790 + $script:Rnd }
        elseif ($p -lt 800) { Get-Rnd 240; $script:Tmin = 1090 + $script:Rnd }
        else { Get-Rnd 180; $script:Tmin = 1340 + $script:Rnd }
    }
    else {
        if ($p -lt 100) { Get-Rnd 160; $script:Tmin = 520 + $script:Rnd }
        elseif ($p -lt 440) { Get-Rnd 270; $script:Tmin = 790 + $script:Rnd }
        elseif ($p -lt 820) { Get-Rnd 240; $script:Tmin = 1090 + $script:Rnd }
        else { Get-Rnd 180; $script:Tmin = 1340 + $script:Rnd }
    }
}

function Build-Calendar {
    param([int]$Target, [int]$StartDay, [int]$EndDay, [int]$NowDay, [int]$NowMin)

    $startMon = Get-MondayOfWeek -DayNumber $StartDay
    $endMon = Get-MondayOfWeek -DayNumber $EndDay
    $weeks = [int](Get-IntDiv -A ($endMon - $startMon) -B 7) + 1

    $wW = @()
    $wCap = @()
    $wState = @()
    $state = 'sprint'
    for ($i = 0; $i -lt $weeks; $i++) {
        switch ($state) {
            'sprint' { $w = 420; $cap = 6 }
            'normal' { $w = 150; $cap = 3 }
            'quiet' { $w = 55; $cap = 1 }
            default { $w = 0; $cap = 0 }
        }
        Get-Rnd 61
        $jitter = 70 + $script:Rnd
        $w = [int](Get-IntDiv -A ($w * $jitter) -B 100)

        $mid = $startMon + $i * 7 + 3
        $midd = Get-DayString -DayNumber $mid
        $mm = [int]$midd.Substring(5, 2)
        $dd = [int]$midd.Substring(8, 2)
        if (($mm -eq 12 -and $dd -ge 20) -or ($mm -eq 1 -and $dd -le 3)) {
            $w = [int](Get-IntDiv -A ($w * 30) -B 100)
        }

        $wState += $state
        $wW += $w
        $wCap += $cap

        Get-Rnd 100
        $r = $script:Rnd
        switch ($state) {
            'sprint' {
                if ($r -lt 45) { $state = 'sprint' }
                elseif ($r -lt 80) { $state = 'normal' }
                elseif ($r -lt 95) { $state = 'quiet' }
                else { $state = 'off' }
            }
            'normal' {
                if ($r -lt 15) { $state = 'sprint' }
                elseif ($r -lt 65) { $state = 'normal' }
                elseif ($r -lt 90) { $state = 'quiet' }
                else { $state = 'off' }
            }
            'quiet' {
                if ($r -lt 10) { $state = 'sprint' }
                elseif ($r -lt 55) { $state = 'normal' }
                elseif ($r -lt 90) { $state = 'quiet' }
                else { $state = 'off' }
            }
            'off' {
                if ($r -lt 8) { $state = 'sprint' }
                elseif ($r -lt 55) { $state = 'normal' }
                elseif ($r -lt 80) { $state = 'quiet' }
                else { $state = 'off' }
            }
        }
    }
    if ($wCap[$weeks - 1] -lt 1) { $wCap[$weeks - 1] = 1 }
    if ($wW[$weeks - 1] -lt 60) { $wW[$weeks - 1] = 60 }

    $wsum = 0
    foreach ($x in $wW) { $wsum += $x }
    if ($wsum -le 0) { $wsum = 1 }

    $wCount = @()
    $rem = @()
    $left = $Target
    for ($i = 0; $i -lt $weeks; $i++) {
        $base = [int](Get-IntDiv -A ($wW[$i] * $Target) -B $wsum)
        if ($base -gt $wCap[$i]) { $base = $wCap[$i] }
        $wCount += $base
        $left -= $base
        $rem += ($wW[$i] * $Target % $wsum)
    }
    while ($left -gt 0) {
        $best = -1
        $bestRem = -1
        for ($i = 0; $i -lt $weeks; $i++) {
            if ($wCount[$i] -ge $wCap[$i]) { continue }
            if ($rem[$i] -gt $bestRem) { $best = $i; $bestRem = $rem[$i] }
        }
        if ($best -lt 0) { break }
        $wCount[$best]++
        $rem[$best] = -1
        $left--
    }
    while ($left -gt 0) {
        for ($i = $weeks - 1; $i -ge 0 -and $left -gt 0; $i--) {
            if ($wCount[$i] -le 0) { continue }
            $wCount[$i]++
            $left--
        }
    }

    $slots = New-Object System.Collections.Generic.List[int]
    for ($i = 0; $i -lt $weeks; $i++) {
        if ($wCount[$i] -le 0) { continue }
        $dw = @(1250, 1200, 1150, 1100, 900, 140, 100)
        for ($d = 0; $d -lt 7; $d++) {
            $day = $startMon + $i * 7 + $d
            if ($day -lt $StartDay -or $day -gt $EndDay) { $dw[$d] = 0 }
        }
        $local = New-Object System.Collections.Generic.List[int]
        for ($k = 0; $k -lt $wCount[$i]; $k++) {
            $script:PwArr = $dw
            Get-WeightedPick
            $d = $script:PwPick
            $dw[$d] = 0
            $day = $startMon + $i * 7 + $d
            if ($day -lt $StartDay) { $day = $StartDay }
            if ($day -gt $EndDay) { $day = $EndDay }
            Get-SampleTime -Dow (Get-Dow -DayNumber $day)
            $local.Add($day * 1440 + $script:Tmin)
            $avail = $false
            for ($j = 0; $j -lt 7; $j++) { if ($dw[$j] -gt 0) { $avail = $true } }
            if (-not $avail) {
                $dw = @(700, 680, 650, 620, 500, 300, 260)
                for ($j = 0; $j -lt 7; $j++) {
                    $day = $startMon + $i * 7 + $j
                    if ($day -lt $StartDay -or $day -gt $EndDay) { $dw[$j] = 0 }
                }
            }
        }
        $prev = -1
        foreach ($abs0 in ($local | Sort-Object)) {
            $abs = $abs0
            if ($prev -ge 0 -and $abs -le $prev) { $abs = $prev + 3 }
            if ($prev -ge 0 -and (Get-IntDiv -A $abs -B 1440) -eq (Get-IntDiv -A $prev -B 1440) -and ($abs - $prev) -lt 40) {
                Get-Rnd 135
                $abs = $prev + 45 + $script:Rnd
            }
            $slots.Add($abs)
            $prev = $abs
        }
    }

    $script:Slot = @($slots | Sort-Object)
    $n = $script:Slot.Count

    $capAbs = $EndDay * 1440 + 17 * 60 + 30
    if ($EndDay -eq $NowDay) {
        $nowAbs = $NowDay * 1440 + $NowMin - 20
        if ($capAbs -gt $nowAbs) { $capAbs = $nowAbs }
    }

    Get-Rnd 90
    $script:Slot[0] = $StartDay * 1440 + 9 * 60 + $script:Rnd
    $prev = $script:Slot[0]
    for ($i = 1; $i -lt $n - 1; $i++) {
        if ($script:Slot[$i] -le $prev) { $script:Slot[$i] = $prev + 3 }
        if ($script:Slot[$i] -gt ($capAbs - 3)) { $script:Slot[$i] = $capAbs - 3 }
        $prev = $script:Slot[$i]
    }
    $script:Slot[$n - 1] = $capAbs
    if ($n -ge 2 -and $script:Slot[$n - 2] -ge ($capAbs - 1)) {
        $script:Slot[$n - 2] = $capAbs - 3
        for ($i = $n - 3; $i -ge 0; $i--) {
            if ($script:Slot[$i] -ge $script:Slot[$i + 1]) { $script:Slot[$i] = $script:Slot[$i + 1] - 3 }
        }
    }

    $script:CommitTs = @()
    for ($i = 0; $i -lt $n; $i++) {
        $dayn = Get-IntDiv -A $script:Slot[$i] -B 1440
        $mn = $script:Slot[$i] % 1440
        $hh = Get-IntDiv -A $mn -B 60
        $mm = $mn % 60
        Get-Rnd 60
        $ss = $script:Rnd
        $date = Get-DayString -DayNumber $dayn
        $script:CommitTs += ('{0}T{1:D2}:{2:D2}:{3:D2}{4}' -f $date, $hh, $mm, $ss, $script:TzOffset)
    }
}

# --------------------------------------------------------------- execution ---

function Invoke-Execute {
    $idx = Join-Path $script:TempDir 'index'
    if (Test-Path $idx) { Remove-Item -Force $idx }
    $env:GIT_INDEX_FILE = $idx
    $parent = ''
    $current = Join-Path $script:TempDir 'current'
    $msgFile = Join-Path $script:TempDir 'msg'

    try {
        for ($i = 0; $i -lt $script:NumCommits; $i++) {
            foreach ($u in $script:CommitUnits[$i]) {
                $f = $script:UFidx[$u]
                $b = $script:UBidx[$u]
                $flags = $script:FFlags[$f].ToCharArray()
                $flags[$b] = '1'
                $script:FFlags[$f] = -join $flags
                $flags = $script:FFlags[$f]

                $ms = New-Object System.IO.MemoryStream
                for ($bb = 0; $bb -lt $script:FBlocks[$f].Count; $bb++) {
                    if ($flags[$bb] -eq '1') {
                        $chunk = $script:FBlocks[$f][$bb]
                        $ms.Write($chunk, 0, $chunk.Length)
                    }
                }
                [System.IO.File]::WriteAllBytes($current, $ms.ToArray())
                $sha = (Invoke-GitText -GitArgs @('hash-object', '-w', '--no-filters', '--', $current))
                Invoke-Git -GitArgs @('update-index', '--add', '--cacheinfo', "$($script:FMode[$f]),$sha,$($script:FPath[$f])") | Out-Null
            }

            $tree = Invoke-GitText -GitArgs @('write-tree')
            $enc = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllBytes($msgFile, $enc.GetBytes($script:CommitMsg[$i] + "`n"))

            $env:GIT_AUTHOR_NAME = $script:Name
            $env:GIT_AUTHOR_EMAIL = $script:Email
            $env:GIT_COMMITTER_NAME = $script:Name
            $env:GIT_COMMITTER_EMAIL = $script:Email
            $env:GIT_AUTHOR_DATE = $script:CommitTs[$i]
            $env:GIT_COMMITTER_DATE = $script:CommitTs[$i]

            if (-not $parent) {
                $sha = Invoke-GitText -GitArgs @('commit-tree', $tree, '-F', $msgFile)
            }
            else {
                $sha = Invoke-GitText -GitArgs @('commit-tree', $tree, '-p', $parent, '-F', $msgFile)
            }
            $parent = $sha.Trim()
            Write-Note ("  {0}  {1}" -f $script:CommitTs[$i], $script:CommitMsg[$i])
        }
        $script:LastCommit = $parent

        if ($script:BranchExists) {
            Invoke-Git -GitArgs @('update-ref', "refs/heads/$Branch", $script:LastCommit) | Out-Null
        }
        else {
            Invoke-Git -GitArgs @('update-ref', "refs/heads/$Branch", $script:LastCommit, '0000000000000000000000000000000000000000') | Out-Null
        }
    }
    finally {
        Remove-Item Env:GIT_INDEX_FILE -ErrorAction SilentlyContinue
        Remove-Item Env:GIT_AUTHOR_NAME -ErrorAction SilentlyContinue
        Remove-Item Env:GIT_AUTHOR_EMAIL -ErrorAction SilentlyContinue
        Remove-Item Env:GIT_COMMITTER_NAME -ErrorAction SilentlyContinue
        Remove-Item Env:GIT_COMMITTER_EMAIL -ErrorAction SilentlyContinue
        Remove-Item Env:GIT_AUTHOR_DATE -ErrorAction SilentlyContinue
        Remove-Item Env:GIT_COMMITTER_DATE -ErrorAction SilentlyContinue
    }
}

# --------------------------------------------------------------------- plan ---

function Write-Plan {
    for ($i = 0; $i -lt $script:NumCommits; $i++) {
        $files = New-Object System.Collections.Generic.List[string]
        foreach ($u in $script:CommitUnits[$i]) {
            if (-not $files.Contains($script:UPath[$u])) { $files.Add($script:UPath[$u]) }
        }
        Write-Output ("{0}`t{1}`t{2}" -f $script:CommitTs[$i], $script:CommitMsg[$i], ($files -join ','))
    }
}

function Write-Stats {
    $seen = New-Object System.Collections.Generic.List[int]
    for ($i = 0; $i -lt $script:NumCommits; $i++) {
        $day = Get-IntDiv -A $script:Slot[$i] -B 1440
        $mon = Get-MondayOfWeek -DayNumber $day
        if (-not $seen.Contains($mon)) { $seen.Add($mon) }
    }
    $types = New-Object System.Collections.Generic.List[string]
    $counts = New-Object System.Collections.Generic.List[int]
    for ($i = 0; $i -lt $script:NumCommits; $i++) {
        $type = $script:CommitMsg[$i].Split(':')[0]
        $pos = $types.IndexOf($type)
        if ($pos -lt 0) { $types.Add($type); $counts.Add(1) }
        else { $counts[$pos]++ }
    }
    $summary = @()
    for ($i = 0; $i -lt $types.Count; $i++) { $summary += "$($types[$i])=$($counts[$i])" }

    Write-Info ''
    Write-Info "commits:   $($script:NumCommits)"
    Write-Info "files:     $($script:NumFiles)"
    Write-Info "range:     $($script:CommitTs[0]) .. $($script:CommitTs[$script:NumCommits - 1])"
    Write-Info "weeks:     $($seen.Count) active"
    Write-Info "types:     $($summary -join ' ')"
}

# --------------------------------------------------------------------- main ---

if (-not $Branch -or $Branch -eq 'HEAD') { Stop-WithError 'the branch name must not be empty or HEAD' }
if ($Branch.StartsWith('refs/')) { Stop-WithError 'Branch takes a short name, not a full ref' }
if ($MinLines -lt 1) { $MinLines = 1 }
if ($MaxBlocks -lt 1) { $MaxBlocks = 1 }

$densityMilli = [int][Math]::Round($Density * 1000)
if ($densityMilli -le 0) { Stop-WithError '-Density must be greater than zero' }

Reset-Rng -NewSeed ([long]$Seed)
$script:Branch = $Branch

if (-not (Test-Path $Repo)) { Stop-WithError "cannot enter $Repo" }
Push-Location $Repo
try {
    $root = Invoke-GitText -GitArgs @('rev-parse', '--show-toplevel') -AllowFail
    if (-not $root) { Stop-WithError 'not a git repository' }
    Set-Location $root
    $script:RepoRoot = $root

    $headOk = [bool](Invoke-GitText -GitArgs @('rev-parse', '--verify', '--quiet', 'HEAD') -AllowFail)
    if (-not $headOk) { Stop-WithError 'HEAD has no commits; nothing to replay' }

    $script:Name = ''
    $script:Email = ''
    if ($Author) {
        if ($Author -notmatch '^(.*?)\s*<([^>]*)>$') { Stop-WithError '-Author must look like "Name <email>"' }
        $script:Name = $Matches[1]
        $script:Email = $Matches[2]
    }
    else {
        $script:Name = (Invoke-GitText -GitArgs @('config', 'user.name') -AllowFail)
        $script:Email = (Invoke-GitText -GitArgs @('config', 'user.email') -AllowFail)
    }
    if (-not $script:Name -or -not $script:Email) {
        Stop-WithError "set git user.name/user.email or pass -Author 'Name <email>'"
    }

    if (-not $Tz) {
        $off = [DateTimeOffset]::Now.Offset
        $sign = if ($off.Ticks -lt 0) { '-' } else { '+' }
        $Tz = '{0}{1:D2}{2:D2}' -f $sign, [Math]::Abs($off.Hours), [Math]::Abs($off.Minutes)
    }
    if ($Tz -notmatch '^[+-]\d{4}$') { Stop-WithError '-Tz must look like +0200 or -0700' }
    $script:TzOffset = $Tz

    $today = (Get-Date).ToString('yyyy-MM-dd')
    if (-not $EndDate) { $EndDate = $today }

    foreach ($d in @($StartDate, $EndDate, $today)) {
        if ($d -notmatch '^\d{4}-\d{2}-\d{2}$') { Stop-WithError "expected YYYY-MM-DD, got: $d" }
    }

    $epoch = [datetime]'1970-01-01'
    $startDay = [int](([datetime]::ParseExact($StartDate, 'yyyy-MM-dd', $null)) - $epoch).TotalDays
    $endDay = [int](([datetime]::ParseExact($EndDate, 'yyyy-MM-dd', $null)) - $epoch).TotalDays
    $nowDay = [int](([datetime]::ParseExact($today, 'yyyy-MM-dd', $null)) - $epoch).TotalDays
    $nowMin = (Get-Date).Hour * 60 + (Get-Date).Minute
    if ($startDay -gt $endDay) { Stop-WithError '-StartDate is after -EndDate' }

    $script:BranchExists = $false
    $ref = "refs/heads/$Branch"
    $existing = Invoke-GitText -GitArgs @('rev-parse', '--verify', '--quiet', $ref) -AllowFail
    if ($existing) {
        $script:BranchExists = $true
        if (-not $Force -and -not $DryRun) {
            Stop-WithError "branch $Branch already exists; pass -Force to rewrite it"
        }
    }

    $headTree = Invoke-GitText -GitArgs @('rev-parse', 'HEAD^{tree}')

    if (-not $DryRun) {
        Write-Info $script:Prog
        Write-Info "repository: $script:RepoRoot"
        Write-Info "identity:   $($script:Name) <$($script:Email)>  tz $Tz"
    }

    try {
        Build-Units
        Sort-Units

        $weeksSpan = [int](Get-IntDiv -A ($endDay - $startDay) -B 7) + 1
        $target = [int](Get-IntDiv -A ($weeksSpan * $densityMilli) -B 1000)
        if ($target -gt $script:NumUnits) { $target = $script:NumUnits }
        if ($target -lt 8) { $target = 8 }
        if ($target -gt $script:NumUnits) { $target = $script:NumUnits }

        Group-Units -Target $target
        Build-Calendar -Target $script:NumCommits -StartDay $startDay -EndDay $endDay -NowDay $nowDay -NowMin $nowMin

        if ($DryRun) {
            Write-Plan
            return
        }

        Write-Info "units:      $($script:NumUnits)"
        Write-Info "commits:    $($script:NumCommits) over $weeksSpan weeks"

        Invoke-Execute

        $finalTree = Invoke-GitText -GitArgs @('rev-parse', "$Branch^{tree}")
        if ($finalTree -ne $headTree) {
            Stop-WithError "final tree $finalTree does not match HEAD tree $headTree"
        }

        Write-Stats

        Write-Info ''
        Write-Info "wrote branch $Branch"
        Write-Info "final tree matches HEAD ($headTree)"
        Write-Info ''
        Write-Info 'adopt it with:'
        Write-Info "  git checkout $Branch"
        Write-Info '  git branch -M main          # optional, replaces the current history'
    }
    finally {
        if ($script:TempDir -and (Test-Path $script:TempDir)) {
            Remove-Item -Recurse -Force $script:TempDir -ErrorAction SilentlyContinue
        }
    }
}
finally {
    Pop-Location
}
