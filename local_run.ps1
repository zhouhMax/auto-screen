param(
    [string]$Slot = "close",
    [switch]$NoWait,
    [switch]$DryRun,
    [switch]$Offline
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"

# 从 .env 读取 WxPusher 配置（该文件不提交仓库）
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    Get-Content $envFile -Encoding utf8 | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), "Process")
        }
    }
}

New-Item -ItemType Directory -Force (Join-Path $root "logs") | Out-Null
$log = Join-Path $root ("logs\run_" + $Slot + "_" + (Get-Date -Format "yyyyMMdd") + ".log")
"===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') slot=$Slot nowait=$NoWait dryrun=$DryRun offline=$Offline =====" |
    Out-File $log -Append -Encoding utf8

# 先拉取云端提交的选股结果（data/results），再回填表格
python sync_cloud.py 2>&1 | Out-File $log -Append -Encoding utf8
python sync_results.py 2>&1 | Out-File $log -Append -Encoding utf8

# 用本地通达信日线刷新历史缓存，再选股回填
python tdx.py 2>&1 | Out-File $log -Append -Encoding utf8

$argv = @("main.py", "--slot", $Slot, "--excel")
if ($NoWait) { $argv += "--no-wait" }
if ($DryRun) { $argv += "--dry-run" }
if ($Offline) { $argv += "--offline" }
python @argv 2>&1 | Out-File $log -Append -Encoding utf8

"exit=$LASTEXITCODE" | Out-File $log -Append -Encoding utf8
