"""agent1(舆情方案生成 Agent)测试。

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

from agents.agent1 import billing
from agents.agent1.auth import _valid_keys, assert_owner
from agents.agent1.store import converter, scheme_store
from common import config

_SCRIPTS = (
    Path(__file__).resolve().parent.parent
    / "agents" / "agent1" / "skills" / "overseas-sentiment-query-builder" / "scripts"
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
    out = _run_script("step4_queries.py", {"schemes": [
        {"id": "Q0", "name": "集团层", "tracks": [
            {"key": "a", "boolean": "(A)", "google": "(A)"},
            {"key": "b", "boolean": "(A) AND (strike)", "google": "(A) strike"},
        ]},
    ]})
    sc = out["schemes"][0]
    assert sc["tracks"][0]["boolean_query"] == "(A)"
    assert sc["tracks"][1]["key"] == "b"
    # 每轨默认 selected=True,sources 空列表
    assert sc["tracks"][0]["selected"] is True
    assert sc["tracks"][0]["sources"] == []


def test_step6_cadence_fix_fast():
    """快讯轨强制快讯/小时级。"""
    out = _run_script("step6_cadence.py", {"schemes": [
        {"id": "Q3", "tracks": [{"key": "快讯", "frequency": "周级", "risk": "critical"}]},
    ]})
    tr = out["schemes"][0]["tracks"][0]
    assert tr["frequency"] == "快讯/小时级"
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


def _sample_group() -> dict:
    return {
        "group_id": "g1", "owner": "user1", "company_name": "测试公司",
        "status": "review", "schemes": [
            {"id": "Q0", "name": "集团层", "region": "全语种", "lang": "中",
             "desc": "", "gaps": ["GAP001 拼写待证"], "selected": True,
             "tracks": [
                 {"key": "a", "boolean_query": "(A)", "google_query": "(A)",
                  "sources": ["media.com"], "frequency": "周级", "risk": "medium",
                  "relevance": "direct", "selected": True},
                 {"key": "b", "boolean_query": "(A) AND (strike)", "google_query": "(A) strike",
                  "sources": [], "frequency": "日级", "risk": "high",
                  "relevance": "direct", "selected": False},
             ]},
        ],
        "keywords": [{"layer": "A", "category": "A1", "terms": "\"测试\"", "lang": "全",
                      "guard": "", "note": ""}],
    }


def test_converter_selected_only():
    """转换层:只导出勾选的轨(方案 selected 且轨 selected)。"""
    spec = converter.group_to_spec(_sample_group())
    assert len(spec["tasks"]) == 1  # 只有 a 轨(b 未选)
    assert spec["tasks"][0]["boolean"] == "(A)"
    assert spec["tasks"][0]["sources"] == ["media.com"]
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
