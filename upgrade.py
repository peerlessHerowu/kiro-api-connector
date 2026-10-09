"""Update a portable installation's code while preserving its local data."""
import io
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARCHIVE = 'https://github.com/peerlessHerowu/kiro-api-connector/archive/refs/heads/main.zip'
TOP_LEVEL = {'app.py', 'README.md', 'upgrade.py', 'setup.ps1', 'setup.sh',
             '打开配置向导.cmd', '打开配置向导.command', '升级到最新版.cmd'}


def update():
    if not (ROOT / 'runtime/bin/node.exe').exists():
        raise RuntimeError('这是源码目录。源码请执行 git pull --ff-only 后再运行 setup.ps1。')
    with urllib.request.urlopen(ARCHIVE, timeout=60) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        prefix = next((n.split('/', 1)[0] for n in names if n.endswith('/app.py')), None)
        if not prefix or f'{prefix}/runtime/kiro_local_bridge.js' not in names:
            raise RuntimeError('下载的更新包不完整，未修改本机文件。')
        with tempfile.TemporaryDirectory(prefix='kiro-connector-update-') as directory:
            staging = Path(directory)
            for name in names:
                relative = name.split('/', 1)[1] if name.startswith(prefix + '/') else ''
                if relative in TOP_LEVEL or relative == 'web/index.html' or (relative.startswith('runtime/') and Path(relative).suffix in {'.js', '.cjs', '.py'}):
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
            required = [staging / 'app.py', staging / 'runtime/kiro_local_bridge.js', staging / 'runtime/context_policy.cjs']
            if not all(path.exists() for path in required):
                raise RuntimeError('更新包缺少桥接文件，未修改本机文件。')
            for source in staging.rglob('*'):
                if source.is_file():
                    target = ROOT / source.relative_to(staging)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    print('程序文件已更新；本机 API key、数据库、Kiro 设置和日志未修改。请重新打开配置向导。')


if __name__ == '__main__':
    try:
        update()
    except Exception as error:
        print('更新失败：' + str(error))
        raise SystemExit(1)
