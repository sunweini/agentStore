#!/bin/bash
# 多文件编译 E2E(需在 Windows 编译服务同步新代码并重启后运行)
# 用法: bash e2e_multifile.sh [BASE_URL];缺省 http://10.33.17.130:8100
B="${1:-http://10.33.17.130:8100}"
echo "=== 1) health ==="
curl -s -m 15 "$B/health"; echo

echo "=== 2) 多文件 PASS:Plugin.cs 调用 Helper.cs(跨文件真实接线)==="
curl -s -m 120 -X POST "$B/compile" -H 'Content-Type: application/json' -d '{
  "files": [
    {"name": "Plugin.cs", "code": "public class Plugin { public void Run() { Helper.H(); } }"},
    {"name": "Helper.cs", "code": "public class Helper { public static void H() { } }"}
  ],
  "project_name": "mf-e2e-pass"
}' | python3 -m json.tool

echo "=== 3) 多文件 FAIL:错误在第二个文件(证明 csproj 接了 Helper.cs)==="
curl -s -m 120 -X POST "$B/compile" -H 'Content-Type: application/json' -d '{
  "files": [
    {"name": "Plugin.cs", "code": "public class Plugin { public void Run() { Helper.H(); } }"},
    {"name": "Helper.cs", "code": "public class Helper { public static void H() { int x = missing; } }"}
  ],
  "project_name": "mf-e2e-fail"
}' | python3 -m json.tool

echo "=== 4) DLL 拉取(步骤 2 产物)==="
curl -s -m 15 -o /tmp/mf-e2e-pass.dll -w "%{http_code} %{size_download}B\n" "$B/dll/mf-e2e-pass"
file /tmp/mf-e2e-pass.dll

echo "=== 5) 校验:../evil.cs → 400 ==="
curl -s -m 15 -X POST "$B/compile" -H 'Content-Type: application/json' -d '{
  "files": [{"name": "../evil.cs", "code": "x"}], "project_name": "mf-e2e-bad"}' ; echo
