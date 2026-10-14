"""Install immutable code releases, switch only when idle, roll back failed startup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parent.parent


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def code_files(root):
    for name in ('app.py','upgrade.py','stop.py','README.md'):
        yield root/name
    for directory,suffixes in (('runtime',{'.js','.cjs','.py'}),('web',{'.html'})):
        for file in sorted((root/directory).iterdir()):
            if file.is_file() and file.suffix in suffixes: yield file


def prepare(root,home):
    files=list(code_files(root))
    digest=hashlib.sha256()
    for file in files:
        digest.update(str(file.relative_to(root)).encode());digest.update(file.read_bytes())
    release=home/'installation/releases'/digest.hexdigest()[:16]
    if not release.exists():
        release.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=release.parent,prefix='staging-') as directory:
            staging=Path(directory)/'release'; staging.mkdir()
            for file in files:
                target=staging/file.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(file,target)
            # Syntax checks before touching a live service or release pointer.
            for file in staging.rglob('*.py'):
                compile(file.read_text(encoding='utf-8'),str(file),'exec')
            staging.rename(release)
    return release


def engine(root,home):
    target=home/'installation/engine'
    if (target/'runtime/bin/node.exe').exists() and (target/'runtime/node_modules/@sifxprime/krouter/app/custom-server.js').exists():
        return str(target/'python/python.exe') if (target/'python/python.exe').exists() else sys.executable
    sys.path.insert(0,str(root))
    import app
    deps=app.Connector(home).dependencies()
    if not deps['ready']:raise RuntimeError('Dependencies are not ready; run setup first')
    node=target/'runtime/bin/node.exe'
    router=target/'runtime/node_modules/@sifxprime/krouter'
    if not node.exists():node.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(deps['node'],node)
    if not router.exists():
        router.parent.mkdir(parents=True,exist_ok=True)
        # Copy the package only; never copy the configured router database or logs.
        with tempfile.TemporaryDirectory(dir=router.parent,prefix='staging-') as directory:
            package=Path(directory)/'package'
            shutil.copytree(Path(deps['routerApp']).parent,package,
                            ignore=shutil.ignore_patterns('__pycache__','*.log','route-cache'))
            package.rename(router)
    python=sys.executable
    if (root/'python/python.exe').exists():
        if not (target/'python/python.exe').exists():
            with tempfile.TemporaryDirectory(dir=target,prefix='staging-') as directory:
                package=Path(directory)/'python'
                shutil.copytree(root/'python',package);package.rename(target/'python')
        python=str(target/'python/python.exe')
    return python


def request(base,path,token=None):
    headers={'Content-Type':'application/json','Origin':base}
    if token:headers['X-Setup-Token']=token
    req=urllib.request.Request(base+path,data=b'{"restart":true}' if path=='/api/shutdown' else None,headers=headers)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req,timeout=5) as response:
        raw=response.read()
        return raw.decode('utf-8') if path=='/' else json.loads(raw)


def existing(base,allow_busy=False):
    import re
    try:page=request(base,'/')
    except urllib.error.HTTPError:
        raise RuntimeError('Port belongs to an unrecognized HTTP service') from None
    except urllib.error.URLError:return None
    match=re.search(r"const token='([^']+)'",page)
    if not match or 'Kiro API Connector' not in page:raise RuntimeError('Port belongs to another application')
    status=request(base,'/api/status',match[1])
    if not allow_busy and status.get('activeRequests',0):raise RuntimeError('Kiro is answering; retry after it finishes')
    return match[1],status


def register_task(home,python,enabled=True,task_name=None):
    if os.name!='nt':raise RuntimeError('Managed Windows installation only')
    name=task_name or 'KiroApiConnector-'+hashlib.sha256(str(home).lower().encode()).hexdigest()[:12]
    # All untrusted paths travel as JSON data, never interpolated PowerShell code.
    payload={'name':name,'python':python,'runner':str(home/'installation/service_runner.py'),
             'home':str(home),'enabled':enabled}
    script="""
