import importlib.util
import json
import os
import plistlib
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import zipfile
import io
from unittest.mock import patch
from http.server import ThreadingHTTPServer

spec=importlib.util.spec_from_file_location('app',Path(__file__).parents[1]/'app.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=Path(self.temp.name)
        self.settings=self.home/'settings.json'
        self.connector=app.Connector(self.home/'data',settings=self.settings)
        self.connector.last_test={'model':'test'}

    def tearDown(self):
        self.temp.cleanup()

    def test_html_response_has_safe_actionable_error(self):
        with patch.object(self.connector.opener,'open',return_value=io.BytesIO(b'<html>private-secret</html>')):
            with self.assertRaisesRegex(app.InvalidJsonResponse,'非 JSON') as error:
                self.connector.request('https://example.com/v1/models')
            self.assertNotIn('private-secret',str(error.exception))

    def test_update_error_includes_updater_detail(self):
        self.connector.dependencies=lambda: {'bundled':True}
        with patch('subprocess.run', return_value=type('Result', (), {'returncode':1, 'stdout':'GitHub 下载失败：HTTP 404。', 'stderr':''})()):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 404'):
                self.connector.update()

    def test_busy_bridge_cannot_be_updated(self):
        self.connector.bridge_health=lambda:{'activeRequests':1}
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, '正在回答'):
                self.connector.update()
            run.assert_not_called()

    def test_context_change_does_not_restart_hot_bridge(self):
        old={'nodeId':'n','connectionId':'c','routerKeyId':'k','prefix':'kiro-connector',
             'baseUrl':'https://example.com/v1','models':['m'],'defaultModel':'m',
             'effortModels':[],'contextBudget':200000}
        self.connector.config=old
        self.connector.key=lambda:'fake'
        self.connector.models=lambda *args:['m']
        self.connector.start_router=lambda:None
        self.connector.start_bridge=lambda:None
        self.connector.bridge_health=lambda:{'hotReload':True,'activeRequests':1}
        self.connector.dependencies=lambda:{'routerApp':'fake','bundled':False}
        self.connector.test=lambda:None
        with patch.object(self.connector, 'stop_bridge') as stop, patch.object(self.connector, 'api') as api:
            self.connector.configure({'baseUrl':old['baseUrl'],'models':['m'],
                'defaultModel':'m','contextBudget':500000})
            stop.assert_not_called()
            api.assert_not_called()
        self.assertEqual(app.read_json(self.connector.config_file)['contextBudget'],500000)

    def test_update_shutdown_preserves_kiro_connection(self):
        self.connector.apply()
        settings = self.settings.read_bytes()
        backup = self.connector.backup.read_bytes()
        self.connector.wanted = True
        server = ThreadingHTTPServer(('127.0.0.1',0),app.handler(self.connector,0))
        port = server.server_port
        server.RequestHandlerClass = app.handler(self.connector,port)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{port}/api/shutdown',
                data=b'{}', headers={'X-Setup-Token':self.connector.token})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request,timeout=5) as response:
                self.assertEqual(json.load(response), {'ok':True})
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(self.connector.wanted)
            self.assertTrue(self.connector.shutting_down)
            self.assertEqual(self.settings.read_bytes(), settings)
            self.assertEqual(self.connector.backup.read_bytes(), backup)
        finally:
            server.shutdown(); server.server_close()

    def test_invalid_model_catalog_has_actionable_error(self):
        self.connector.request=lambda *args,**kwargs: {'data':'not-a-list'}
        with self.assertRaisesRegex(app.InvalidJsonResponse,'模型接口格式'):
            self.connector.models('https://example.com/v1','fake-key')

    def test_apply_restore_keeps_other_settings(self):
        app.write_json(self.settings,{'editor.fontSize':16})
        self.connector.apply()
        current=app.read_json(self.settings);current['editor.fontSize']=18
        app.write_json(self.settings,current)
        self.connector.restore()
        self.assertEqual(app.read_json(self.settings),{'editor.fontSize':18})

    def test_foreign_bridge_cannot_be_overwritten(self):
        app.write_json(self.settings,{app.ENDPOINT_KEYS[0]:[{'endpoint':'http://127.0.0.1:20129'}]})
        before=self.settings.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'另一套'):self.connector.apply()
        self.assertEqual(self.settings.read_bytes(),before)
        self.assertFalse(self.connector.backup.exists())

    def test_restore_refuses_changed_endpoint(self):
        self.connector.apply()
        current=app.read_json(self.settings)
        current[app.ENDPOINT_KEYS[0]]=[{'endpoint':'http://127.0.0.1:9999'}]
        app.write_json(self.settings,current)
        with self.assertRaisesRegex(RuntimeError,'其他程序'):self.connector.restore()
        self.assertTrue(self.connector.backup.exists())

    def test_missing_original_file_restored_to_absent(self):
        self.connector.apply();self.connector.restore()
        self.assertFalse(self.settings.exists())

    def test_unverified_cannot_enable(self):
        self.connector.last_test=None
        with self.assertRaisesRegex(RuntimeError,'实际推理'):self.connector.apply()

    def test_configuration_failure_restores_prior_provider(self):
        old={'nodeId':'node-old','connectionId':'connection-old','baseUrl':'https://old.example/v1',
             'defaultModel':'old-model'}
        self.connector.config=dict(old)
        self.connector.key=lambda:'old-fake-key'
        calls=[]
        self.connector.api=lambda path,body=None,method=None:calls.append((path,body,method))
        self.connector.start_bridge=lambda:None
        def failed(body):
            self.connector.configuration_mutated=True
            self.connector.config={'baseUrl':'https://new.example/v1'}
            raise RuntimeError('Synthetic test failure')
        self.connector._configure=failed
        with self.assertRaisesRegex(RuntimeError,'Synthetic'):self.connector.configure({})
        self.assertEqual(self.connector.config,old)
        self.assertEqual(app.read_json(self.connector.config_file),old)
        self.assertEqual(calls[-1][1]['apiKey'],'old-fake-key')

    def test_jsonc_settings_are_not_destroyed(self):
        self.settings.write_text('{ // comment\n "editor.fontSize": 16\n}',encoding='utf8')
        before=self.settings.read_bytes()
        with self.assertRaises(json.JSONDecodeError):self.connector.apply()
        self.assertEqual(self.settings.read_bytes(),before)
        self.assertFalse(self.connector.backup.exists())

    @unittest.skipUnless(os.name=='nt','Windows autostart')
    def test_autostart_is_owned_and_unicode_safe(self):
        with patch.dict(os.environ,{'APPDATA':str(self.home/'fake-appdata')}):
            path=self.home/'fake-appdata/Microsoft/Windows/Start Menu/Programs/Startup/KiroApiConnector.cmd'
            self.connector.autostart(True)
            self.assertIn('chcp 65001',path.read_text(encoding='utf8'))
            self.connector.autostart(False)
            self.assertFalse(path.exists())
            path.write_text('another installation',encoding='utf8')
            with self.assertRaisesRegex(RuntimeError,'不属于'):self.connector.autostart(False)
            self.assertTrue(path.exists())

    def test_url_credentials_and_non_https_rejected(self):
        for url in ['http://example.com/v1','https://user:key@example.com/v1','https://example.com/v1?key=x','https://example.com/v1#key']:
            with self.assertRaises(ValueError):app.validate_url(url)
        self.assertEqual(app.validate_url(' https://example.com/v1/ '),'https://example.com/v1')

    def test_source_export_excludes_private_data_and_dependencies(self):
        spec=importlib.util.spec_from_file_location('export',Path(__file__).parents[1]/'build_source.py')
        export=importlib.util.module_from_spec(spec);spec.loader.exec_module(export)
        root=self.home/'source';root.mkdir()
        for name in export.TOP_LEVEL:(root/name).write_text('source',encoding='utf8')
        for directory in export.DIRECTORIES:
            (root/directory).mkdir();(root/directory/'main.py').write_text('source',encoding='utf8')
        for path in ['config.json','runtime/credentials.local.json','.local/private.json','runtime/node_modules/private.js']:
            target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('fake-secret-only',encoding='utf8')
        output=self.home/'source.zip';export.build(output,root)
        with zipfile.ZipFile(output) as archive:
            self.assertFalse(any('node_modules' in n or '.local' in n or 'config.json' in n or 'credentials' in n for n in archive.namelist()))
            self.assertTrue(all(b'fake-secret-only' not in archive.read(n) for n in archive.namelist()))
            self.assertEqual(archive.getinfo('kiro-api-connector/打开配置向导.command').external_attr>>16,0o100755)
        with self.assertRaises(FileExistsError):export.build(output,root)

    def test_macos_paths_and_launchagent(self):
        with patch.object(app.sys,'platform','darwin'),patch.object(app.Path,'home',return_value=self.home):
            home,settings=app.user_paths()
            self.assertEqual(home,self.home/'Library/Application Support/KiroApiConnector')
            self.assertEqual(settings,self.home/'Library/Application Support/Kiro/User/settings.json')
            self.connector.autostart(True)
            path=app.startup_path();plist=plistlib.loads(path.read_bytes())
            self.assertTrue(plist['RunAtLoad'])
            self.assertIn('--no-browser',plist['ProgramArguments'])
            self.assertIn('/opt/homebrew/bin',plist['EnvironmentVariables']['PATH'])
            self.connector.autostart(False);self.assertFalse(path.exists())

    def test_macos_secret_format_rejects_windows_cipher(self):
        with patch.object(app.sys,'platform','darwin'):
            protected=app.dpapi('fake-mac-admin')
            self.assertEqual(app.dpapi(protected,True),'fake-mac-admin')
            with self.assertRaisesRegex(RuntimeError,'其他系统'):app.dpapi('windows-cipher',True)

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_local_secret_protection(self):
        value=app.dpapi('fake-secret-only')
        self.assertNotIn('fake-secret',value)
        self.assertEqual(app.dpapi(value,True),'fake-secret-only')

    def test_http_auth_host_origin(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),app.handler(self.connector,0))
        port=server.server_port;server.RequestHandlerClass=app.handler(self.connector,port)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def rejected(headers):
            request=urllib.request.Request(f'http://127.0.0.1:{port}/api/stop',data=b'{}',headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as error:opener.open(request)
            self.assertEqual(error.exception.code,403)
        try:
            rejected({})
            rejected({'X-Setup-Token':self.connector.token,'Origin':'https://evil.example'})
            rejected({'X-Setup-Token':self.connector.token,'Host':'evil.example'})
            with opener.open(f'http://127.0.0.1:{port}/') as response:
                html=response.read().decode()
            self.assertIn('Kiro API Connector',html)
            self.assertIn(self.connector.token,html)
            with opener.open(f'http://127.0.0.1:{port}/health') as response:
                self.assertEqual(json.load(response)['app'],'kiro-api-connector')
        finally:server.shutdown();server.server_close()

    def test_local_server_port_cannot_have_two_owners(self):
        first=app.LocalServer(('127.0.0.1',0),app.handler(self.connector,0))
        try:
            with self.assertRaises(OSError):
                app.LocalServer(first.server_address,app.handler(self.connector,first.server_port))
        finally:first.server_close()


if __name__=='__main__':unittest.main()
