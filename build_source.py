"""Export only distributable project source, never a configured installation."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
TOP_LEVEL = ('.gitignore', 'AGENTS.md', 'README.md', 'app.py', 'build_portable.py',
             'build_source.py', '打开配置向导.cmd')
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
                archive.write(path, Path('kiro-api-connector')/path.relative_to(root))
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=build(args.output)
    print('Source archive:',result,result.stat().st_size,'bytes')
