"""sentiment-query-agent(舆情方案生成 Agent)测试。

覆盖:
1. skill 分步脚本单测(格式契约/缺字段记 GAP)
2. store 单测(勾选汇总/转换/文件库)
3. 鉴权/计费单测(apikey 校验/归属 403/pending→commit)

图单测/端到端(真实 MCP+LLM)依赖外部服务,单独跑。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agents.sentiment_query_agent import billing
from agents.sentiment_query_agent.auth import _valid_keys, assert_owner
from agents.sentiment_query_agent.graph.nodes import _extract_json
from agents.sentiment_query_agent.store import converter, scheme_store
from common import config

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_data_paths():
    """运行时数据目录必须落在项目根 data/(防路径层级错)。"""
    assert str(scheme_store._DATA_DIR).endswith(f"{_PROJECT_ROOT}/data/schemes")
    assert str(billing._DATA_DIR).endswith(f"{_PROJECT_ROOT}/data/billing")

_SCRIPTS = (
    Path(__file__).resolve().parent.parent
    / "agents" / "sentiment_query_agent" / "skills" / "overseas-sentiment-query-builder" / "scripts"
)


def _run_script(name: str, payload: dict) -> dict:
    """调 skill 分步脚本,返回 stdout JSON(失败抛 RuntimeError)。"""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / name)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{name} 失败: {proc.stderr}")
    return json.loads(proc.stdout)


# ===== 1. skill 分步脚本单测 =====


def test_step1_entities_format():
    out = _run_script("step1_entities.py", {
        "entities": {"parent": "中国十五冶",
                     "overseas_entities": [{"name": "NFCA", "lang": "en", "region": "赞比亚"}]},
    })
    assert out["entities"]["parent"] == "中国十五冶"
    assert out["entities"]["overseas_entities"][0]["name"] == "NFCA"
    # 缺字段记 GAP
    assert any("GAP" in g for g in out.get("_gaps", []))


def test_step4_queries_tracks():
    """轨 key 用中文语义名:全量新闻/负面新闻/行业新闻/快讯/司法/招标。"""
    out = _run_script("step4_queries.py", {"schemes": [
        {"id": "Q0", "name": "集团层", "tracks": [
            {"key": "全量新闻", "boolean": "(A)", "google": "(A)"},
            {"key": "负面新闻", "boolean": "(A) AND (strike)", "google": "(A) strike"},
        ]},
    ]})
    sc = out["schemes"][0]
    assert sc["tracks"][0]["boolean_query"] == "(A)"
    assert sc["tracks"][1]["key"] == "负面新闻"
    # 每轨默认 selected=True,sources 空列表,且不含 risk 字段
    assert sc["tracks"][0]["selected"] is True
    assert sc["tracks"][0]["sources"] == []
    assert "risk" not in sc["tracks"][0]


def test_step4_rejects_old_letter_keys():
    """旧字母轨 key(a/b/c)不在新 TRACK_KEYS:全部无效 → 脚本非 0 退出。"""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "step4_queries.py")],
        input=json.dumps({"schemes": [{"id": "Q0", "name": "集团层", "tracks": [
            {"key": "a", "boolean": "(A)", "google": "(A)"},
        ]}]}),
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    assert "FORMAT_ERROR" in proc.stderr


def test_step6_cadence_fix_fast():
    """快讯轨强制快讯/小时级;risk 字段保留(修复后行为)。"""
    out = _run_script("step6_cadence.py", {"schemes": [
        {"id": "Q3", "tracks": [{"key": "快讯", "frequency": "周级", "risk": "critical"}]},
    ]})
    tr = out["schemes"][0]["tracks"][0]
    assert tr["frequency"] == "快讯/小时级"
    assert tr["risk"] == "critical"  # risk 保留
    assert any("GAP" in g for g in out.get("_gaps", []))


def test_script_bad_json_fails():
    """LLM 输出非 JSON → 脚本非 0 退出。"""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "step1_entities.py")],
        input="not json", capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    assert "FORMAT_ERROR" in proc.stderr


# ===== 2. store 单测 =====


def test_extract_json_robust():
    """LLM 输出容错解析:纯 JSON/代码块/带说明文字。"""
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('好的:\n{"a": 1}\n完成') == {"a": 1}
    assert _extract_json('不是 JSON') is None
    assert _extract_json('') is None


def _sample_group() -> dict:
    return {
        "group_id": "g1", "owner": "user1", "company_name": "测试公司",
        "status": "review", "schemes": [
            {"id": "Q0", "name": "集团层", "region": "全语种", "lang": "中",
             "desc": "", "gaps": ["GAP001 拼写待证"], "selected": True,
             "tracks": [
                 {"key": "全量新闻", "boolean_query": "(A)", "google_query": "(A)",
                  "sources": ["media.com"], "frequency": "周级",
                  "relevance": "direct", "selected": True},
                 {"key": "负面新闻", "boolean_query": "(A) AND (strike)", "google_query": "(A) strike",
                  "sources": [], "frequency": "日级",
                  "relevance": "direct", "selected": False},
             ]},
        ],
        "keywords": [{"layer": "A", "category": "A1", "terms": "\"测试\"", "lang": "全",
                      "guard": "", "note": ""}],
    }


def test_converter_selected_only():
    """转换层:只导出勾选的轨(方案 selected 且轨 selected)。"""
    spec = converter.group_to_spec(_sample_group())
    assert len(spec["tasks"]) == 1  # 只有 全量新闻 轨(负面新闻 未选)
    assert spec["tasks"][0]["boolean"] == "(A)"
    assert spec["tasks"][0]["sources"] == ["media.com"]
    assert "risk" not in spec["tasks"][0]
    assert spec["keywords"][0]["layer"] == "A"
    # GAP → extra_notes
    assert any(n["key"].startswith("GAP") for n in spec["extra_notes"])


def test_store_save_load_roundtrip(tmp_path, monkeypatch):
    """文件库:草稿/正式/索引读写。"""
    monkeypatch.setattr(scheme_store, "_DATA_DIR", tmp_path)
    group = _sample_group()
    scheme_store.save_draft(group)
    assert (tmp_path / "g1.draft.json").exists()
    loaded = scheme_store.load_group("g1")
    assert loaded["company_name"] == "测试公司"

    group["status"] = "committed"
    scheme_store.save_committed(group)
    assert (tmp_path / "g1.json").exists()
    assert not (tmp_path / "g1.draft.json").exists()  # 草稿删除
    assert scheme_store.load_group("g1")["status"] == "committed"
    # 索引按 owner 过滤
    assert [g["group_id"] for g in scheme_store.list_groups("user1")] == ["g1"]
    assert scheme_store.list_groups("other") == []


# ===== 3. 鉴权/计费单测 =====


def test_auth_valid_keys(monkeypatch):
    """apikey 映射从 env 读。"""
    monkeypatch.setenv("API_KEYS_JSON", json.dumps({"key1": "user1"}))
    assert _valid_keys() == {"key1": "user1"}


def test_auth_assert_owner():
    """归属校验:owner 不符 → 403。"""
    with pytest.raises(Exception):
        assert_owner("user2", {"owner": "user1"})
    assert_owner("user1", {"owner": "user1"})  # 不抛


def test_billing_pending_commit(tmp_path, monkeypatch):
    """计费:创建记 pending,commit 转正式;超并发拒绝。"""
    monkeypatch.setattr(billing, "_DATA_DIR", tmp_path)
    billing.create_pending("u1", "g1")
    rec = json.loads((tmp_path / "u1.json").read_text(encoding="utf-8"))
    assert rec[0]["status"] == "pending"
    billing.commit("u1", "g1")
    rec = json.loads((tmp_path / "u1.json").read_text(encoding="utf-8"))
    assert rec[0]["status"] == "committed"
    assert rec[0]["committed_at"]


def test_billing_max_pending(tmp_path, monkeypatch):
    """防刷:超过并发上限拒绝。"""
    monkeypatch.setattr(billing, "_DATA_DIR", tmp_path)
    for i in range(billing._MAX_PENDING):
        billing.create_pending("u1", f"g{i}")
    with pytest.raises(Exception):
        billing.create_pending("u1", "g_over")


def test_billing_concurrent_no_loss(tmp_path, monkeypatch):
    """并发竞态:同一用户并发提交,记录不丢失(文件锁保证原子)。"""
    import asyncio
    monkeypatch.setattr(billing, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(billing, "_MAX_PENDING", 100)  # 放开上限测竞态

    async def _run():
        await asyncio.gather(*[
            asyncio.to_thread(billing.create_pending, "u1", f"g{i}")
            for i in range(10)
        ], return_exceptions=True)

    asyncio.run(_run())
    recs = json.loads((tmp_path / "u1.json").read_text(encoding="utf-8"))
    assert len(recs) == 10  # 无丢失


def test_billing_cancel_pending_releases_quota(tmp_path, monkeypatch):
    """stop 任务:取消 pending 释放并发额度。"""
    monkeypatch.setattr(billing, "_DATA_DIR", tmp_path)
    for i in range(3):
        billing.create_pending("u1", f"g{i}")
    billing.cancel_pending("u1", "g1")
    recs = json.loads((tmp_path / "u1.json").read_text(encoding="utf-8"))
    assert [r["group_id"] for r in recs] == ["g0", "g2"]  # g1 已释放
    # 取消不存在的记录不报错
    billing.cancel_pending("u1", "g_never")
    recs = json.loads((tmp_path / "u1.json").read_text(encoding="utf-8"))
    assert len(recs) == 2
