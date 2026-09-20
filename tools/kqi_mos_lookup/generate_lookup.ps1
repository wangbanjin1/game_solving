param(
    [string]$Output = "outputs/9_20_1/KQI_MOS带宽查表_v1.xlsx",
    [string]$PreviewDir = "outputs/9_20_1/KQI_MOS带宽查表_v1_previews"
)

$ErrorActionPreference = "Stop"
$runtimeRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime"
$nodeExe = Join-Path $runtimeRoot "dependencies\node\bin\node.exe"
$nodeModules = Join-Path $runtimeRoot "dependencies\node\node_modules"
$localModules = Join-Path $PSScriptRoot "node_modules"

if (-not (Test-Path -LiteralPath $nodeExe)) {
    throw "未找到 Codex 工作区 Node.js：$nodeExe"
}
if (-not (Test-Path -LiteralPath $nodeModules)) {
    throw "未找到 @oai/artifact-tool 依赖目录：$nodeModules"
}
if (-not (Test-Path -LiteralPath $localModules)) {
    New-Item -ItemType Junction -Path $localModules -Target $nodeModules | Out-Null
}

& $nodeExe (Join-Path $PSScriptRoot "generate_lookup.mjs") `
    --output $Output `
    --preview-dir $PreviewDir
exit $LASTEXITCODE
