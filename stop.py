"""Gracefully stop the local wizard and services it owns."""
import re
import socket
import subprocess
import urllib.request

base = "http://127.0.0.1:20147"

def free(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0

def cleanup_orphans():
    """Clean listeners left by an older connector instance on reserved ports."""
    ports = (20148, 20149)
    if all(free(port) for port in ports):
        return
    if __import__('os').name == 'nt':
        output = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True,
                                         stderr=subprocess.DEVNULL)
        pids = set()
        for line in output.splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[3] == "LISTENING" and any(
                fields[1].endswith(":" + str(port)) for port in ports):
                pids.add(fields[4])
        for pid in pids:
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        output = subprocess.check_output(["lsof", "-tiTCP:20148", "-tiTCP:20149", "-sTCP:LISTEN"],
                                         text=True, stderr=subprocess.DEVNULL)
        for pid in {x.strip() for x in output.splitlines() if x.strip().isdigit()}:
            subprocess.run(["kill", pid])
try:
    page = urllib.request.urlopen(base + "/", timeout=3).read().decode("utf-8", "replace")
    match = re.search(r"const token='([^']+)'", page)
    if not match or "Kiro API Connector" not in page:
        raise RuntimeError("20147 不是本工具的配置向导，未停止。")
    request = urllib.request.Request(base + "/api/shutdown", data=b"{}", method="POST",
        headers={"Content-Type":"application/json", "Origin":base, "X-Setup-Token":match.group(1)})
    urllib.request.urlopen(request, timeout=8).read()
    cleanup_orphans()
    if not free(20148) or not free(20149):
        raise RuntimeError("向导已停止，但 20148/20149 仍被占用，请检查旧版进程。")
    print("已停止配置向导及本工具启动的桥接服务。")
except Exception as error:
    print(f"停止失败：{error}")
    print("如果服务已经停止，可以忽略此提示。")
