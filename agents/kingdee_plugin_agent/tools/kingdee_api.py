"""金蝶云星空 WebAPI 元数据客户端(只读,不写业务数据)。

调用流:
  查询 ──► 登录获取凭证(请求体携带 userName/password/dc,免预登录)──► 调元数据接口 ──► 解析 FieldInfo
  429/5xx/超时 ──► 指数退避(2 次重试)──► KingdeeApiUnavailable

⚠️ 端点路径与响应结构 = 初始契约(占位):
  本环境无真实金蝶实例,端点路径与响应字段以本文件为准作为文档化初始契约,
  需在团队环境可用后对照金蝶 WebAPI 文档/真实实例验证并调整 —— 见设计文档
  docs/superpowers/specs/2026-08-08-kingdee-plugin-agent-design.md §13 风险与待确认
  (金蝶官方文档可爬性/API 行为无法在本环境验证)。
  单元测试基于 mock 响应,不依赖真实环境。
"""
import os
import time
from dataclasses import dataclass

import httpx


class KingdeeApiUnavailable(RuntimeError):
    """金蝶 API 不可用(网络/超时/429 重试超限/业务错误)。"""


@dataclass
class FieldInfo:
    field_name: str
    field_label: str
    data_type: str


class KingdeeApiClient:
    """金蝶云星空 WebAPI 客户端(只读元数据查询)。

    凭证随每个请求体携带(`userName`/`password`/`dc`),无需预登录接口
    (Kingdee WebAPI 支持的登录方式之一;若真实实例要求 acctID/session
    方式,在 §13 风险验证时调整)。
    """

    #: 字段查询服务(ExecuteBillQuery):初始契约路径,待真实环境验证
    _EXECUTE_BILL_QUERY = "/K3Cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc"
    #: 元数据服务(列表 FormId 查询):初始契约路径,待真实环境验证
    _METADATA_SERVICE = "/K3Cloud/Kingdee.BOS.WebApi.ServicesStub.MetadataService.QueryBusinessObjects.common.kdsvc"
    #: 表单操作查询(按钮/操作):初始契约路径,待真实环境验证
    _FORM_OPERATIONS = "/K3Cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.GetFormOperations.common.kdsvc"

    def __init__(self, base_url: str, username: str, password: str, data_center: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.session = httpx.Client(timeout=timeout)
        self._auth = {"userName": username, "password": password, "dc": data_center}

    def _post(self, path: str, body: dict) -> dict:
        """POST 元数据接口:1 次请求 + 2 次指数退避重试(429/5xx/超时)。"""
        for attempt in range(3):
            try:
                r = self.session.post(f"{self.base_url}{path}", json={**self._auth, **body})
            except httpx.TransportError:  # 超时/连接失败
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise KingdeeApiUnavailable("金蝶 API 网络/超时错误,重试超限") from None
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise KingdeeApiUnavailable("金蝶 API 重试超限(429/5xx)")
            if r.status_code != 200:
                raise KingdeeApiUnavailable(f"金蝶 API 错误:HTTP {r.status_code}")
            data = r.json()
            status = data.get("Result", {}).get("ResponseStatus", {})
            if not status.get("IsSuccess", False):
                raise KingdeeApiUnavailable(str(status.get("Errors", "未知错误")))
            return data

    def list_formids(self) -> list[str]:
        """查询可用业务对象 FormId 列表。

        ⚠️ 初始契约:元数据服务 `QueryBusinessObjects` 响应按
        `Result.ValidationResults[].FieldName` 取值;真实端点/响应结构
        需在真实金蝶实例上验证后调整(设计文档 §13)。
        """
        data = self._post(self._METADATA_SERVICE, {})
        return [f["FieldName"] for f in data["Result"]["ValidationResults"]]

    def get_form_fields(self, form_id: str) -> list[FieldInfo]:
        """查询单据字段元数据(只读)。

        ⚠️ 初始契约:经 ExecuteBillQuery 取 1 行,字段名从结果头推断;
        真实实现按 MCP 文档/金蝶 API 文档调整(设计文档 §13 风险验证)。
        """
        data = self._post(self._EXECUTE_BILL_QUERY, {
            "formid": form_id, "fieldKeys": "*", "topRowCount": 1,
        })
        return [FieldInfo(f["FieldName"], f.get("FieldLabel", ""), f.get("DataType", ""))
                for f in data["Result"]["ValidationResults"]]

    def get_operations(self, form_id: str) -> list[str]:
        """查询表单可用操作(提交/审核/反审核等)列表。

        ⚠️ 初始契约:响应按 `Result.ValidationResults[].OperationName` 取值;
        真实端点/响应结构需在真实金蝶实例上验证后调整(设计文档 §13)。
        """
        data = self._post(self._FORM_OPERATIONS, {"formid": form_id})
        return [f.get("OperationName", "") for f in data["Result"]["ValidationResults"]
                if f.get("OperationName")]

    @classmethod
    def client_from_env_or_none(cls) -> "KingdeeApiClient | None":
        """从环境变量构造客户端;缺 KD_BASE_URL 返回 None(无环境 = 硬门槛信号)。

        环境变量:KD_BASE_URL / KD_USERNAME / KD_PASSWORD / KD_DATA_CENTER
        """
        base = os.getenv("KD_BASE_URL")
        if not base:
            return None
        return cls(base, os.getenv("KD_USERNAME", ""), os.getenv("KD_PASSWORD", ""),
                   os.getenv("KD_DATA_CENTER", ""))
