# tests/test_templates.py
# 模板库测试:三类型(单据/服务/列表)加载 + 渲染器(C# 大括号免疫)。
from agents.kingdee_plugin_agent.templates import load_template, render_template
import pytest

# 各类型模板的基类契约(渲染后骨架必须保持正确的基类继承)
_BASE_CLASSES = {
    "bill": "AbstractBillPlugIn",
    "service": "AbstractOperationServicePlugIn",
    "list": "AbstractListPlugIn",
}


def test_load_bill_template():
    assert "AbstractBillPlugIn" in load_template("bill")


def test_load_all_plugin_types():
    for plugin_type, base in _BASE_CLASSES.items():
        tpl = load_template(plugin_type)
        assert base in tpl
        assert "{{NAMESPACE}}" in tpl and "{{CLASS_NAME}}" in tpl
        assert "{{BUSINESS_LOGIC}}" in tpl


def test_load_unknown_type_raises():
    with pytest.raises(ValueError):
        load_template("unknown")


def test_render_template_fills():
    tpl = "namespace {{NAMESPACE}} class {{CLASS_NAME}} {{BUSINESS_LOGIC}}"
    out = render_template(tpl, {"NAMESPACE": "K3.Plugin", "CLASS_NAME": "StockCheck", "BUSINESS_LOGIC": "// 逻辑"})
    assert "namespace K3.Plugin" in out and "StockCheck" in out


def test_render_full_template_survives_csharp_braces():
    """完整模板含 C# 字面大括号(方法体/属性),逐个 token replace 渲染不炸。

    这是禁用 str.format 的原因:先 replace("{{", "{") 再 .format 会把方法体
    的 { } 当成格式字段,直接 KeyError。replace 逐个 {{TOKEN}} 替换对字面大括号免疫。
    """
    for plugin_type, base in _BASE_CLASSES.items():
        out = render_template(
            load_template(plugin_type),
            {"NAMESPACE": "K3.Plugin", "CLASS_NAME": "StockCheck", "BUSINESS_LOGIC": "// 校验库存"},
        )
        assert f"class StockCheck : {base}" in out, plugin_type
        assert "namespace K3.Plugin" in out, plugin_type
        assert "// 校验库存" in out, plugin_type
        # C# 字面大括号原样保留,且无未替换占位符残留
        assert "public override void" in out, plugin_type
        assert "{{" not in out, plugin_type
