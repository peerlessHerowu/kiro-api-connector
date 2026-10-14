"""Stable Windows task entry; code lives outside download folders."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


def job_for_process_tree():
    """The OS closes this handle on death and kills our app and its descendants."""
    if os.name != 'nt': return None
    class Basic(ctypes.Structure):
        _fields_ = [('processTime',ctypes.c_longlong),('jobTime',ctypes.c_longlong),
                    ('flags',wintypes.DWORD),('minWorking',ctypes.c_size_t),
                    ('maxWorking',ctypes.c_size_t),('active',wintypes.DWORD),
                    ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name,ctypes.c_ulonglong) for name in ('readOps','writeOps','otherOps','readBytes','writeBytes','otherBytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('basic',Basic),('io',IO),('processMemory',ctypes.c_size_t),
                    ('jobMemory',ctypes.c_size_t),('peakProcess',ctypes.c_size_t),('peakJob',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    handle=kernel.CreateJobObjectW(None,None)
    # Only explicitly detached update workers may outlive the old release.
    limits=Extended(); limits.basic.flags=0x2000 | 0x800  # KILL_ON_JOB_CLOSE | BREAKAWAY_OK
    if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(handle,kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def log(home,event,**fields):
    home.mkdir(parents=True,exist_ok=True)
    with (home/'lifecycle.log').open('a',encoding='utf-8') as out:
        out.write(json.dumps({'time':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'event':event,**fields},ensure_ascii=False)+'\n')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--port',type=int,default=20147)
    parser.add_argument('--scheduled',action='store_true')
    args=parser.parse_args(); home=args.home.resolve()
    if args.scheduled and not (home/'installation/run-enabled').exists():return 0
    job=job_for_process_tree()  # Keep handle alive for this process lifetime.
    record=json.loads((home/'installation/current.json').read_text(encoding='utf-8'))
    root=Path(record['root'])
    if not root.is_relative_to(home/'installation/releases'):
        raise RuntimeError('Invalid managed release path')
    log(home,'supervisor_start',pid=os.getpid(),release=root.name)
    with (home/'startup.log').open('ab') as output:
        proc=subprocess.Popen([record['python'],str(root/'app.py'),'--home',str(home),
                               '--port',str(args.port),'--no-browser'],
                              env=dict(os.environ,KIRO_CONNECTOR_ENGINE=str(home/'installation/engine')),
                              stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        code=proc.wait()
    log(home,'supervisor_exit',exitCode=code,release=root.name)
    return code


if __name__=='__main__':
    try: sys.exit(main())
    except Exception as error:
        # No configuration or credentials are written to stderr.
        print('Managed startup failed:',type(error).__name__,file=sys.stderr)
        sys.exit(1)
