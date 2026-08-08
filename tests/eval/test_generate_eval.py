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
