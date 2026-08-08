# tests/test_kingdee_api.py
# 金蝶 WebAPI 元数据客户端测试(mock 响应,不连真实环境)。
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
