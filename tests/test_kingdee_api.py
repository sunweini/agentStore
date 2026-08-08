# tests/test_kingdee_api.py
# 金蝶 WebAPI 元数据客户端测试(mock 响应,不连真实环境)。
import zipfile

import pytest
from agents.kingdee_plugin_agent.tools.kingdee_api import KingdeeApiClient, KingdeeApiUnavailable

def test_client_parses_form_fields(monkeypatch):
    client = KingdeeApiClient("http://k3", "u", "p", "dc")
    # json 以实例属性挂载(type() 类体内 lambda 会作为描述符绑定,调用时多传 self)
    resp = type("R", (), {"status_code": 200})()
    resp.json = lambda: {
        "Result": {"ResponseStatus": {"IsSuccess": True},
                   "ValidationResults": [{"FieldName": "FQty", "FieldLabel": "数量", "DataType": "Decimal"}]}}
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)
    fields = client.get_form_fields("SAL_PurchaseOrder")
    assert fields[0].field_name == "FQty"
    assert fields[0].data_type == "Decimal"

def test_client_non_json_200_response_raises(monkeypatch):
    """HTTP 200 但响应体非 JSON(如 HTML 错误页)→ KingdeeApiUnavailable,不抛裸 ValueError。"""
    client = KingdeeApiClient("http://k3", "u", "p", "dc")
    resp = type("R", (), {"status_code": 200})()
    def bad_json():
        raise ValueError("Expecting value")
    resp.json = bad_json
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)
    with pytest.raises(KingdeeApiUnavailable):
        client.get_form_fields("SAL_PurchaseOrder")


def test_client_429_retries_then_raises(monkeypatch):
    client = KingdeeApiClient("http://k3", "u", "p", "dc")
    calls = {"n": 0}
    def flaky(*a, **k):
        calls["n"] += 1
        return type("R", (), {"status_code": 429})()
    monkeypatch.setattr(client.session, "post", flaky)
    with pytest.raises(KingdeeApiUnavailable):
        client.get_form_fields("X")
    assert calls["n"] == 3  # 1 次 + 2 次退避重试

def test_no_env_no_client():
    assert KingdeeApiClient.client_from_env_or_none() is None  # 无 env 返回 None(硬门槛信号)

# --- B8: 冒烟客户端 + 打包工具 ---
from agents.kingdee_plugin_agent.tools.smoke_client import SmokeClient
from agents.kingdee_plugin_agent.tools.package import PackageBuilder

def test_smoke_verify(monkeypatch, tmp_path):
    client = SmokeClient(KingdeeApiClient("http://k3", "u", "p", "dc"))
    dll = tmp_path / "p.dll"
    dll.write_bytes(b"mock-dll")  # 存在性检查要求 DLL 真实存在
    # mock 部署+查询:assembly 加载成功(实例属性挂载,无描述符绑定问题)
    monkeypatch.setattr(client.api, "_post", lambda *a, **k: {"Result": {"IsSuccess": True}})
    r = client.deploy_and_verify(dll, "SAL_PurchaseOrder")
    assert r.ok is True

def test_smoke_missing_dll_fails(tmp_path):
    client = SmokeClient(KingdeeApiClient("http://k3", "u", "p", "dc"))
    missing = tmp_path / "nope.dll"
    r = client.deploy_and_verify(missing, "SAL_PurchaseOrder")
    assert r.ok is False
    assert str(missing) in r.detail  # detail 指明缺失路径

def test_smoke_api_unavailable_fails(monkeypatch, tmp_path):
    client = SmokeClient(KingdeeApiClient("http://k3", "u", "p", "dc"))
    dll = tmp_path / "p.dll"
    dll.write_bytes(b"mock-dll")
    def boom(*a, **k):
        raise KingdeeApiUnavailable("金蝶 API 错误:HTTP 500")
    monkeypatch.setattr(client.api, "_post", boom)
    r = client.deploy_and_verify(dll, "SAL_PurchaseOrder")
    assert r.ok is False
    assert "HTTP 500" in r.detail

def test_package_build(tmp_path):
    builder = PackageBuilder(output_dir=tmp_path)
    dll = tmp_path / "Plugin.dll"
    dll.write_bytes(b"PE")
    p = builder.build({"code": "x", "dll_path": dll, "design": {}, "review": {}})
    assert p.suffix == ".zip" and p.exists()
    # 锁定 5 条目契约:源码 + DLL + 部署说明 + 设计/审查记录
    with zipfile.ZipFile(p) as z:
        assert set(z.namelist()) == {"source/Plugin.cs", "bin/Plugin.dll", "deploy.md",
                                     "records/design.json", "records/review.json"}
