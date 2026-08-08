"""经验库种子灌入(幂等,错误签名去重)。

命令行入口(维护手册 maintenance.md 步骤 1.4):
    python -m agents.kingdee_plugin_agent.seed.seed_load [--data-dir <dir>]
默认 data_dir = data/kingdee-rag(与 RagClient 默认一致);输出 "种子灌入完成:新增 N 条"。
"""
import argparse
import json
from pathlib import Path

from common.rag import RagClient

SEED_FILE = Path(__file__).parent / "compile_errors.json"


def load_seed_data(client: RagClient) -> int:
    if not SEED_FILE.exists():
        return 0
    items = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    added = 0
    for item in items:
        sig = f"{item['code']}|{item['file_pattern']}"
        existing = client.search("experience", sig, k=1, filter={"signature": sig})
        if existing and existing[0]["metadata"].get("signature") == sig:
            continue  # 幂等:签名已存在跳过
        # 文本格式与 ExperienceStore.propose 统一(设计 §6.2:种子即 w7 格式样本)
        client.add_documents("experience", [f"[{item['code']}] {item['message']} 修复:{item['fix']}"],
                             [{"signature": sig, "code": item["code"], "source": item.get("source", "seed")}])
        added += 1
    return added


def main(argv: list[str] | None = None) -> int:
    """CLI 入口(可测形态:返回新增条数并打印;由 __main__ 经 argparse 调用)。"""
    parser = argparse.ArgumentParser(description="经验库种子灌入(幂等,错误签名去重)")
    parser.add_argument("--data-dir", type=Path, default=Path("data/kingdee-rag"),
                        help="RAG 数据目录(默认 data/kingdee-rag,与 RagClient 默认一致)")
    args = parser.parse_args(argv)
    client = RagClient(data_dir=args.data_dir)
    added = load_seed_data(client)
    print(f"种子灌入完成:新增 {added} 条")
    return added


if __name__ == "__main__":
    main()
