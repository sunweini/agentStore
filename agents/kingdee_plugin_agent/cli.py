"""CLI 入口:需求文本 + 环境配置(硬门槛)→ 图执行 → TodoList + 交付包。

用法:
  kingdee-cli "给采购单审核加库存校验" --env test

流程:
  1. 环境硬门槛:未配置 KD_BASE_URL → 报错退出(exit 1),不进入图执行
  2. 图执行:interrupt 交互澄清循环(Q/A:澄清问题 / 确认摘要 / 中途询问)
  3. 结束后打印 TodoList 摘要 + 交付包路径;全部交付返回 0,失败/中止返回 1

测试注入:run_cli 以模块级名字调用 build_graph,测试 monkeypatch
agents.kingdee_plugin_agent.cli.build_graph 注入确定性模式(build_graph(llm=None)
+ fake 编译/冒烟),与 C10 图测试同一注入思路 —— 只注入 LLM/外部服务,
不 mock LangGraph 本身。
"""
import argparse
import os
import uuid

from langgraph.types import Command

from agents.kingdee_plugin_agent.agent import build_graph, default_recursion_limit


def _field(item, name: str) -> str:
    """todo 条目取值:兼容 Subtask 实例 / dict(LangGraph 反序列化)。"""
    return getattr(item, name) if hasattr(item, name) else item.get(name, "")


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kingdee-cli")
    parser.add_argument("requirement", help="需求描述")
    parser.add_argument("--env", required=True, help="金蝶目标环境名(env 配置)")
    args = parser.parse_args(argv)
    # 环境硬门槛:无 KD_BASE_URL 退出(真实实现读 .env 环境配置)
    if not os.getenv("KD_BASE_URL"):
        print("错误:未配置金蝶环境(KD_BASE_URL),先配置环境再使用")
        return 1

    print(f"需求: {args.requirement}")
    print(f"目标环境: {args.env}")

    # 生产缺省:真实 LLM 由 env 接线(build_graph() 内部 get_chat_model());
    # 测试路径 monkeypatch 本模块 build_graph 注入确定性模式(llm=None)。
    app = build_graph()
    # thread_id 每次运行唯一(隔离 checkpointer 会话)。recursion_limit 按子任务数
    # 预算(设计 §6.2:100 + 20×n);CLI 澄清期还不知道子任务数(拆解发生在一次
    # invoke 内),按上限 10 给足 —— 300 超步覆盖澄清 + 全流水线 + 返工重跑
    cfg = {"configurable": {"thread_id": f"kingdee-cli-{uuid.uuid4().hex}"},
           "recursion_limit": default_recursion_limit(10)}
    state = {"requirement_spec": {"requirement": args.requirement,
                                  "environment": args.env},
             "todo": []}

    # ── 交互澄清循环:interrupt 挂起 → 打印问题/摘要 → stdin 答复 → resume ──
    while True:
        result = app.invoke(state, cfg)
        interrupts = result.get("__interrupt__")
        if not interrupts:
            state = result
            break
        payload = interrupts[0].value
        qtype = payload.get("type", "")
        if qtype == "question":
            print(f"[澄清 {payload.get('round', 0) + 1}] {payload.get('text', '')}")
        elif qtype == "confirm":
            print(payload.get("summary", "需求确认摘要"))
        else:  # ask_user / 未知类型:原样展示
            print(f"[询问] {payload.get('question', payload)}")
        try:
            answer = input("> ")
        except EOFError:
            print("错误:交互输入中断(非交互终端?),任务已中止")
            return 1
        state = Command(resume=answer)

    # ── TodoList 摘要 + 交付包路径 ──
    print("\n── TodoList 摘要 ──")
    for t in state.get("todo", []):
        print(f"  {_field(t, 'id')} [{_field(t, 'plugin_type')}] "
              f"{_field(t, 'status')}  {_field(t, 'title')}")
    deliverables = list(state.get("final_deliverables") or [])
    if not deliverables and state.get("final_deliverable"):
        deliverables = [state["final_deliverable"]]
    for d in deliverables:
        print(f"  交付包: {d}")

    if state.get("action") == "finish":
        print("全部子任务交付完成")
        return 0
    print("任务未全部交付(失败/中止),详见上方 TodoList 摘要")
    return 1


if __name__ == "__main__":
    raise SystemExit(run_cli())
