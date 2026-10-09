"""Export only distributable project source, never a configured installation."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
TOP_LEVEL = ('.gitignore', '.gitattributes', 'AGENTS.md', 'README.md', 'app.py', 'build_portable.py',
             'build_source.py', 'setup.ps1', 'setup.sh', '打开配置向导.cmd', '打开配置向导.command')
DIRECTORIES = {'runtime': {'.js', '.cjs', '.py'}, 'web': {'.html'},
               'tests': {'.py', '.cjs'}}


def source_files(root=ROOT):
    files = [root/name for name in TOP_LEVEL]
    for directory, suffixes in DIRECTORIES.items():
        files.extend(p for p in (root/directory).iterdir() if p.is_file() and p.suffix in suffixes)
    for path in files:
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError('Missing or external source file: ' + path.name)
    return sorted(files)


def build(output, root=ROOT):
    files = source_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids silently replacing a user's existing archive.
    with output.open('xb') as handle:
        with zipfile.ZipFile(handle,'w',zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                name=Path('kiro-api-connector')/path.relative_to(root)
                info=zipfile.ZipInfo.from_file(path,name)
                info.create_system=3
                info.external_attr=(0o100755 if path.suffix in ('.command','.sh') else 0o100644)<<16
                info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,path.read_bytes())
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=build(args.output)
    print('Source archive:',result,result.stat().st_size,'bytes')
