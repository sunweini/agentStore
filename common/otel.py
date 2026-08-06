"""OpenTelemetry 全链路可观测初始化。

设计见 docs/superpowers/specs/2026-08-06-agent1-sentiment-query-agent-design.md §9;
规范见 docs/dev-standards.md §5 与全局可观测性编码规范。

约束(遵循 OBS-CORE-003):
- 用户标识(apikey)等只进日志/计费,不进 span label。
- span 只带低基数 label:trace_id / thread_id / 步骤名 / 引擎名 / 节点名。
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from common import config

_PROVIDER: TracerProvider | None = None


def init_otel() -> TracerProvider:
    """初始化全局 TracerProvider(OTLP HTTP exporter)。

    幂等:重复调用返回已创建的实例。OTEL_ENDPOINT 未配置时返回空 provider
    (span 丢弃,不阻塞本地无 collector 环境)。

    用法:
        from common.otel import init_otel, get_tracer
        init_otel()                      # 应用启动时调一次
        tracer = get_tracer()
        with tracer.start_as_current_span("graph_execute") as span:
            span.set_attribute("thread_id", group_id)
    """
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER

    resource = Resource.create({SERVICE_NAME: "function-call-tool-agent1"})
    _PROVIDER = TracerProvider(resource=resource)

    endpoint = config.get_env("OTEL_ENDPOINT")
    if endpoint:
        _PROVIDER.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
    trace.set_tracer_provider(_PROVIDER)
    return _PROVIDER


def get_tracer() -> trace.Tracer:
    """获取项目 tracer(需先 init_otel)。"""
    return trace.get_tracer("function-call-tool")
