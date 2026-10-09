"""Gracefully stop the local wizard and services it owns."""
import re
import urllib.request

base = "http://127.0.0.1:20147"
try:
    page = urllib.request.urlopen(base + "/", timeout=3).read().decode("utf-8", "replace")
    match = re.search(r"const token='([^']+)'", page)
    if not match or "Kiro API Connector" not in page:
        raise RuntimeError("20147 不是本工具的配置向导，未停止。")
    request = urllib.request.Request(base + "/api/shutdown", data=b"{}", method="POST",
        headers={"Content-Type":"application/json", "Origin":base, "X-Setup-Token":match.group(1)})
    urllib.request.urlopen(request, timeout=8).read()
    print("已停止配置向导及本工具启动的桥接服务。")
except Exception as error:
    print(f"停止失败：{error}")
    print("如果服务已经停止，可以忽略此提示。")
