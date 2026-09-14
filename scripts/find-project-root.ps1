<#
.SYNOPSIS
  Walk up from a starting directory to find the nearest Akela project context root.

.DESCRIPTION
  Looks for `akela.json` first (strong signal: this directory is an Akela context root) —
  the nearest ancestor containing it wins.

  If no `akela.json` is found anywhere up the chain, falls back to the first MARKER TYPE
  that appears anywhere among the ancestors, checked in this priority order: AGENTS.md,
  CLAUDE.md, .git, README.md. Priority is evaluated across the whole chain per marker type
  (not per directory), so a `.git` sitting deep inside a project (e.g. a public "auto/"
  sub-repository) never outranks an AGENTS.md/CLAUDE.md that sits higher up at the real
  project root — only if NO ancestor has AGENTS.md or CLAUDE.md does a `.git` win.

  No path in this script is hardcoded to any machine or workspace location, so it works
  identically whether run from inside the workspace or from a standalone clone elsewhere.

.PARAMETER StartPath
  Directory to start searching from. Defaults to the current directory.

.EXAMPLE
  powershell -File scripts/find-project-root.ps1
  powershell -File scripts/find-project-root.ps1 -StartPath "C:\path\to\project\auto\scripts"
#>
param(
    [string]$StartPath = (Get-Location).Path
)

# Collect the chain of ancestor directories, nearest first.
$chain = @()
$dir = Get-Item -LiteralPath $StartPath
while ($dir) {
    $chain += $dir.FullName
    $parent = $dir.Parent
    if (-not $parent -or $parent.FullName -eq $dir.FullName) { break }
    $dir = $parent
}

# 1) akela.json is the strongest signal: nearest ancestor wins.
foreach ($d in $chain) {
    if (Test-Path -LiteralPath (Join-Path $d "akela.json")) {
        Write-Output $d
        exit 0
    }
}

# 2) No akela.json anywhere: fall back to the first marker type that appears
#    anywhere in the chain, in priority order.
foreach ($marker in @("AGENTS.md", "CLAUDE.md", ".git", "README.md")) {
    foreach ($d in $chain) {
        if (Test-Path -LiteralPath (Join-Path $d $marker)) {
            Write-Output $d
            exit 0
        }
    }
}

Write-Output $StartPath
exit 1
