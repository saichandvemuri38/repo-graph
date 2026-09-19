#requires -Version 7.0
<#
  Atlas.Template: a tiny template engine for the files in .claude/templates.
    {{name}}  {{a.b}}                      value (dotted paths into hashtables)
    {{#if name}} ... {{/if}}               shown when the value is non-empty (not '', $null, 0, $false or an empty list)
    {{#each list}} ... {{/each}}           repeats; inside, {{.}} is the item, or {{prop}} for a hashtable item
  Unknown names render as nothing. Blocks do not nest.
#>
Set-StrictMode -Version Latest

function Get-Value {
    param($Data, [string]$Path)
    $cur = $Data
    foreach ($part in $Path.Split('.')) {
        if ($null -eq $cur) { return $null }
        if ($cur -is [System.Collections.IDictionary]) { $cur = if ($cur.Contains($part)) { $cur[$part] } else { $null } }
        else {
            $prop = $cur.PSObject.Properties[$part]
            $cur = if ($prop) { $prop.Value } else { $null }
        }
    }
    $cur
}

function Test-Truthy {
    param($Value)
    if ($null -eq $Value) { return $false }
    if ($Value -is [bool]) { return $Value }
    if ($Value -is [string]) { return $Value.Length -gt 0 }
    if ($Value -is [System.Collections.IDictionary]) { return $Value.Count -gt 0 }
    if ($Value -is [System.Collections.IEnumerable]) { return @($Value).Count -gt 0 }
    if ($Value -is [ValueType]) { return $Value -ne 0 }
    $true
}

function Format-Value {
    param($Value)
    if ($null -eq $Value) { return '' }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string] -and $Value -isnot [System.Collections.IDictionary]) { return (@($Value) -join ', ') }
    [string]$Value
}

function Expand-TemplateText {
    param([Parameter(Mandatory)][string]$Template, [Parameter(Mandatory)]$Data)
    $text = [regex]::Replace($Template, '(?s)\{\{#each ([\w\.]+)\}\}\r?\n?(.*?)\{\{/each\}\}\r?\n?', {
            param($m)
            $items = Get-Value $Data $m.Groups[1].Value
            if ($null -eq $items) { return '' }
            $body = $m.Groups[2].Value
            $sb = [System.Text.StringBuilder]::new()
            foreach ($item in @($items)) {
                $line = [regex]::Replace($body, '\{\{([\w\.]+)\}\}', {
                        param($v)
                        $name = $v.Groups[1].Value
                        if ($name -eq '.') { return (Format-Value $item) }
                        Format-Value (Get-Value $item $name)
                    })
                [void]$sb.Append($line)
            }
            $sb.ToString()
        })
    $text = [regex]::Replace($text, '(?s)\{\{#if ([\w\.]+)\}\}\r?\n?(.*?)\{\{/if\}\}\r?\n?', {
            param($m)
            if (Test-Truthy (Get-Value $Data $m.Groups[1].Value)) { $m.Groups[2].Value } else { '' }
        })
    [regex]::Replace($text, '\{\{([\w\.]+)\}\}', { param($m) Format-Value (Get-Value $Data $m.Groups[1].Value) })
}

function Expand-Template {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)]$Data)
    $file = Join-Path (Join-Path $PSScriptRoot '../templates') $Name
    if (-not (Test-Path $file)) { throw "Template not found: $file" }
    Expand-TemplateText -Template (Get-Content -Raw -Path $file -Encoding utf8) -Data $Data
}

Export-ModuleMember -Function Expand-Template, Expand-TemplateText
