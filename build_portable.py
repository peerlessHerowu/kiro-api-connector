"""Build a credential-free portable Windows folder from installed local runtimes."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parent


def build(destination,node,router):
    if destination.exists():
        raise RuntimeError('Output exists; refusing to overwrite')
    destination.mkdir(parents=True)
    shutil.copy2(ROOT/'app.py',destination/'app.py')
    shutil.copy2(ROOT/'README.md',destination/'README.md')
    shutil.copytree(ROOT/'web',destination/'web')
    runtime=destination/'runtime';runtime.mkdir()
    for file in (ROOT/'runtime').glob('*'):
        if file.suffix in ('.js','.cjs','.py'):
            shutil.copy2(file,runtime/file.name)
    (runtime/'bin').mkdir();shutil.copy2(node,runtime/'bin/node.exe')
    node_license=node.parent/'LICENSE'
    if not node_license.exists():raise RuntimeError('Node license is required')
    shutil.copy2(node_license,runtime/'bin/NODE_LICENSE.txt')
    shutil.copytree(router,runtime/'node_modules/@sifxprime/krouter',
                    ignore=shutil.ignore_patterns('__pycache__','*.log','route-cache'))
    python=destination/'python';python.mkdir()
    base=Path(sys.base_prefix)
    for pattern in ['python.exe','pythonw.exe','python3*.dll','vcruntime*.dll','LICENSE*']:
        for file in base.glob(pattern):shutil.copy2(file,python/file.name)
    shutil.copytree(base/'DLLs',python/'DLLs',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(base/'Lib',python/'Lib',ignore=shutil.ignore_patterns('site-packages','__pycache__','test','tests','idlelib','tkinter','ensurepip'))
    # Isolated import paths prevent accidentally loading another machine's Python packages.
    version=f'{sys.version_info.major}{sys.version_info.minor}'
    (python/f'python{version}._pth').write_text('.\nLib\nDLLs\n..\n',encoding='utf8')
    (destination/'打开配置向导.cmd').write_text('@echo off\r\nstart "" "%~dp0python\\pythonw.exe" "%~dp0app.py"\r\n',encoding='utf8')
    notice='Kiro API Connector portable build.\nIncludes Python (PSF license), Node.js and kRouter 0.5.163 and its dependencies.\nOriginal license files are retained; see python/LICENSE* and runtime/node_modules/@sifxprime/krouter.\nNo Kiro client, credentials or runtime databases are included.\n'
    (destination/'THIRD_PARTY_NOTICES.txt').write_text(notice,encoding='utf8')
    archive=destination.with_name(destination.name+'.zip')
    if archive.exists():raise RuntimeError('Archive exists; refusing overwrite')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for file in destination.rglob('*'):
            if file.is_file():z.write(file,file.relative_to(destination.parent))
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--node',type=Path,required=True)
    parser.add_argument('--router',type=Path,required=True)
    args=parser.parse_args()
    archive=build(args.output,args.node,args.router)
    print('Portable archive:',archive,archive.stat().st_size,'bytes')
