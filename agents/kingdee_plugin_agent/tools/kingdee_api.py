"""金蝶云星空 WebAPI 元数据客户端(只读,不写业务数据)。

调用流:
  预登录 ValidateUser(acctID/userName/password/lcid)→ 得 KDSVCSessionId
  ──► 元数据请求(form-urlencoded `data=JSON` + Cookie kdservice-sessionid)
  ──► 会话失效自动重登一次 ──► 429/5xx/超时指数退避(2 次重试)──► KingdeeApiUnavailable

⚠️ 端点验证状态(2026-08-10 真实实例 10.33.17.130,星空 9.0.252.12 实测):
  - ValidateUser 登录       ✅ 可用(响应顶层 KDSVCSessionId)
  - ExecuteBillQuery        ✅ 可用(响应为数组的数组,如 [["XSDD000019","2021-11-09"],...])
  - QueryBusinessInfo       ✅ 可用(字段元数据,Result.NeedReturnData.Entrys[])
  官方 SDK 支持列表(2024-06 手册)无 GetFormOperations / QueryBusinessObjects,
  已移除这两个占位方法。旧式「请求体携带 userName/password/dc」认证在本实例
  已失效(报「会话信息已丢失」),必须走 ValidateUser 预登录;httpx 0.28+ 默认
  HTTP/2 金蝶不支持,须显式 http1=True。
"""
import os
import time
from dataclasses import dataclass

import httpx


class KingdeeApiUnavailable(RuntimeError):
    """金蝶 API 不可用(网络/超时/429 重试超限/登录失败/业务错误)。"""


@dataclass
class FieldInfo:
    field_name: str
    field_label: str
    data_type: str