$ErrorActionPreference='Stop'
$p=$env:KIRO_TASK_DATA | ConvertFrom-Json
if (-not $p.enabled) { Unregister-ScheduledTask -TaskName $p.name -Confirm:$false -ErrorAction SilentlyContinue; exit }
$args='"'+$p.runner+'" --home "'+$p.home+'" --scheduled'
$action=New-ScheduledTaskAction -Execute $p.python -Argument $args
$trigger=New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$recovery=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $p.name -Action $action -Trigger @($trigger,$recovery) -Principal $principal -Settings $settings -Force | Out-Null
"""
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        env=dict(os.environ,KIRO_TASK_DATA=json.dumps(payload)),capture_output=True,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        (home/'task-register.log').write_bytes(result.stderr)
        raise RuntimeError('Windows task registration failed; see task-register.log')
    return name


def launch(home,python,port,task=None):
    if task:
        # A closed HTTP socket can precede the old scheduled process exiting.
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
            '$ErrorActionPreference="Stop"; for($i=0;$i -lt 80;$i++){if((Get-ScheduledTask -TaskName $env:KIRO_TASK_NAME).State -ne "Running"){exit 0}; Start-Sleep -Milliseconds 250}; exit 1'],
            env=dict(os.environ,KIRO_TASK_NAME=task),capture_output=True,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise RuntimeError('Previous scheduled supervisor has not exited')
        class TaskProcess:
            def poll(self):return None
            def terminate(self):
                subprocess.run(['schtasks.exe','/End','/TN',task],check=True,capture_output=True,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            def wait(self,timeout=None):time.sleep(.5)
        subprocess.run(['schtasks.exe','/Run','/TN',task],check=True,capture_output=True,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return TaskProcess()
    return subprocess.Popen([python,str(home/'installation/service_runner.py'),'--home',str(home),'--port',str(port)],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def wait_ready(base,proc,configured,release=None):
    for _ in range(160):
        if proc.poll() is not None:raise RuntimeError('New supervisor exited')
        try:
            state=existing(base,allow_busy=True)
            if state and (release is None or state[1].get('installRoot')==str(release)) and (
                    not configured or all(state[1]['running'].get(n) for n in ('bridge','router'))):return
        except (OSError,ValueError):pass
        time.sleep(.25)
    raise RuntimeError('New version did not become healthy')


def install(root,home,port=20147,autostart=True,fallback_root=None):
    from runtime.service_runner import log
    home=home.resolve();home.mkdir(parents=True,exist_ok=True)
    lock=(home/'installation.lock').open('a+b')
    if os.name=='nt':
        import msvcrt
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:lock.close();raise RuntimeError('Another installation/update is running') from None
    try:
        base=f'http://127.0.0.1:{port}'; old=existing(base,allow_busy=True)
        release=prepare(root,home)
        pointer=home/'installation/current.json'
        previous=json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else None
        if previous and previous['root']==str(release) and old and old[1].get('installRoot')==str(release):
            if autostart and not (home/'installation/task.json').exists():
                name=register_task(home,previous['python']);write_json(home/'installation/task.json',{'name':name})
            return previous
        if old and old[1].get('activeRequests',0):raise RuntimeError('Kiro is answering; retry after it finishes')
        python=engine(root,home)
        checked=subprocess.run([python,'-c',
            'import sys;sys.path.insert(0,sys.argv[1]);import app;from pathlib import Path;assert app.Connector(Path(sys.argv[2])).dependencies()["ready"]',str(release),str(home)],
            env=dict(os.environ,KIRO_CONNECTOR_ENGINE=str(home/'installation/engine')),
            capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if checked.returncode:raise RuntimeError('New release dependency/import check failed; old service unchanged')
        entry=home/'installation/service_runner.py'
        runner_before=entry.read_bytes() if entry.exists() else None
        staged_runner=entry.with_suffix('.tmp')
        shutil.copy2(root/'runtime/service_runner.py',staged_runner);staged_runner.replace(entry)
        next_record={'root':str(release),'python':python}
        task=register_task(home,python) if autostart else None
        # Recheck immediately before shutdown, after potentially slow local copying.
        old=existing(base)
        if (home/'config.json').exists():
            config=json.loads((home/'config.json').read_text(encoding='utf-8'))
            try:health=request(f"http://127.0.0.1:{config['bridgePort']}",'/health')
            except urllib.error.URLError:health={}
            if health.get('activeRequests',0):raise RuntimeError('Kiro is answering; retry after it finishes')
        if old:
            request(base,'/api/shutdown',old[0])
            for _ in range(80):
                with socket.socket() as sock:
                    if sock.connect_ex(('127.0.0.1',port))!=0:break
                time.sleep(.25)
            else:raise RuntimeError('Old wizard did not exit; release unchanged')
        write_json(pointer,next_record)
        (home/'installation/run-enabled').touch()
        proc=launch(home,python,port,task)
        try:wait_ready(base,proc,(home/'config.json').exists(),release)
        except Exception:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=15)
            if runner_before is not None:entry.write_bytes(runner_before)
            if previous:
                write_json(pointer,previous);recovered=launch(home,previous['python'],port,task)
                wait_ready(base,recovered,(home/'config.json').exists(),Path(previous['root']))
            elif old:
                # Initial migration can fall back to its original complete source tree.
                original=old[1].get('installRoot') or str(fallback_root or root)
                if original:
                    pointer.unlink(missing_ok=True)
                    (home/'installation/run-enabled').unlink(missing_ok=True)
                    subprocess.Popen([sys.executable,str(Path(original)/'app.py'),'--home',str(home),'--no-browser'],
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            log(home,'release_failed',release=release.name,rolledBack=bool(previous));raise
        if previous:write_json(home/'installation/previous.json',previous)
        if task:write_json(home/'installation/task.json',{'name':task})
        legacy=Path(os.environ.get('APPDATA',''))/'Microsoft/Windows/Start Menu/Programs/Startup/KiroApiConnector.cmd'
        if autostart and legacy.exists() and str(home) in legacy.read_text(encoding='utf-8') and 'app.py' in legacy.read_text(encoding='utf-8'):
            shutil.copy2(legacy,home/'startup-before-managed.cmd');legacy.unlink()
        log(home,'release_applied',release=release.name,previous=previous['root'] if previous else None)
        return next_record
    finally:lock.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--port',type=int,default=20147);parser.add_argument('--no-autostart',action='store_true')
    args=parser.parse_args()
    try:install(ROOT,args.home,args.port,not args.no_autostart)
    except Exception as error:
        from runtime.service_runner import log
        log(args.home,'install_failed',errorType=type(error).__name__)
        print(str(error),file=sys.stderr);sys.exit(1)
