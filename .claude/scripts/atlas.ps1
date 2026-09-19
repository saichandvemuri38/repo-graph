#requires -Version 7.0
<#
  Runs a CodeAtlas engine command against this repo, with output streamed live.
    pwsh -NoProfile -File .claude/scripts/atlas.ps1 <command> [arguments]
  Examples: index | status | search "user login" --graph | impact Foo.bar --json | changes | taint | unused | web --open | serve
  `serve` is what .mcp.json starts for Claude Code.
#>
Import-Module (Join-Path $PSScriptRoot '../lib/Atlas.Core.psm1') -DisableNameChecking

if ($args.Count -eq 0) {
    [Console]::Error.WriteLine('usage: atlas.ps1 <command> [arguments]   (commands: index status search context impact trace changes check locate taint defs unused report publish web projects serve)')
    exit 2
}
try {
    $env:PYTHONPATH = (Get-EnginePath) + [IO.Path]::PathSeparator + $env:PYTHONPATH
    $env:PYTHONUTF8 = '1'
    & (Get-EnginePython) -m atlas_engine --root (Get-AtlasRoot) @args
    exit $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine("atlas.ps1: $_")
    exit 1
}
