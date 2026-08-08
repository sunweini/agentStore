"""类型专属代码模板库。模板 = 骨架进 prompt,指南 = 参数化细节检索。

模板目录:bill/ service/ list/ 各一个 template.cs,占位符 `{{NAMESPACE}}`
`{{CLASS_NAME}}` `{{BUSINESS_LOGIC}}`,基类按类型写死在模板里
(bill→AbstractBillPlugIn / service→AbstractOperationServicePlugIn / list→AbstractListPlugIn)。
"""
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent
_PLUGIN_TYPES = ("bill", "service", "list")


def load_template(plugin_type: str) -> str:
    """读取指定插件类型的 C# 骨架模板。未知类型抛 ValueError。"""
    if plugin_type not in _PLUGIN_TYPES:
        raise ValueError(f"未知插件类型: {plugin_type},可选 {_PLUGIN_TYPES}")
    return (_TEMPLATE_DIR / plugin_type / "template.cs").read_text(encoding="utf-8")


def render_template(template: str, values: dict) -> str:
    """按 {{TOKEN}} 逐个 token 替换渲染(不做 str.format)。

    不用 str.format:C# 模板含大量字面 { }(方法体/属性),先 replace("{{", "{")
    再 .format 会把方法体大括号当成格式字段,直接 KeyError/ValueError。
    逐个 token 的 str.replace 对字面大括号免疫;values 中未提供的占位符保留原样。
    """
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template
