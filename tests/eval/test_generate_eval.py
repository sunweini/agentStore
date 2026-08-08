"""eval 集测试:case 文件 schema + run_eval 确定性(与提交基线一致)。"""
import json
from pathlib import Path

from tests.eval.run_eval import run_eval  # 接口即契约:run_eval 必须可导入

from agents.kingdee_plugin_agent.store.artifact_store import ArtifactStore


def test_eval_cases_valid_schema():
    cases_dir = Path(__file__).parent / "cases"
    for f in sorted(cases_dir.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["plugin_type"] in ("bill", "service", "list")
        assert data["requirement"]
        assert data["form_id"]
        assert data["field"]
        assert data["expected_trigger"]


def test_run_eval_deterministic_matches_baseline(tmp_path):
    """llm=None + 评估级 MockCompiler → 报告与提交的 baseline.json 完全一致。

    基线 = 确定性结果(llm=None 走 w3 模板骨架,无随机);prompt/模板变更后
    重跑本测试即暴露与基线的差异(compile_pass_rate 回归检测)。
    """
    from tests.eval.run_eval import default_mock_compiler

    report = run_eval(
        llm=None,
        store=ArtifactStore(root=tmp_path),
        cases_dir=Path(__file__).parent / "cases",
        compile_client=default_mock_compiler(),
    )
    baseline = json.loads(
        (Path(__file__).parent / "baseline.json").read_text(encoding="utf-8"))
    assert report == baseline
    assert baseline["compile_pass_rate"] == 1.0
    assert baseline["trigger_pass_rate"] == 1.0


def test_trigger_assertion_flags_wrong_event(tmp_path):
    """expected_trigger 与生成代码事件不一致时 trigger_ok=False,编译不受影响。

    真实回归信号:LLM 生成挂错事件(如 OnLoad 而非 AfterDoOperation)时,
    mock 编译仍通过,只有事件断言能抓住差异。
    """
    from tests.eval.run_eval import default_mock_compiler, run_eval

    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "bad_1.json").write_text(json.dumps({
        "id": "bad_1", "plugin_type": "bill",
        "requirement": "采购单审核时校验库存不足则拦截",
        "form_id": "SAL_PurchaseOrder", "field": "FQty",
        "expected_trigger": "OnBeforeClick",  # 模板里不存在的事件
    }, ensure_ascii=False), encoding="utf-8")
    report = run_eval(llm=None, store=ArtifactStore(root=tmp_path / "store"),
                      cases_dir=cases_dir, compile_client=default_mock_compiler())
    r = report["results"][0]
    assert r["compiled"] is True          # 编译通过(事件断言与编译独立)
    assert r["trigger_ok"] is False       # 事件断言抓住差异
    assert report["trigger_pass_rate"] == 0.0


def test_malformed_case_isolated(tmp_path):
    """单个坏 case(非法 JSON)不中断整个 eval,按失败记录。"""
    from tests.eval.run_eval import default_mock_compiler, run_eval

    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "bad.json").write_text("{not json", encoding="utf-8")
    (cases_dir / "good_1.json").write_text(json.dumps({
        "id": "good_1", "plugin_type": "bill",
        "requirement": "采购单审核时校验库存不足则拦截",
        "form_id": "SAL_PurchaseOrder", "field": "FQty",
        "expected_trigger": "AfterDoOperation",
    }, ensure_ascii=False), encoding="utf-8")
    report = run_eval(llm=None, store=ArtifactStore(root=tmp_path / "store"),
                      cases_dir=cases_dir, compile_client=default_mock_compiler())
    by_id = {r["id"]: r for r in report["results"]}
    assert set(by_id) == {"bad", "good_1"}   # 坏 case 以文件 stem 为 id 兜底
    assert by_id["good_1"]["compiled"] is True
    assert by_id["bad"]["compiled"] is False
    assert by_id["bad"]["errors"] == ["eval:JSONDecodeError"]
