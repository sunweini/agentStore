"""经验库种子灌入(幂等,错误签名去重)。"""
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
        client.add_documents("experience", [item["message"] + " 修复:" + item["fix"]],
                             [{"signature": sig, "code": item["code"], "source": item.get("source", "seed")}])
        added += 1
    return added