class KingdeeApiClient:
    """金蝶云星空 WebAPI 客户端(只读元数据查询)。

    认证:ValidateUser 预登录拿 KDSVCSessionId,后续请求带 Cookie
    `kdservice-sessionid=<session>`,请求体为 form-urlencoded 的 `data=JSON`。
    旧式请求体带凭证方式已弃用(实测被服务端拒绝)。
    """

    #: 登录端点(✅ 实测可用):parameters=[acctID, userName, password, lcid]
    _LOGIN = "/K3Cloud/Kingdee.BOS.WebApi.ServicesStub.AuthService.ValidateUser.common.kdsvc"
    #: 数据查询端点(✅ 实测可用):响应为数组的数组
    _EXECUTE_BILL_QUERY = "/K3Cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc"
    #: 字段元数据端点(✅ 实测可用):Result.NeedReturnData.Entrys[]
    _QUERY_BIZ_INFO = "/K3Cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.QueryBusinessInfo.common.kdsvc"

    def __init__(self, base_url: str, acct_id: str, username: str, password: str,
                 lcid: int = 2052, timeout: float = 120.0):
        # KD_BASE_URL 允许带 /k3cloud/ 前缀或裸主机,统一归一到主机根(路径自带 /K3Cloud/)
        self.base_url = base_url.rstrip("/")
        for suffix in ("/k3cloud", "/K3Cloud"):
            if self.base_url.lower().endswith(suffix):
                self.base_url = self.base_url[: -len(suffix)]
                break
        # http1=True:金蝶不支持 HTTP/2,httpx 0.28+ 默认 HTTP/2 会全 502/超时
        self.session = httpx.Client(timeout=timeout,
                                    transport=httpx.HTTPTransport(http1=True))
        self._acct_id = acct_id
        self._username = username
        self._password = password
        self._lcid = lcid
        self._session_id: str | None = None

    # ── 认证 ──────────────────────────────────────────────────────────────
    def _login(self) -> str:
        """ValidateUser 预登录,返回 KDSVCSessionId;失败抛 KingdeeApiUnavailable。"""
        r = self.session.post(self.base_url + self._LOGIN,
                              json={"parameters": [self._acct_id, self._username,
                                                   self._password, self._lcid]})
        if r.status_code != 200:
            raise KingdeeApiUnavailable(f"金蝶登录失败:HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            raise KingdeeApiUnavailable("金蝶登录响应非 JSON") from None
        if data.get("LoginResultType") != 1:
            raise KingdeeApiUnavailable(f"金蝶登录失败:{data.get('Message', '未知错误')}")
        self._session_id = data["KDSVCSessionId"]
        return self._session_id

    def _session_cookie(self) -> str:
        if not self._session_id:
            self._login()
        return f"kdservice-sessionid={self._session_id}"

    # ── 请求 ──────────────────────────────────────────────────────────────
    def _post(self, path: str, body: dict) -> dict:
        """POST 元数据接口:会话失效自动重登 1 次 + 2 次指数退避(429/5xx/超时)。

        金蝶 WebAPI 请求体为 form-urlencoded 的 `data=JSON 字符串`;
        响应可能为 JSON(数组/对象)或 `response_error:` 纯文本(服务端异常)。
        """
        cookie = self._session_cookie()
        for attempt in range(3):
            try:
                r = self.session.post(
                    self.base_url + path,
                    data={"data": _dumps(body)},
                    headers={"Cookie": cookie},
                )
            except httpx.TransportError:  # 超时/连接失败
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise KingdeeApiUnavailable("金蝶 API 网络/超时错误,重试超限") from None
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise KingdeeApiUnavailable(f"金蝶 API 重试超限(HTTP {r.status_code})")
            if r.status_code != 200:
                raise KingdeeApiUnavailable(f"金蝶 API 错误:HTTP {r.status_code}")
            text = r.text
            # 会话失效(报错文本含"会话"/session)→ 重登一次再发,仅 1 次
            if "会话" in text or "session" in text.lower():
                self._login()
                cookie = self._session_cookie()
                r = self.session.post(self.base_url + path,
                                      data={"data": _dumps(body)},
                                      headers={"Cookie": cookie})
                if r.status_code != 200:
                    raise KingdeeApiUnavailable(f"金蝶 API 错误:HTTP {r.status_code}")
                text = r.text
            # 服务端异常以纯文本返回(response_error:...),非 JSON
            if text.startswith("response_error") or not text.lstrip().startswith(("{", "[")):
                raise KingdeeApiUnavailable(f"金蝶 API 服务端异常:{text[:200]}")
            try:
                data = r.json()
            except ValueError:
                raise KingdeeApiUnavailable("金蝶 API 响应非 JSON") from None
            # 归一化:ExecuteBillQuery 等成功时最外层是数组;失败时是
            # [{"Result":{"ResponseStatus":{...}}}] 或 {"Result":{"ResponseStatus":{...}}}
            if isinstance(data, list):
                inner = data[0] if data and isinstance(data[0], dict) else {}
                if "Result" in inner:
                    data = inner["Result"]
                else:
                    return data  # 纯数据数组(如查询结果行),无 ResponseStatus 可查
            elif isinstance(data, dict):
                data = data.get("Result", data)
            status = data.get("ResponseStatus", {})
            if not status.get("IsSuccess", True):
                errors = status.get("Errors") or []
                msg = "; ".join(str(e.get("Message", e)) for e in errors) or "未知错误"
                raise KingdeeApiUnavailable(msg)
            return data

    # ── 元数据查询 ────────────────────────────────────────────────────────
    def get_form_fields(self, form_id: str) -> list[FieldInfo]:
        """查询单据字段元数据(✅ 实测可用,QueryBusinessInfo)。

        解析 Result.NeedReturnData.Entrys[]:
        - FBillHead(主表)→ 字段平铺为顶层(field_name=字段 Key)
        - 其余 Entry(分录/子单头)→ 字段带 `EntryKey.FieldName` 前缀
        - ParentKey 非空的子分录暂不展开
        """
        data = self._post(self._QUERY_BIZ_INFO, {"formid": form_id})
        nrd = data.get("NeedReturnData", {}) or {}
        fields: list[FieldInfo] = []
        for ent in nrd.get("Entrys", []) or []:
            key = ent.get("Key")
            if not key or ent.get("ParentKey"):
                continue
            prefix = "" if key == "FBillHead" else f"{key}."
            for f in ent.get("Fields", []) or []:
                fkey = f.get("Key")
                if not fkey:
                    continue
                fields.append(FieldInfo(
                    field_name=f"{prefix}{fkey}",
                    field_label=_zh_name(f.get("Name", [])),
                    data_type=_field_type_label(f),
                ))
        return fields

    @classmethod
    def client_from_env_or_none(cls) -> "KingdeeApiClient | None":
        """从环境变量构造客户端;缺 KD_BASE_URL 返回 None(无环境 = 硬门槛信号)。

        环境变量:KD_BASE_URL(主机,可带 /k3cloud/ 前缀)/ KD_USERNAME / KD_PASSWORD
        / KD_DATA_CENTER(账套 ID,即 ValidateUser 的 acctID)/ KD_LCID(可选,默认 2052)
        """
        base = os.getenv("KD_BASE_URL")
        if not base:
            return None
        return cls(base, os.getenv("KD_DATA_CENTER", ""), os.getenv("KD_USERNAME", ""),
                   os.getenv("KD_PASSWORD", ""), int(os.getenv("KD_LCID", "2052")))


def _dumps(body: dict) -> str:
    """请求体 JSON 序列化(ensure_ascii=False 保中文用户名)。"""
    import json
    return json.dumps(body, ensure_ascii=False)


def _zh_name(name_list: list) -> str:
    """从多语言 Name 数组提取中文名(LocaleId=2052),失败取第一个。"""
    if not isinstance(name_list, list):
        return ""
    for item in name_list:
        if isinstance(item, dict) and item.get("Key") == 2052:
            return item.get("Value", "") or ""
    if name_list and isinstance(name_list[0], dict):
        return name_list[0].get("Value", "") or ""
    return ""


def _field_type_label(f: dict) -> str:
    """字段类型语义:关联字段标 BaseField->FormId;其余保留 FieldType 数字编码。"""
    lookup = f.get("LookUpObjectFormId")
    if lookup:
        return f"BaseField->{lookup}"
    ft = f.get("FieldType")
    et = f.get("ElementType")
    if ft is None:
        return ""
    return f"FieldType={ft}" + (f",ElementType={et}" if et is not None else "")
