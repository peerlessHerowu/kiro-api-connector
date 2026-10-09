"""Windows/macOS local setup wizard for Kiro's API-key bridge. Standard library only."""
import argparse
import base64
import ctypes
import http.cookiejar
import json
import os
import plistlib
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
ENDPOINT_KEYS = ('codewhisperer.config.krsEndpoints', 'codewhisperer.config.cpsEndpoints')
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class RequestFailure(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'请求失败：HTTP {status}，请检查地址、凭据或模型权限。')


class InvalidJsonResponse(RuntimeError):
    pass


def user_paths():
    if sys.platform=='darwin':
        support=Path.home()/'Library/Application Support'
        return support/'KiroApiConnector', support/'Kiro/User/settings.json'
    if os.name=='nt':
        return Path(os.environ['LOCALAPPDATA'])/'KiroApiConnector', Path(os.environ['APPDATA'])/'Kiro/User/settings.json'
    raise RuntimeError('当前支持 Windows 与 macOS。')


def startup_path():
    if sys.platform=='darwin':
        return Path.home()/'Library/LaunchAgents/club.kiro-api-connector.plist'
    return Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/KiroApiConnector.cmd'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with os.fdopen(os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w',encoding='utf8') as handle:
        if os.name!='nt':os.fchmod(handle.fileno(),0o600)
        handle.write(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def read_json(path, fallback=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else fallback


def validate_url(value):
    url = urllib.parse.urlsplit(value.strip().rstrip('/'))
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('请填写 HTTPS Base URL，例如 https://example.com/v1，不含账号、参数或片段。')
    return urllib.parse.urlunsplit(url)


def dpapi(value, decrypt=False):
    if sys.platform=='darwin':
        # macOS secrets live in a 0700 directory and 0600 files; this is not encryption.
        prefix='owner-file:'
        if decrypt:
            if not value.startswith(prefix):raise RuntimeError('无法读取其他系统的管理密码，请在本机重新配置。')
            return base64.b64decode(value[len(prefix):]).decode()
        return prefix+base64.b64encode(value.encode()).decode()
    if os.name != 'nt':
        raise RuntimeError('凭据保护仅支持 Windows。')
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = base64.b64decode(value) if decrypt else value.encode()
    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        result = ctypes.string_at(target.data, target.size)
        return result.decode() if decrypt else base64.b64encode(result).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def free_port(port):
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1', port)) != 0


def takeover_existing(port):
    """Gracefully stop this tool's existing wizard before switching installations."""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as response:
            page = response.read().decode('utf8', 'replace')
        match = re.search(r"const token='([^']+)'", page)
        if not match or 'Kiro API Connector' not in page:
            raise RuntimeError('配置端口已被其他本地程序占用，未停止。')
        status_request = urllib.request.Request(
            f'http://127.0.0.1:{port}/api/status',
            headers={'X-Setup-Token':match.group(1)})
        with urllib.request.urlopen(status_request, timeout=5) as response:
            old_status = json.loads(response.read())
        if os.name == 'nt':
            answer = ctypes.windll.user32.MessageBoxW(
                None, '检测到已有 Kiro API Connector。是否停止旧向导并切换到当前版本？',
                'Kiro API Connector', 0x24)
            if answer != 6:
                raise RuntimeError('已取消切换，旧向导保持运行。')
        request = urllib.request.Request(
            f'http://127.0.0.1:{port}/api/shutdown', data=b'{}', method='POST',
            headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}',
                     'X-Setup-Token':match.group(1)})
        try:
            with urllib.request.urlopen(request, timeout=15):
                pass
            for _ in range(80):
                if free_port(port): return False
                time.sleep(.25)
            raise RuntimeError('旧向导退出超时，请检查日志；未强制结束。')
        except urllib.error.HTTPError as error:
            if error.code != 404: raise
        # Older versions only offer Stop, which restores endpoints. The new
        # instance must verify inference before enabling the connection again.
        request.full_url = f'http://127.0.0.1:{port}/api/stop'
        with urllib.request.urlopen(request, timeout=15):
            pass
        if os.name == 'nt':
            output = subprocess.check_output(['netstat','-ano','-p','tcp'], text=True,
                                             stderr=subprocess.DEVNULL, creationflags=FLAGS)
            pids = set()
            for line in output.splitlines():
                fields = line.split()
                if len(fields) >= 5 and fields[1].endswith(':' + str(port)) and fields[3] == 'LISTENING':
                    pids.add(fields[4])
            for pid in pids:
                subprocess.run(['taskkill','/PID',pid,'/T','/F'], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=FLAGS)
        else:
            # macOS source updates need the same handoff as Windows. lsof is
            # part of the base system and limits termination to this port.
            output = subprocess.check_output(['lsof','-tiTCP:' + str(port),'-sTCP:LISTEN'], text=True,
                                             stderr=subprocess.DEVNULL)
            for pid in {x.strip() for x in output.splitlines() if x.strip().isdigit()}:
                os.kill(int(pid), 15)
        for _ in range(40):
            if free_port(port): return bool(old_status.get('enabled'))
            time.sleep(.25)
        raise RuntimeError('旧向导已停止，但配置端口仍被占用。')
    except urllib.error.URLError:
        raise RuntimeError('已有程序占用配置端口，未能确认它是本工具。') from None


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if os.name=='nt':
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


