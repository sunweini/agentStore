# tests/test_kingdee_api.py
# 金蝶 WebAPI 元数据客户端测试(mock 响应,不连真实环境)。
import json
import zipfile

import pytest
from agents.kingdee_plugin_agent.tools.kingdee_api import KingdeeApiClient, KingdeeApiUnavailable


def _client(**kw):
    """构造客户端,acct_id 为第二参数(旧式 username/password/dc 签名已弃用)。"""
    return KingdeeApiClient(kw.get("base_url", "http://k3"),
                            kw.get("acct_id", "ACCT1"),
                            kw.get("username", "u"),
                            kw.get("password", "p"))


def _resp(status=200, text=None, obj=None):
    r = type("R", (), {"status_code": status})()
    if obj is not None:
        r.json = lambda: obj
        r.text = json.dumps(obj, ensure_ascii=False)  # 保中文(会话失效检测依赖原文)
    else:
        r.json = lambda: (_ for _ in ()).throw(ValueError("Expecting value"))
        r.text = text or ""
    return r


def _login_resp(sid="sid-1"):
    return _resp(obj={"LoginResultType": 1, "KDSVCSessionId": sid,
                      "Message": None})


class _FakeSession:
    """按 URL 分发的 fake session:登录请求返回 KDSVCSessionId,其余走 handler。

    复用真实 URL 常量:URL 含 AuthService.ValidateUser 视为登录,否则转 handler。
    """

    def __init__(self, handler):
        self.handler = handler
        self.post_calls = []

    def post(self, url, **kw):
        self.post_calls.append((url, kw))
        if KingdeeApiClient._LOGIN in url:
            return _login_resp()
        return self.handler(url, kw)


def test_client_parses_form_fields_from_query_biz_info(monkeypatch):
    """get_form_fields 经 QueryBusinessInfo 解析:FBillHead 平铺,分录带前缀。"""
    client = _client()
    meta = {"Result": {"ResponseStatus": {"IsSuccess": True}, "NeedReturnData": {
        "Entrys": [
            {"Key": "FBillHead", "ParentKey": None,
             "Name": [{"Key": 2052, "Value": "基本信息"}],
             "Fields": [
                 {"Key": "FBillNo", "Name": [{"Key": 2052, "Value": "单据编号"}],
                  "FieldType": 231, "ElementType": 12},
                 {"Key": "FCustId", "Name": [{"Key": 2052, "Value": "客户"}],
                  "FieldType": 1, "LookUpObjectFormId": "BD_Customer"},
             ]},
            {"Key": "FSaleOrderEntry", "ParentKey": None,
             "Name": [{"Key": 2052, "Value": "明细"}],
             "Fields": [
                 {"Key": "FMaterialId", "Name": [{"Key": 2052, "Value": "物料"}],
                  "FieldType": 1, "LookUpObjectFormId": "BD_Material"},
             ]},
            {"Key": "FChild", "ParentKey": "FSaleOrderEntry",  # 子分录不展开
             "Fields": [{"Key": "FQty", "Name": []}]},
        ]
    }}}

    monkeypatch.setattr(client.session, "post", lambda url, **k: _resp(obj=meta))
    client._session_id = "sid-1"  # 预设会话,绕过登录
    fields = client.get_form_fields("SAL_SaleOrder")
    assert fields[0].field_name == "FBillNo"
    assert fields[0].field_label == "单据编号"
    assert fields[0].data_type == "FieldType=231,ElementType=12"
    assert fields[1].field_name == "FCustId"
    assert fields[1].data_type == "BaseField->BD_Customer"  # 关联字段语义
    assert fields[2].field_name == "FSaleOrderEntry.FMaterialId"  # 分录带前缀
    assert len(fields) == 3  # 子分录忽略


def test_client_legacy_body_auth_rejected():
    """旧式「请求体带 userName/password/dc」已弃用:dc 不再作为构造参数,
    第四位置参数是 lcid(默认 2052)。"""
    c = KingdeeApiClient("http://k3", "a", "u", "p")
    assert c._lcid == 2052
    assert c._acct_id == "a"  # 第二参数是 acctID 而非旧式 dc


def test_client_login_failure_raises(monkeypatch):
    """登录失败(LoginResultType != 1)→ KingdeeApiUnavailable。"""
    client = _client()
    monkeypatch.setattr(client.session, "post", lambda url, **k:
                        _resp(obj={"LoginResultType": 0, "Message": "密码错误"}))
    with pytest.raises(KingdeeApiUnavailable, match="密码错误"):
        client.get_form_fields("X")


