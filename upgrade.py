"""Update a portable installation's code while preserving its local data."""
import io
import shutil
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARCHIVE = 'https://github.com/peerlessHerowu/kiro-api-connector/archive/refs/heads/main.zip'
TOP_LEVEL = {'app.py', 'README.md', 'upgrade.py', 'setup.ps1', 'setup.sh',
             'stop.py',
             '打开配置向导.cmd', '打开配置向导.command', '升级到最新版.cmd'}


def download(destination):
    with urllib.request.urlopen(ARCHIVE,timeout=60) as response:
        payload=response.read(32*1024*1024+1)
    if len(payload)>32*1024*1024:raise RuntimeError('更新包超过32MB，未应用。')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        prefix=next((n.split('/',1)[0] for n in archive.namelist() if n.endswith('/app.py')),None)
        if not prefix:raise RuntimeError('更新包缺少 app.py')
        total=0
        for info in archive.infolist():
            relative=info.filename.removeprefix(prefix+'/')
            parts=Path(relative).parts
            if Path(relative).is_absolute() or '..' in parts:raise RuntimeError('更新包路径不合法')
            allowed=relative in TOP_LEVEL or relative=='web/index.html' or (
                len(parts)==2 and parts[0]=='runtime' and Path(relative).suffix in {'.js','.cjs','.py'})
            if allowed and not info.is_dir():
                total+=info.file_size
                if total>64*1024*1024:raise RuntimeError('更新包解压内容超过限制')
                target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.read(info))
    for relative in ('app.py','runtime/managed_install.py','runtime/service_runner.py','runtime/kiro_local_bridge.js','runtime/model_selection.cjs'):
        if not (destination/relative).is_file():raise RuntimeError('更新包缺少文件：'+relative)


def managed_update(home):
    import time
    import sys
    sys.path.insert(0,str(ROOT))
    from runtime.managed_install import install,write_json,engine
    try:
        engine(ROOT,home)
        with tempfile.TemporaryDirectory(prefix='kiro-update-') as directory:
            staging=Path(directory);download(staging)
            install(staging,home,autostart=(home/'installation/task.json').exists(),fallback_root=ROOT)
        write_json(home/'update-status.json',{'state':'complete','finished':time.time()})
    except Exception as error:
        write_json(home/'update-status.json',{'state':'failed','error':str(error),'finished':time.time()})
        raise


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
            required = [staging / 'app.py', staging / 'runtime/kiro_local_bridge.js',
                        staging / 'runtime/context_policy.cjs', staging / 'runtime/create_response.cjs']
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
        import argparse
        parser=argparse.ArgumentParser();parser.add_argument('--managed',action='store_true');parser.add_argument('--home',type=Path)
        args=parser.parse_args()
        import os
        if args.managed or os.name=='nt':
            if not args.home:
                import app
                args.home=app.user_paths()[0]
            managed_update(args.home)
        else:update()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            print('GitHub 更新地址返回 HTTP 404：仓库对当前下载请求不可见。请将仓库设为公开，或改用可认证的发布包。')
        else:
            print(f'GitHub 下载失败：HTTP {error.code}。')
        raise SystemExit(1)
    except urllib.error.URLError as error:
        print('GitHub 下载失败：' + str(error.reason))
        raise SystemExit(1)
    except Exception as error:
        print('更新失败：' + str(error))
        raise SystemExit(1)
