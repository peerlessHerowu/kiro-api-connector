\xef\xbb\xbfparam([switch]$Update, [switch]$Restart)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if ($Update) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw '更新源码需要 Git；也可以重新下载最新源码包。' }
  $changes = git status --porcelain
  if ($LASTEXITCODE -ne 0) { throw '当前目录不是可更新的 Git 仓库。' }
  if ($changes) { throw '源码目录有未提交修改，请先处理后再更新。' }
  git pull --ff-only
  if ($LASTEXITCODE -ne 0) { throw '源码更新失败，未切换运行中的服务。请检查网络和仓库权限。' }
  # Execute the newly downloaded script rather than the old in-memory version.
  & $PSCommandPath -Restart
  exit
}

function Find-Python {
  foreach ($name in @('py','python','python3')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) {
      try { & $cmd.Source -c 'import sys; raise SystemExit(sys.version_info < (3,11))' 2>$null; if ($LASTEXITCODE -eq 0) { return $cmd.Source } } catch {}
    }
  }
  return $null
}

$python = Find-Python
if (-not $python) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw '缺少 Python 和 winget。请安装 Windows 应用安装程序，或使用便携包。' }
  winget install --id Python.Python.3.12 --exact --scope user --accept-source-agreements --accept-package-agreements
  if ($LASTEXITCODE -ne 0) { throw 'Python 安装失败。' }
  $env:PATH = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
  $python = Find-Python
  if (-not $python) { throw 'Python 已安装但当前会话无法找到，请重新打开 PowerShell 执行 setup.ps1。' }
}
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
  try { & $node.Source -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<22 || (a===22 && b<17))process.exit(1); require("node:sqlite")' 2>$null } catch {}
  $nodeReady = $LASTEXITCODE -eq 0
} else { $nodeReady = $false }
if (-not $nodeReady) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw '缺少符合要求的 Node 和 winget，请使用便携包或安装 Node 22.17+。' }
  winget install --id OpenJS.NodeJS.LTS --exact --accept-source-agreements --accept-package-agreements
  if ($LASTEXITCODE -ne 0) { throw 'Node 安装失败。' }
  $env:PATH = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
}
& $python -c 'import app; c=app.Connector(app.user_paths()[0]); c.install()'
if ($LASTEXITCODE -ne 0) { throw 'kRouter 依赖安装失败，请查看本机 install.log。' }

Write-Host '正在启动配置向导。已有配置会保留，不会覆盖其他 Kiro 设置。'
if ($Restart) {
  & $python app.py --takeover
} else {
  & $python app.py
}