class Connector:
    def __init__(self, home, router_port=20148, bridge_port=20149, settings=None):
        self.home = home.resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        if sys.platform=='darwin':self.home.chmod(0o700)
        self.config_file = self.home / 'config.json'
        self.data = self.home / 'router'
        self.router_port, self.bridge_port = router_port, bridge_port
        self.settings = settings or user_paths()[1]
        self.backup = self.home / 'endpoint-backup.json'
        self.processes = {}
        self.lock = threading.RLock()
        self.last_test = None
        self.created = []
        self.configuration_mutated = False
        self.wanted = False
        self.last_error = None
        self.shutting_down = False
        self.config = read_json(self.config_file, {})
        if self.config:
            self.router_port = self.config['routerPort']
            self.bridge_port = self.config['bridgePort']
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                     urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.token = secrets.token_urlsafe(32)

    def dependencies(self):
        bundled_node = ROOT / ('runtime/bin/node.exe' if os.name=='nt' else 'runtime/bin/node')
        extra_paths = [Path('/opt/homebrew/bin'),Path('/usr/local/bin')] if sys.platform=='darwin' else []
        node = str(bundled_node) if bundled_node.exists() else shutil.which('node')
        node = node or next((str(p/'node') for p in extra_paths if (p/'node').exists()),None)
        npm = shutil.which('npm.cmd') or shutil.which('npm')
        npm = npm or next((str(p/'npm') for p in extra_paths if (p/'npm').exists()),None)
        app = ROOT / 'runtime/node_modules/@sifxprime/krouter/app'
        if not (app / 'custom-server.js').exists() and npm:
            try:
                npm_env=dict(os.environ)
                if node:npm_env['PATH']=str(Path(node).parent)+os.pathsep+npm_env.get('PATH','')
                global_root = subprocess.check_output([npm, 'root', '-g'], text=True, creationflags=FLAGS,env=npm_env).strip()
                app = Path(global_root) / '@sifxprime/krouter/app'
            except subprocess.SubprocessError:
                pass
        package = read_json(app.parent / 'package.json', {})
        valid = package.get('version') == '0.5.163' and (app / 'custom-server.js').exists()
        sqlite_ok = bool(node and subprocess.run([node, '-e', "require('node:sqlite')"],
                         capture_output=True, creationflags=FLAGS).returncode == 0)
        return {'node':node, 'npm':npm, 'routerApp':str(app) if valid else None,
                'ready':bool(node and valid and sqlite_ok), 'sqlite':sqlite_ok,
                'bundled':bundled_node.exists()}

    def request(self, url, body=None, method=None, headers=None, timeout=30):
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
              method=method, headers={'Content-Type':'application/json', **(headers or {})})
        try:
            with self.opener.open(req, timeout=timeout) as response:
                try:
                    return json.load(response)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise InvalidJsonResponse('接口返回了空内容或非 JSON 内容，请检查 API 地址（通常含 /v1），以及是否被登录页面或网站防护拦截。') from None
        except urllib.error.HTTPError as error:
            # Never echo server bodies, which may contain submitted credentials.
            raise RequestFailure(error.code) from None

    def api(self, path, body=None, method=None):
        return self.request(f'http://127.0.0.1:{self.router_port}' + path, body, method)

    def key(self):
        with sqlite3.connect(self.data / 'db/data.sqlite') as db:
            db.execute('PRAGMA query_only=ON')
            row = db.execute('SELECT data FROM providerConnections WHERE id=? LIMIT 1',
                             (self.config['connectionId'],)).fetchone()
        if not row:
            raise RuntimeError('本工具的供应商配置已不存在。')
        return json.loads(row[0])['apiKey']

    def models(self, base, key):
        base = validate_url(base)
        if not key and self.config:
            key = self.key()
        if not key:
            raise ValueError('请填写 API key。')
        data = self.request(base + '/models', headers={'Authorization':'Bearer ' + key, 'Accept':'application/json'})
        if not isinstance(data,dict) or not isinstance(data.get('data'),list):
            raise InvalidJsonResponse('模型接口格式不兼容，需要 OpenAI 格式的 data 模型列表。请检查 Base URL，或手动填写模型 ID 后测试。')
        ids = sorted({x['id'] for x in data['data'] if isinstance(x,dict) and isinstance(x.get('id'), str)})
        if not ids:
            raise RuntimeError('未读取到模型，可手动填写模型 ID 后测试。')
        return ids

    def spawn(self, name, command, env):
        if name in self.processes and self.processes[name].poll() is None:
            return
        with (self.home / (name + '.log')).open('ab') as log:
            self.processes[name] = subprocess.Popen(command, env=env, stdout=log, stderr=log,
                                                   creationflags=FLAGS)

    def ready(self, url, name):
        for _ in range(80):
            if self.processes[name].poll() is not None:
                raise RuntimeError(f'{name} 未能启动；请查看本地日志。')
            try:
                self.request(url, timeout=1)
                return
            except Exception:
                time.sleep(.25)
        raise RuntimeError(f'{name} 启动超时。')

    def start_router(self):
        if 'router' in self.processes and self.processes['router'].poll() is None:
            return
        deps = self.dependencies()
        if not deps['ready']:
            raise RuntimeError('需要支持 node:sqlite 的 Node.js（建议22.17+）以及 kRouter 0.5.163。请先检查/安装依赖。')
        if not free_port(self.router_port):
            raise RuntimeError('工具端口已被其他进程使用，未停止任何既有服务。')
        env = {k:v for k,v in os.environ.items() if k.upper() not in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')}
        env.update(PORT=str(self.router_port), HOSTNAME='127.0.0.1', DATA_DIR=str(self.data),
                   KROUTER_SKIP_RUNTIME_HEAL='1')
        self.spawn('router', [deps['node'], '--require', str(ROOT/'runtime/stability-preload.cjs'),
                             str(Path(deps['routerApp'])/'custom-server.js')], env)
        self.ready(f'http://127.0.0.1:{self.router_port}/api/auth/status', 'router')
        private = read_json(self.home/'admin.json', {})
        password = dpapi(private['protected'], True) if private else '123456'
        try:
            self.api('/api/auth/login', {'password':password}, 'POST')
        except RuntimeError:
            pending = self.home/'admin.pending.json'
            if private or not pending.exists():
                raise
            password = dpapi(read_json(pending)['protected'], True)
            self.api('/api/auth/login', {'password':password}, 'POST')
            pending.replace(self.home/'admin.json')
            private = read_json(self.home/'admin.json')
        if not private:
            password = secrets.token_urlsafe(24)
            # Persist before change so failures can be recovered explicitly.
            write_json(self.home/'admin.pending.json', {'protected':dpapi(password)})
            self.api('/api/settings', {'currentPassword':'123456', 'newPassword':password,
                                      'outboundProxyEnabled':False}, 'PATCH')
            (self.home/'admin.pending.json').replace(self.home/'admin.json')
            self.api('/api/auth/login', {'password':password}, 'POST')

    def configure(self, body):
        with self.lock:
            previous = dict(self.config)
            previous_key = self.key() if previous else None
            self.created = []
            self.configuration_mutated = False
            try:
                return self._configure(body)
            except Exception:
                if not self.configuration_mutated:
                    raise
                self.last_test = None
                self.stop_bridge()
                if previous:
                    self.config = previous
                    write_json(self.config_file, previous)
                    try:
                        self.api('/api/provider-nodes/' + previous['nodeId'],
                                 {'baseUrl':previous['baseUrl'],'name':'Kiro API Connector',
                                  'prefix':previous.get('prefix','kiro-connector'),'apiType':'chat'}, 'PUT')
                        self.api('/api/providers/' + previous['connectionId'],
                                 {'apiKey':previous_key,'defaultModel':previous['defaultModel'],
                                  'providerSpecificData':{'baseUrl':previous['baseUrl']}}, 'PUT')
                        self.start_bridge()
                    except Exception:
                        raise RuntimeError('配置失败且自动恢复未完成；已保留原配置文件，请先检查状态与本地日志。') from None
                else:
                    self.wanted = False
                    self.config = {}
                    self.config_file.unlink(missing_ok=True)
                    failures=[]
                    for path in reversed(self.created):
                        try: self.api(path, method='DELETE')
                        except Exception: failures.append(path)
                    if failures:
                        raise RuntimeError('配置失败，部分独立路由对象未能清理；请检查本工具数据目录后重试。') from None
                raise

    def _configure(self, body):
        with self.lock:
            base = validate_url(body.get('baseUrl',''))
            ids = list(dict.fromkeys(body.get('models', [])))
            default = body.get('defaultModel')
            context_budget = body.get('contextBudget', self.config.get('contextBudget', 200000) if self.config else 200000)
            if isinstance(context_budget, bool) or not isinstance(context_budget, int) or not 8000 <= context_budget <= 2000000:
                raise ValueError('上下文预算需为 8000 到 2000000 之间的整数 token。')
            if not ids or default not in ids or len(ids)>100 or any(not isinstance(x,str) or not x or len(x)>200 for x in ids):
                raise ValueError('请选择模型与有效的默认模型，最多100个。')
            key = body.get('apiKey') or (self.key() if self.config else '')
            if not key:
                raise ValueError('请填写 API key。')
            # Verify credentials before touching saved configuration.
            try:
                available = self.models(base, key)
                if any(x not in available for x in ids):
                    raise ValueError('所选模型不在这个 key 的模型目录中。')
            except InvalidJsonResponse:
                if not body.get('manualModels'):
                    raise
            except RequestFailure as error:
                if not body.get('manualModels') or error.status not in (404,405,501):
                    raise
            self.start_router()
            self.configuration_mutated = True
            if self.config:
                self.stop_bridge()
                self.api('/api/provider-nodes/' + self.config['nodeId'],
                         {'baseUrl':base, 'name':'Kiro API Connector',
                          'prefix':self.config['prefix'],'apiType':'chat'}, 'PUT')
                self.api('/api/providers/' + self.config['connectionId'],
                         {'apiKey':key,'defaultModel':default,
                          'providerSpecificData':{'baseUrl':base,'connectionProxyEnabled':False}}, 'PUT')
                node_id, connection_id, router_key = (self.config[k] for k in ('nodeId','connectionId','routerKeyId'))
            else:
                nodes = self.api('/api/provider-nodes')['nodes']
                if any(n.get('prefix')=='kiro-connector' for n in nodes):
                    raise RuntimeError('发现未完成的本工具配置，请先检查本地数据，未覆盖。')
                node = self.api('/api/provider-nodes', {'name':'Kiro API Connector','prefix':'kiro-connector',
                       'type':'openai-compatible','apiType':'chat','baseUrl':base}, 'POST')['node']
                node_id = node['id']
                self.created.append('/api/provider-nodes/' + node_id)
                connection = self.api('/api/providers', {'provider':node_id,'apiKey':key,
                             'name':'Kiro API Connector','defaultModel':default}, 'POST')
                connection_id = connection.get('connection', connection).get('id')
                if not connection_id:
                    raise RuntimeError('供应商创建结果不符合预期，已创建对象保留在独立数据目录。')
                self.created.append('/api/providers/' + connection_id)
                created = self.api('/api/keys', {'name':'Kiro API Connector'}, 'POST')
                router_key = next(x['id'] for x in self.api('/api/keys')['keys'] if x.get('key')==created['key'])
                self.created.append('/api/keys/' + router_key)
            config = {'baseUrl':base,'models':ids,'defaultModel':default,'prefix':'kiro-connector',
                      'nodeId':node_id,'connectionId':connection_id,'routerKeyId':router_key,
                      'dataDir':str(self.data),'routerPort':self.router_port,'bridgePort':self.bridge_port,
                      'routerApp':self.dependencies()['routerApp'],
                      'effortModels':[x for x in body.get('effortModels',[]) if x in ids],
                      'contextBudget':context_budget}
            write_json(self.config_file, config)
            self.config = config
            self.last_test = None
            self.start_bridge()
            self.test()
            return self.status()

    def start_bridge(self):
        if not self.config:
            raise RuntimeError('请先保存连接配置。')
        self.wanted = True
        self.start_router()
        if 'bridge' in self.processes and self.processes['bridge'].poll() is None:
            return
        if not free_port(self.bridge_port):
            raise RuntimeError('桥接端口已被其他进程使用。')
        env = dict(os.environ, KIRO_CONNECTOR_CONFIG=str(self.config_file))
        self.spawn('bridge', [self.dependencies()['node'], str(ROOT/'runtime/kiro_local_bridge.js')], env)
        self.ready(f'http://127.0.0.1:{self.bridge_port}/health', 'bridge')
        self.last_error = None

    def maintain(self):
        while True:
            time.sleep(5)
            with self.lock:
                if self.wanted and any(proc.poll() is not None for proc in self.processes.values()):
                    try:
                        self.start_bridge()
                    except Exception:
                        self.last_error = '子服务恢复失败，请检查状态并手动启动。'

    def stop_bridge(self):
        proc = self.processes.pop('bridge', None)
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)

    def test(self):
        with self.lock:
            self.start_bridge()
            # Reuse real protocol/CRC verification; model IDs never enter a shell.
            source = (ROOT/'runtime/verify.py').read_text(encoding='utf8').replace('20129',str(self.bridge_port))
            target = self.home/'verify-local.py'
            target.write_text(source, encoding='utf8')
            command = [sys.executable,str(target),'--model',self.config['defaultModel']]
            if self.config['defaultModel'] in self.config['effortModels']:
                command += ['--effort','high']
            result = subprocess.run(command,
                     capture_output=True, timeout=260, creationflags=FLAGS)
            if result.returncode:
                self.last_test = None
                raise RuntimeError('模型实际推理或 Kiro 协议测试失败；未自动修改 Kiro 设置。')
            self.last_test = {'model':self.config['defaultModel'],'time':time.strftime('%Y-%m-%d %H:%M:%S')}
            return self.last_test

    def apply(self):
        with self.lock:
            if not self.last_test:
                raise RuntimeError('请先通过实际推理测试。')
            current = read_json(self.settings, {})
            endpoint = f'http://127.0.0.1:{self.bridge_port}'
            if not self.backup.exists():
                for key in ENDPOINT_KEYS:
                    for item in current.get(key, []):
                        existing = item.get('endpoint','')
                        if existing and existing != endpoint:
                            raise RuntimeError('Kiro 已使用另一套自定义桥接，未覆盖。请先停用原桥接。')
                write_json(self.backup, {'settings':str(self.settings),'existed':self.settings.exists(),
                                        'values':{k:current[k] for k in ENDPOINT_KEYS if k in current}})
            for key in ENDPOINT_KEYS:
                current[key] = [{'region':'us-east-1','endpoint':endpoint}]
            write_json(self.settings, current)
            return self.status()

    def restore(self):
        if not self.backup.exists():
            return
        record = read_json(self.backup)
        path = Path(record['settings'])
        current = read_json(path,{})
        expected = [{'region':'us-east-1','endpoint':f'http://127.0.0.1:{self.bridge_port}'}]
        for key in ENDPOINT_KEYS:
            if current.get(key) != expected:
                raise RuntimeError('endpoint 已被其他程序修改，保留备份，未覆盖后续改动。')
        for key in ENDPOINT_KEYS:
            if key in record['values']:
                current[key] = record['values'][key]
            else:
                current.pop(key,None)
        if not record.get('existed',True) and not current:
            path.unlink(missing_ok=True)
        else:
            write_json(path,current)
        self.backup.unlink()

    def stop(self):
        with self.lock:
            self.restore()
            self.wanted = False
            self.stop_bridge()
            proc = self.processes.pop('router', None)
            if proc and proc.poll() is None:
                proc.terminate(); proc.wait(timeout=10)
            self.last_test = None
            return self.status()

    def shutdown(self):
        """Stop owned services for an update; keep Kiro endpoints and backup."""
        with self.lock:
            self.shutting_down = True
            self.wanted = False
            self.stop_bridge()
            proc = self.processes.pop('router', None)
            if proc and proc.poll() is None:
                proc.terminate(); proc.wait(timeout=10)

    def update(self):
        if not self.dependencies()['bundled']:
            raise RuntimeError('源码目录请执行 git pull --ff-only，再运行 setup.ps1 或 setup.sh。')
        updater = ROOT / 'upgrade.py'
        if not updater.exists():
            raise RuntimeError('当前安装没有升级入口，请先换用带“升级到最新版.cmd”的新版便携包。')
        result = subprocess.run([sys.executable, str(updater)], capture_output=True,
                                timeout=180, creationflags=FLAGS)
        if result.returncode:
            detail = (result.stdout or result.stderr or '').strip().splitlines()
            detail = detail[-1] if detail else '请检查网络或仓库访问权限。'
            raise RuntimeError('更新失败，未重启桥接：' + detail)
        if self.config:
            self.stop_bridge()
            self.start_bridge()
        return self.status()

    def status(self):
        return {'configured':bool(self.config),'config':{k:v for k,v in self.config.items()
                if k in ('baseUrl','models','defaultModel','effortModels','contextBudget')},
                'running':{k:p.poll() is None for k,p in self.processes.items()},
                'enabled':self.backup.exists(),'lastTest':self.last_test,
                'dashboard':f'http://127.0.0.1:{self.router_port}/dashboard/usage',
                'dataDir':str(self.home), 'dependencies':self.dependencies(),'lastError':self.last_error,
                'platform':sys.platform,'autostart':startup_path().exists(),
                'updateSupported':bool(self.dependencies()['bundled'] and (ROOT/'upgrade.py').exists())}

    def autostart(self, enabled):
        if sys.platform=='darwin':
            startup=startup_path()
            command=[sys.executable,str(ROOT/'app.py'),'--home',str(self.home),'--no-browser']
            if startup.exists():
                old=plistlib.loads(startup.read_bytes())
                if old.get('ProgramArguments')!=command:
                    raise RuntimeError('自启项不属于当前安装，未修改。')
            if enabled:
                startup.parent.mkdir(parents=True,exist_ok=True)
                plist={'Label':'club.kiro-api-connector','ProgramArguments':command,'RunAtLoad':True,
                       'WorkingDirectory':str(ROOT),'EnvironmentVariables':{'PATH':os.environ.get('PATH','')+':/opt/homebrew/bin:/usr/local/bin'},
                       'StandardOutPath':str(self.home/'startup.log'),'StandardErrorPath':str(self.home/'startup.log')}
                with startup.open('wb') as handle:plistlib.dump(plist,handle)
                startup.chmod(0o600)
            else:
                startup.unlink(missing_ok=True)
            return {'autostart':enabled}
        startup = Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/KiroApiConnector.cmd'
        if enabled:
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            if not pythonw.exists():
                raise RuntimeError('未找到 pythonw.exe。')
            startup.parent.mkdir(parents=True,exist_ok=True)
            command = f'@echo off\r\nchcp 65001 >nul\r\nstart "" "{pythonw}" "{ROOT / "app.py"}" --home "{self.home}" --no-browser\r\n'
            if startup.exists() and startup.read_text(encoding='utf8') != command:
                raise RuntimeError('同名自启项已存在，未覆盖。')
            startup.write_text(command,encoding='utf8')
        elif startup.exists():
            if str(ROOT/'app.py') not in startup.read_text(encoding='utf8'):
                raise RuntimeError('自启项不属于当前安装，未删除。')
            startup.unlink()
        return {'autostart':enabled}

    def install(self):
        deps = self.dependencies()
        if not deps['node'] or not deps['npm'] or not deps['sqlite']:
            raise RuntimeError('请先安装 Node.js 22.17+，重新打开工具。')
        if deps['ready']:
            return deps
        env = {k:v for k,v in os.environ.items() if k.upper() not in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')}
        env['PATH']=str(Path(deps['node']).parent)+os.pathsep+env.get('PATH','')
        with (self.home/'install.log').open('ab') as log:
            result = subprocess.run([deps['npm'],'install','--prefix',str(ROOT/'runtime'),
                     '@sifxprime/krouter@0.5.163'],env=env,stdout=log,stderr=log,
                     timeout=600,creationflags=FLAGS)
        if result.returncode:
            raise RuntimeError('依赖安装失败，请查看 install.log。')
        return self.dependencies()


def handler(connector, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body, kind='application/json'):
            payload = json.dumps(body,ensure_ascii=False).encode() if kind=='application/json' else body
            self.send_response(status)
            self.send_header('Content-Type',kind+'; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY')
            self.end_headers(); self.wfile.write(payload)

        def trusted(self):
            return self.headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}')

        def do_GET(self):
            if not self.trusted():
                return self.send(403,{'error':'Host rejected'})
            if self.path=='/health':
                return self.send(200,{'app':'kiro-api-connector'})
            if self.path=='/':
                page=(ROOT/'web/index.html').read_text(encoding='utf8').replace('__TOKEN__',connector.token)
                return self.send(200,page.encode(),'text/html')
            if self.path=='/api/status' and secrets.compare_digest(self.headers.get('X-Setup-Token',''),connector.token):
                return self.send(200,connector.status())
            self.send(404,{'error':'Not found'})

        def do_POST(self):
            origin = self.headers.get('Origin')
            if not self.trusted() or (origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}')) or not secrets.compare_digest(self.headers.get('X-Setup-Token',''),connector.token):
                return self.send(403,{'error':'本地配置请求认证失败，请刷新页面。'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 65536:
                    raise ValueError('Invalid body size')
                body=json.loads(self.rfile.read(length))
                with connector.lock:
                    if self.path=='/api/models': result={'models':connector.models(body['baseUrl'],body.get('apiKey',''))}
                    elif self.path=='/api/configure': result=connector.configure(body)
                    elif self.path=='/api/test': result=connector.test()
                    elif self.path=='/api/enable': result=connector.apply()
                    elif self.path=='/api/start': connector.start_bridge();result=connector.status()
                    elif self.path=='/api/stop': result=connector.stop()
                    elif self.path=='/api/shutdown':
                        connector.shutdown(); result={'ok':True}
                        threading.Thread(target=self.server.shutdown,daemon=True).start()
                    elif self.path=='/api/autostart': result=connector.autostart(bool(body['enabled']))
                    elif self.path=='/api/update': result=connector.update()
                    elif self.path=='/api/install': result=connector.install()
                    elif self.path=='/api/admin-password': result={'password':dpapi(read_json(connector.home/'admin.json')['protected'],True)}
                    elif self.path=='/api/open-logs':
                        if sys.platform=='darwin':subprocess.Popen(['open',str(connector.home)])
                        else:os.startfile(connector.home)
                        result={'ok':True}
                    else: return self.send(404,{'error':'Not found'})
                self.send(200,result)
            except (RuntimeError,ValueError) as error:
                self.send(400,{'error':str(error)})
            except Exception as error:
                self.send(500,{'error':'操作失败（'+type(error).__name__+'）；已保留本地配置，请查看运行状态。'})
    return Handler


def main():
    if sys.version_info < (3,11):
        raise RuntimeError('需要 Python 3.11+，请更新 Python 后重新打开。')
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,default=user_paths()[0])
    parser.add_argument('--port',type=int,default=20147)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--takeover',action='store_true')
    args=parser.parse_args()
    connector=Connector(args.home)
    reenable = False
    try:
        server=LocalServer(('127.0.0.1',args.port),handler(connector,args.port))
    except OSError:
        if args.takeover:
            reenable = takeover_existing(args.port)
            server=LocalServer(('127.0.0.1',args.port),handler(connector,args.port))
        else:
            server=None
    if server is None:
        try:
            health=connector.request(f'http://127.0.0.1:{args.port}/health',timeout=2)
            if health.get('app')=='kiro-api-connector':
                if not args.no_browser:
                    webbrowser.open(f'http://127.0.0.1:{args.port}')
                return
        except Exception:
            pass
        raise RuntimeError('配置界面端口已被占用，请检查既有工具实例。') from None
    if connector.config:
        try:
            connector.start_bridge()
            if reenable:
                connector.test()
                connector.apply()
        except Exception:
            connector.last_error = '新桥接启动或验证失败，请在配置页重新测试并启用。'
    if not args.no_browser:
        webbrowser.open(f'http://127.0.0.1:{args.port}')
    threading.Thread(target=connector.maintain,daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if not connector.shutting_down:
            connector.stop()


if __name__=='__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        message=str(error) if isinstance(error,RuntimeError) else '启动失败（'+type(error).__name__+'），请检查本机配置。'
        if os.name=='nt':
            ctypes.windll.user32.MessageBoxW(None,message,'Kiro API Connector',0x10)
        elif sys.stderr:
            print(message,file=sys.stderr)
        raise SystemExit(1)
