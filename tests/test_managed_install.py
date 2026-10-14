import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from unittest.mock import patch

from runtime import managed_install as managed
import upgrade


class ManagedTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows detached updater integration')
    def test_update_worker_survives_old_supervisor(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);release=home/'installation/releases/fake';release.mkdir(parents=True)
            shutil.copy2(managed.ROOT/'runtime/service_runner.py',home/'installation/service_runner.py')
            ready=home/'ready';done=home/'done'
            worker='import time;from pathlib import Path;Path('+repr(str(ready))+').touch();time.sleep(2);Path('+repr(str(done))+').touch()'
            (release/'app.py').write_text('import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",'+repr(worker)+'],creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB|subprocess.CREATE_NO_WINDOW)\ntime.sleep(30)\n',encoding='utf-8')
            managed.write_json(home/'installation/current.json',{'root':str(release),'python':sys.executable})
            proc=managed.launch(home,sys.executable,20147)
            try:
                for _ in range(100):
                    if ready.exists():break
                    time.sleep(.05)
                self.assertTrue(ready.exists())
                proc.kill();proc.wait(10)
                for _ in range(100):
                    if done.exists():break
                    time.sleep(.05)
                self.assertTrue(done.exists(),'Update worker was killed with the old release')
            finally:
                if proc.poll() is None:proc.kill();proc.wait(10)

    @unittest.skipUnless(os.name=='nt','Windows real release switch')
    def test_real_failed_release_restores_running_previous_wizard(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)/'home';source=Path(directory)/'source';source.mkdir()
            for name in ('upgrade.py','stop.py','README.md'):(source/name).write_text('# source')
            (source/'runtime').mkdir();(source/'web').mkdir()
            shutil.copy2(managed.ROOT/'runtime/service_runner.py',source/'runtime/service_runner.py')
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            code='''import argparse,json,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Connector:
 def __init__(self,*args):pass
 def dependencies(self):return {"ready":True}
def main():
 p=argparse.ArgumentParser();p.add_argument('--port',type=int);p.add_argument('--home');p.add_argument('--no-browser',action='store_true');a=p.parse_args()
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_GET(self):
   body="Kiro API Connector const token='test'" if self.path=='/' else json.dumps({'installRoot':str(Path(__file__).parent),'running':{'bridge':True,'router':True},'activeRequests':0})
   self.send_response(200);self.end_headers();self.wfile.write(body.encode())
  def do_POST(self):
   self.send_response(200);self.end_headers();self.wfile.write(b'{}');threading.Thread(target=self.server.shutdown).start()
 server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);server.serve_forever();server.server_close()
if __name__=='__main__':main()
'''
            (source/'app.py').write_text(code,encoding='utf-8');base=f'http://127.0.0.1:{port}'
            processes=[];real_launch=managed.launch
            def launch(*args,**kwargs):
                proc=real_launch(*args,**kwargs);processes.append(proc);return proc
            try:
                with patch.object(managed,'engine',return_value=sys.executable),patch.object(managed,'launch',side_effect=launch):
                    first=managed.install(source,home,port,False)
                    self.assertEqual(managed.existing(base)[1]['installRoot'],first['root'])
                    (source/'app.py').write_text(code.replace("if __name__=='__main__':main()","if __name__=='__main__':raise SystemExit(7)"),encoding='utf-8')
                    with self.assertRaisesRegex(RuntimeError,'supervisor exited'):
                        managed.install(source,home,port,False)
                    self.assertEqual(json.loads((home/'installation/current.json').read_text())['root'],first['root'])
                    self.assertEqual(managed.existing(base)[1]['installRoot'],first['root'])
            finally:
                state=managed.existing(base)
                if state:managed.request(base,'/api/shutdown',state[0])
                for proc in processes:proc.wait(timeout=10)

    @unittest.skipUnless(os.name=='nt' and os.environ.get('KIRO_TEST_SCHEDULED_TASK')=='1','Opt-in real Windows recovery task')
    def test_scheduled_task_restarts_killed_supervisor(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);release=home/'installation/releases/fake';release.mkdir(parents=True)
            shutil.copy2(managed.ROOT/'runtime/service_runner.py',home/'installation/service_runner.py')
            starts=home/'starts.log'
            (release/'app.py').write_text('from pathlib import Path\nimport time\nwith Path('+repr(str(starts))+').open("a") as f:f.write("start\\n")\ntime.sleep(180)\n',encoding='utf-8')
            managed.write_json(home/'installation/current.json',{'root':str(release),'python':sys.executable})
            name='KiroApiConnector-Test-'+str(os.getpid())
            (home/'installation/run-enabled').touch()
            try:
                managed.register_task(home,sys.executable,task_name=name)
                managed.launch(home,sys.executable,20147,task=name)
                for _ in range(100):
                    if starts.exists():break
                    time.sleep(.1)
                self.assertTrue(starts.exists(),'Scheduled action never started')
                records=[json.loads(l) for l in (home/'lifecycle.log').read_text().splitlines()]
                pid=next(r['pid'] for r in records if r['event']=='supervisor_start')
                subprocess.run(['taskkill.exe','/PID',str(pid),'/F'],check=True,capture_output=True)
                for _ in range(90):
                    if len(starts.read_text().splitlines())>=2:break
                    time.sleep(1)
                self.assertEqual(len(starts.read_text().splitlines()),2,'OS did not recover killed supervisor')
            finally:
                subprocess.run(['schtasks.exe','/End','/TN',name],capture_output=True)
                managed.register_task(home,sys.executable,False,task_name=name)

    def test_immutable_release_excludes_credentials_and_bad_code(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);source=home/'source';source.mkdir()
            for name in ('app.py','upgrade.py','stop.py','README.md'):
                (source/name).write_text('# source',encoding='utf-8')
            (source/'runtime').mkdir();(source/'web').mkdir()
            (source/'runtime/credentials.local.json').write_text('SECRET')
            (source/'config.json').write_text('SECRET')
            release=managed.prepare(source,home)
            self.assertFalse((release/'config.json').exists())
            self.assertFalse((release/'runtime/credentials.local.json').exists())
            self.assertEqual(managed.prepare(source,home),release)
            (source/'app.py').write_text('syntax error !!!')
            with self.assertRaises(SyntaxError):managed.prepare(source,home)
            self.assertEqual((release/'app.py').read_text(),'# source')

    def test_busy_wizard_never_prepares_or_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(managed,'existing',side_effect=RuntimeError('Kiro is answering')),patch.object(managed,'prepare') as prepare:
                with self.assertRaisesRegex(RuntimeError,'answering'):
                    managed.install(managed.ROOT,Path(directory),autostart=False)
                prepare.assert_not_called()

    def test_failed_switch_rolls_back_pointer_and_relaunches(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);pointer=home/'installation/current.json'
            previous={'root':str(home/'installation/releases/old'),'python':sys.executable}
            managed.write_json(pointer,previous)
            proc=type('Proc',(),{'poll':lambda self:0})()
            with patch.object(managed,'existing',return_value=None),patch.object(managed,'prepare',return_value=home/'installation/releases/new'),patch.object(managed,'engine',return_value=sys.executable),patch.object(managed,'launch',return_value=proc) as launch,patch.object(managed,'wait_ready',side_effect=[RuntimeError('bad startup'),None]),patch('subprocess.run',return_value=type('Result',(),{'returncode':0})()):
                with self.assertRaisesRegex(RuntimeError,'bad startup'):
                    managed.install(managed.ROOT,home,autostart=False)
                self.assertEqual(json.loads(pointer.read_text()),previous)
                self.assertEqual(launch.call_count,2)
                self.assertIn('release_failed',(home/'lifecycle.log').read_text())

    def test_archive_traversal_is_rejected(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('repo/app.py','# source')
            archive.writestr('repo/../outside.py','bad')
        data.seek(0)
        with tempfile.TemporaryDirectory() as directory,patch('urllib.request.urlopen',return_value=data):
            with self.assertRaisesRegex(RuntimeError,'路径'):
                upgrade.download(Path(directory))
            self.assertFalse((Path(directory).parent/'outside.py').exists())

    @unittest.skipUnless(os.name=='nt','Windows Job Object integration')
    def test_killed_runner_releases_its_child_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);release=home/'installation/releases/fake';release.mkdir(parents=True)
            import shutil
            shutil.copy2(managed.ROOT/'runtime/service_runner.py',home/'installation/service_runner.py')
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            (release/'app.py').write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-m','http.server',str("+str(port)+"),'--bind','127.0.0.1'])\ntime.sleep(120)\n",encoding='utf-8')
            managed.write_json(home/'installation/current.json',{'root':str(release),'python':sys.executable})
            proc=managed.launch(home,sys.executable,port)
            def listening():
                with socket.socket() as sock:return sock.connect_ex(('127.0.0.1',port))==0
            try:
                for _ in range(100):
                    if listening():break
                    if proc.poll() is not None:self.fail('Runner exited; '+(home/'startup.log').read_text())
                    time.sleep(.05)
                self.assertTrue(listening())
                proc.kill();proc.wait(10)
                for _ in range(100):
                    if not listening():break
                    time.sleep(.05)
                self.assertFalse(listening(),'Child survived the supervisor and still owns the port')
            finally:
                if proc.poll() is None:proc.kill();proc.wait(10)


if __name__=='__main__':unittest.main()
