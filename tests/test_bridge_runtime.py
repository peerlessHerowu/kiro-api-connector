"""Isolated protocol replay; set KIRO_TEST_ROUTER_APP to the pinned kRouter app."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('verify', ROOT/'runtime/verify.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


@unittest.skipUnless(os.environ.get('KIRO_TEST_ROUTER_APP') and shutil.which('node'), 'Pinned local kRouter required')
class BridgeRuntimeTest(unittest.TestCase):
    def test_empty_summary_retry_hot_budget_and_busy_status(self):
        requests = []
        started, finish = threading.Event(), threading.Event()
        class FakeRouter(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"data":[]}')
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                if len(requests) == 1:
                    payload = {'choices':[], 'usage':{'prompt_tokens':6896,'completion_tokens':0}}
                else:
                    started.set(); finish.wait(5)
                    payload = {'choices':[{'delta':{'content':'OK'},'finish_reason':'stop'}],
                               'usage':{'prompt_tokens':6896,'completion_tokens':5}}
                self.wfile.write(('data: '+json.dumps(payload)+'\n\ndata: [DONE]\n\n').encode())

        router = ThreadingHTTPServer(('127.0.0.1',0), FakeRouter)
        threading.Thread(target=router.serve_forever,daemon=True).start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home/'db').mkdir()
            db = sqlite3.connect(home/'db/data.sqlite')
            db.execute('CREATE TABLE apiKeys (id TEXT PRIMARY KEY, key TEXT, isActive INTEGER)')
            db.execute("INSERT INTO apiKeys VALUES ('test','local-fake-key',1)")
            db.commit(); db.close()
            with socket.socket() as port_socket:
                port_socket.bind(('127.0.0.1',0)); port = port_socket.getsockname()[1]
            config = {'routerPort':router.server_port,'bridgePort':port,'dataDir':str(home),
                      'routerApp':os.environ['KIRO_TEST_ROUTER_APP'],'routerKeyId':'test',
                      'defaultModel':'test','models':['test'],'effortModels':[],
                      'prefix':'isolated','contextBudget':200000}
            config_path = home/'config.json'
            config_path.write_text(json.dumps(config),encoding='utf8')
            with (home/'bridge.log').open('wb') as log:
                proc = subprocess.Popen(['node',str(ROOT/'runtime/kiro_local_bridge.js')],
                    env=dict(os.environ,KIRO_CONNECTOR_CONFIG=str(config_path)),stdout=log,stderr=log)
                def get(path):
                    with opener.open(f'http://127.0.0.1:{port}'+path,timeout=5) as response:
                        return json.load(response)
                try:
                    for _ in range(80):
                        if proc.poll() is not None:
                            self.fail((home/'bridge.log').read_text(encoding='utf8'))
                        try: get('/health'); break
                        except OSError: time.sleep(.1)
                    # Reload catalog/default/budget without replacing the process.
                    config.update(defaultModel='changed',models=['changed'],contextBudget=500000)
                    replacement=home/'config.tmp'
                    replacement.write_text(json.dumps(config),encoding='utf8')
                    replacement.replace(config_path)
                    self.assertEqual(get('/List-Available-Models')['defaultModel']['modelId'],'changed')
                    payload={'conversationState':{'currentMessage':{'userInputMessage':{
                        'content':'[SYSTEM NOTE: automated summarization request] Summarize the supplied conversation.',
                        'modelId':'auto'}}}}
                    data=[]; errors=[]
                    def generate():
                        try:
                            request=urllib.request.Request(f'http://127.0.0.1:{port}/generateAssistantResponse',
                                data=json.dumps(payload).encode())
                            with opener.open(request,timeout=10) as response:
                                data.extend(verify.events(response.read()))
                        except Exception as error: errors.append(error)
                    thread=threading.Thread(target=generate)
                    thread.start()
                    self.assertTrue(started.wait(5))
                    self.assertEqual(get('/health')['activeRequests'],1)
                    finish.set(); thread.join(10)
                    self.assertFalse(thread.is_alive())
                    self.assertFalse(errors, errors)
                    self.assertEqual(len(requests),2)
                    self.assertEqual(requests[0],requests[1])
                    self.assertEqual(requests[0]['model'],'isolated/changed')
                    self.assertEqual(''.join(frame.get('content','') for frame in data),'OK')
                    self.assertEqual(sum(frame.get('stopReason')=='END_TURN' for frame in data),1)
                    percentages=[frame['contextUsagePercentage'] for frame in data if 'contextUsagePercentage' in frame]
                    self.assertAlmostEqual(percentages[-1],6901/500000*100)
                    self.assertEqual(get('/health')['activeRequests'],0)
                finally:
                    finish.set(); proc.terminate()
                    try: proc.wait(10)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait(10)
                    router.shutdown();router.server_close()


if __name__ == '__main__': unittest.main()