def test_client_session_expired_relogin(monkeypatch):
    """会话失效(响应含「会话」)→ 自动重登 1 次再发,成功返回。"""
    client = _client()
    calls = {"n": 0}

    def handler(url, kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp(obj=[{"Result": {"ResponseStatus": {
                "IsSuccess": False, "Errors": [{"Message": "会话信息已丢失，请重新登录"}]}}}])
        if calls["n"] == 2:  # 重登后重发
            return _resp(obj=[{"Result": {"ResponseStatus": {"IsSuccess": True},
                                          "ValidationResults": []}}])
        raise AssertionError(f"不应第 {calls['n']} 次调用")

    monkeypatch.setattr(client.session, "post", _FakeSession(handler).post)
    r = client._post("/K3Cloud/test.kdsvc", {"formid": "X"})  # 任一 _post 路径即可
    assert r == {"ResponseStatus": {"IsSuccess": True}, "ValidationResults": []}
    assert calls["n"] == 2  # 首次失败 + 重登后 1 次


def test_client_non_json_200_response_raises(monkeypatch):
    """HTTP 200 但响应体非 JSON(如 HTML 错误页)→ KingdeeApiUnavailable。"""
    client = _client()

    def handler(url, kw):
        return _resp(200, text="<html>gateway error</html>")

    monkeypatch.setattr(client.session, "post", _FakeSession(handler).post)
    with pytest.raises(KingdeeApiUnavailable):
        client.get_form_fields("SAL_PurchaseOrder")


def test_client_response_error_text_raises(monkeypatch):
    """服务端 response_error 纯文本(占位端点)→ KingdeeApiUnavailable,含原因。"""
    client = _client()

    def handler(url, kw):
        return _resp(200, text="response_error:发生时间：2026-08-10\n错误编号：abc")

    monkeypatch.setattr(client.session, "post", _FakeSession(handler).post)
    with pytest.raises(KingdeeApiUnavailable, match="response_error"):
        client._post("/K3Cloud/test.kdsvc", {"formid": "X"})


def test_client_429_retries_then_raises(monkeypatch):
    """429 重试 2 次后放弃(共 3 次查询,不含登录)。"""
    client = _client()
    calls = {"n": 0}

    def handler(url, kw):
        calls["n"] += 1
        return _resp(429)

    monkeypatch.setattr(client.session, "post", _FakeSession(handler).post)
    with pytest.raises(KingdeeApiUnavailable):
        client.get_form_fields("X")
    assert calls["n"] == 3  # 1 次 + 2 次退避重试


def test_client_arrays_response_normalized(monkeypatch):
    """ExecuteBillQuery 数组响应归一化:成功数组不含 ResponseStatus。"""
    client = _client()
    client._session_id = "sid-1"  # 预设会话,绕过登录
    # 数组内无 Result → 整体保留(数据行),不误判 IsSuccess
    monkeypatch.setattr(client.session, "post", lambda url, **k:
                        _resp(obj=[["XSDD000019", "2021-11-09"], ["XSDD000025", "2021-11-10"]]))
    data = client._post("/K3Cloud/test.kdsvc", {"formid": "X"})
    assert data == [["XSDD000019", "2021-11-09"], ["XSDD000025", "2021-11-10"]]


def test_client_url_k3cloud_prefix_normalized():
    """KD_BASE_URL 带 /k3cloud/ 前缀 → 归一到主机根,避免双重路径。"""
    c1 = KingdeeApiClient("http://k3/k3cloud/", "a", "u", "p")
    c2 = KingdeeApiClient("http://k3/", "a", "u", "p")
    assert c1.base_url == c2.base_url == "http://k3"


def test_no_env_no_client():
    assert KingdeeApiClient.client_from_env_or_none() is None  # 无 env 返回 None(硬门槛信号)


def test_client_from_env_env_vars(monkeypatch):
    """env 分套:KD_BASE_URL_TEST 优先于 KD_BASE_URL。"""
    import common.config as config
    monkeypatch.setenv("KD_BASE_URL", "http://default/k3cloud/")
    monkeypatch.setenv("KD_BASE_URL_TEST", "http://test/k3cloud/")
    monkeypatch.setenv("KD_USERNAME_TEST", "t-user")
    c = KingdeeApiClient.client_from_env_or_none(env="test")
    assert c is not None
    assert c.base_url == "http://test"
    assert c._username == "t-user"


def test_client_from_env_no_env_falls_back(monkeypatch):
    """env 空回落默认 KD_*。"""
    monkeypatch.setenv("KD_BASE_URL", "http://default/k3cloud/")
    monkeypatch.setenv("KD_USERNAME", "d-user")
    c = KingdeeApiClient.client_from_env_or_none(env="")
    assert c is not None
    assert c.base_url == "http://default"
    assert c._username == "d-user"


# --- B8: 冒烟客户端 + 打包工具 ---
from agents.kingdee_plugin_agent.tools.smoke_client import SmokeClient
from agents.kingdee_plugin_agent.tools.package import PackageBuilder


def _pe_dll(tmp_path, name="p.dll"):
    """构造 PE 头假 DLL(MZ 开头,过 PE 校验)。"""
    dll = tmp_path / name
    dll.write_bytes(b"MZ\x90\x00" + b"\x00" * 64)  # PE 头 + 填充
    return dll


def test_smoke_verify(monkeypatch, tmp_path):
    """冒烟通过:PE DLL + FormId 真实(QueryBusinessInfo 返回字段)。"""
    client = SmokeClient(_client())
    dll = _pe_dll(tmp_path)
    # 真实实现调 get_form_fields(QueryBusinessInfo)验证目标单据
    monkeypatch.setattr(client.api, "get_form_fields", lambda form_id: [_f()])
    r = client.deploy_and_verify(dll, "SAL_PurchaseOrder")
    assert r.ok is True
    assert "PE" in r.detail and "337" not in r.detail  # detail 含产物校验结论


def _f():
    from agents.kingdee_plugin_agent.tools.kingdee_api import FieldInfo
    return FieldInfo("FBillNo", "单据编号", "FieldType=231")


def test_smoke_missing_dll_fails(tmp_path):
    client = SmokeClient(_client())
    missing = tmp_path / "nope.dll"
    r = client.deploy_and_verify(missing, "SAL_PurchaseOrder")
    assert r.ok is False
    assert str(missing) in r.detail  # detail 指明缺失路径


def test_smoke_non_pe_dll_fails(tmp_path):
    """源码/空壳冒充 DLL(非 PE 头)→ 拒绝。"""
    client = SmokeClient(_client())
    dll = tmp_path / "fake.dll"
    dll.write_bytes(b"mock-dll")  # 非 MZ 开头
    r = client.deploy_and_verify(dll, "SAL_PurchaseOrder")
    assert r.ok is False
    assert "PE" in r.detail


def test_smoke_empty_form_id_fails(tmp_path):
    """form_id 为空(需求未提取目标单据)→ 冒烟失败。"""
    client = SmokeClient(_client())
    dll = _pe_dll(tmp_path)
    r = client.deploy_and_verify(dll, "")
    assert r.ok is False
    assert "form_id" in r.detail


def test_smoke_api_unavailable_fails(monkeypatch, tmp_path):
    client = SmokeClient(_client())
    dll = _pe_dll(tmp_path)
    def boom(*a, **k):
        raise KingdeeApiUnavailable("金蝶 API 错误:HTTP 500")
    monkeypatch.setattr(client.api, "get_form_fields", boom)
    r = client.deploy_and_verify(dll, "SAL_PurchaseOrder")
    assert r.ok is False
    assert "HTTP 500" in r.detail


def test_package_build(tmp_path):
    builder = PackageBuilder(output_dir=tmp_path)
    dll = tmp_path / "Plugin.dll"
    dll.write_bytes(b"PE")
    p = builder.build({"code": "x", "dll_path": dll, "design": {}, "review": {},
                       "spec_version": 1, "requirement_spec": {"requirement": "审核校验"}})
    assert p.suffix == ".zip" and p.exists()
    # 锁定 6 条目契约:源码 + DLL + 部署说明 + 设计/审查记录 + 需求版本冻结记录
    with zipfile.ZipFile(p) as z:
        assert set(z.namelist()) == {"source/Plugin.cs", "bin/Plugin.dll", "deploy.md",
                                     "records/design.json", "records/review.json",
                                     "records/spec.json"}
        spec = json.loads(z.read("records/spec.json"))
        assert spec["spec_version"] == 1                      # 冻结版本盖章
        assert spec["requirement_spec"]["requirement"] == "审核校验"
