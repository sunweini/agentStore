# 每 agent 独立 CHANGELOG + 版本号管理实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把根 `CHANGELOG.md` 的 34 段历史按归属拆到两个 agent 的独立 CHANGELOG,根文件重建为索引 + 项目级。

**Architecture:** 纯文档迁移。用一个 Python 脚本按「版本段标题归属」把根文件 34 段拆成 sentiment / kingdee / 项目级 三份,每份按版本降序、去掉 agent 前缀、版本号原样保留;根文件重建为索引 + v0.1.0 项目初始化段;根 CLAUDE.md + 两 agent CLAUDE.md 补收尾约定。

**Tech Stack:** Python 脚本(bash heredoc 一次性,不入库)+ Markdown。

## Global Constraints

- 全部 34 段**必须**迁完,段数之和恒等(kingdee 24 + sentiment 9 + 根 1 = 34)。
- 版本号**原样保留**,不重编;sentiment 两个 v1.2.0 重复号保留为历史事实。
- 每 agent 文件内版本**降序**;独立文件段标题**去掉 agent 前缀**。
- 段内容逐字保留(仅标题去前缀,其余不改)。
- 当前版本号(索引表用):kingdee v1.26.0 / sentiment v1.24.0。
- 迁移脚本不入库(一次性),但迁移结果要能复现验证(段数断言)。

---

### Task 1: 拆 34 段到两 agent + 重建根文件

**Files:**
- Create: `agents/sentiment_query_agent/CHANGELOG.md`
- Create: `agents/kingdee_plugin_agent/CHANGELOG.md`
- Modify: `CHANGELOG.md`(重建为索引 + 项目级)

**Interfaces:**
- Consumes: 现根 `CHANGELOG.md`(34 段,设计文档归属表)
- Produces: 两份独立 CHANGELOG + 根索引

- [ ] **Step 1: 写迁移脚本(逐段按标题归属分拣)**

```python
# /tmp/split_changelog.py — 一次性迁移脚本,不入库
import re, sys
src = open('CHANGELOG.md').read()

# 定位每个版本段:## v... 到下一个 ## v 前
sections = re.split(r'(?m)^(?=## v)', src)
header = sections[0]  # 文件头(# 版本更新说明 ... ---)

def parse_sections(sections):
    segs = []
    for s in sections[1:]:
        title = s.splitlines()[0]
        m = re.match(r'## v([0-9.]+) — ([0-9-]+)\(([^)]*)\)', title)
        assert m, f'无法解析段标题: {title}'
        ver, date, rest = m.groups()
        segs.append((ver, rest, s))
    return segs

segs = parse_sections(sections)
assert len(segs) == 34, f'段数 {len(segs)} != 34'

KINGDEE = {'v1.26.0','v1.25.0','v1.20.0','v1.19.0','v1.18.0','v1.17.0','v1.16.0',
           'v1.15.0','v1.14.0','v1.13.0','v1.12.0','v1.11.0','v1.10.0','v1.9.0',
           'v1.8.1','v1.8.0','v1.7.1','v1.7.0','v1.6.1','v1.6.0','v1.5.0',
           'v1.4.0','v1.4.1','v1.3.0'}
SENTIMENT = {'v1.24.0','v1.23.0','v1.22.0','v1.21.0','v1.2.0','v1.1.0','v1.0.0','v0.2.0'}
# v1.2.0 有两个(轨key + 生产三错),都归 sentiment;v0.1.0 归根

kd, sm, root = [], [], []
for ver, rest, s in segs:
    if ver in KINGDEE:
        kd.append(s)
    elif ver in SENTIMENT or (ver == 'v1.2.0' and ('轨 key' in s or '生产三错' in s)):
        sm.append(s)
    elif ver == 'v0.1.0':
        root.append(s)
    else:
        raise AssertionError(f'未归属: {ver} {rest}')

# 断言精确归属(设计文档)
assert len(kd) == 24, f'kingdee {len(kd)} != 24'
assert len(sm) == 9, f'sentiment {len(sm)} != 9'
assert len(root) == 1, f'root {len(root)} != 1'

def strip_agent_prefix(text):
    """段标题去掉 agent 前缀,如 (kingdee-plugin-agent:终审修复...) → (终审修复...)"""
    lines = text.splitlines()
    lines[0] = re.sub(r'\([a-z-]+-plugin-agent:', '(', lines[0], count=1)
    return '\n'.join(lines)

def write_agent(path, title_agent, sections_list):
    # 版本降序:按 ## v 号排序(semver 比较,float 不安全用 tuple 化)
    def key(s):
        ver = re.match(r'## v([0-9.]+)', s.splitlines()[0]).group(1)
        return tuple(int(x) for x in ver.split('.'))
    ordered = sorted(sections_list, key=key, reverse=True)
    body = '\n'.join(strip_agent_prefix(s).rstrip() for s in ordered)
    with open(path, 'w') as f:
        f.write(f'# {title_agent} 版本更新说明(CHANGELOG)\n\n'
                f'> 版本号独立管理(每 agent 独立序列),历史从根 CHANGELOG 迁移(2026-08-12)。\n'
                f'> 收尾规则:改动归本 agent → 更新本文件 + bump 版本号(当前最大号 +1)。\n\n'
                f'---\n\n{body}\n')

write_agent('agents/kingdee_plugin_agent/CHANGELOG.md', 'kingdee-plugin-agent', kd)
write_agent('agents/sentiment_query_agent/CHANGELOG.md', 'sentiment-query-agent', sm)
print('OK: kd=%d sm=%d root=%d' % (len(kd), len(sm), len(root)))
```

- [ ] **Step 2: 跑脚本验证段数**

Run: `.venv/bin/python /tmp/split_changelog.py`
Expected: `OK: kd=24 sm=9 root=1`

- [ ] **Step 3: 重建根 CHANGELOG.md(索引 + 项目级)**

```markdown
# 版本更新说明(CHANGELOG)

项目:agentStore — 基于 LangChain/LangGraph 的多步骤任务 Agent 组
仓库:https://github.com/sunweini/agentStore

---

## Agent 索引

| Agent | 当前版本 | CHANGELOG |
|---|---|---|
| sentiment-query-agent | v1.24.0 | [CHANGELOG](agents/sentiment_query_agent/CHANGELOG.md) |
| kingdee-plugin-agent | v1.26.0 | [CHANGELOG](agents/kingdee_plugin_agent/CHANGELOG.md) |

> 每 agent 独立版本号序列,撞号消除。改动归属哪个 agent → 更新该 agent 的
> CHANGELOG + bump 该 agent 版本号;纯项目级 → 本文件「项目级变更」区。

## 项目级变更

跨 agent / 公共层变更记这里:
- common/ 公共库(config/llm/rag/otel/db 等)
- compile_service(kingdee 用但属公共基建)
- 依赖升级 / 工作流约定 / 基建

## 项目级历史

### v0.1.0 — 2026-08-06(项目初始化)

(自 v0.1.0 后,agent 功能版本全归各 agent CHANGELOG,根文件仅记项目级。)
```

(将上面内容写入 `CHANGELOG.md`,覆盖根文件。)

- [ ] **Step 4: 验证迁移完整性(段数 + 降序 + 无残留)**

Run:
```bash
echo "根段数: $(grep -c '^## v' CHANGELOG.md)"
echo "kingdee: $(grep -c '^## v' agents/kingdee_plugin_agent/CHANGELOG.md)"
echo "sentiment: $(grep -c '^## v' agents/sentiment_query_agent/CHANGELOG.md)"
grep -n '^## v' agents/kingdee_plugin_agent/CHANGELOG.md | head -3
grep -n '^## v' agents/sentiment_query_agent/CHANGELOG.md | head -3
```
Expected: 根=1, kingdee=24, sentiment=9;两 agent 首行是最高版本(v1.26.0 / v1.24.0);kingdee 标题无 `kingdee-plugin-agent:` 前缀。

- [ ] **Step 5: 抽查内容完整性(迁移非截断)**

Run: `grep -c "SQLite checkpointer" agents/kingdee_plugin_agent/CHANGELOG.md && grep -c "多用户配额管理" agents/sentiment_query_agent/CHANGELOG.md && grep -c "项目初始化" CHANGELOG.md`
Expected: 各 ≥1(关键段内容在目标文件)

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md agents/kingdee_plugin_agent/CHANGELOG.md agents/sentiment_query_agent/CHANGELOG.md
git commit -m "docs: 每 agent 独立 CHANGELOG(34 段历史按归属迁移,根降为索引+项目级)"
```

---

### Task 2: 根 CLAUDE.md + 两 agent CLAUDE.md 收尾约定

**Files:**
- Modify: `CLAUDE.md:17`(开发流程第 4 条)
- Modify: `agents/kingdee_plugin_agent/CLAUDE.md`(常用操作区加收尾条目)
- Modify: `agents/sentiment_query_agent/CLAUDE.md`(常用操作区加收尾条目)

**Interfaces:**
- Consumes: Task 1 的独立 CHANGELOG 文件 + 版本号规则(kingdee v1.27 / sentiment v1.25 续)
- Produces: 收尾流程约定(改动归 agent → 写该 agent CHANGELOG + bump)

- [ ] **Step 1: 根 CLAUDE.md 第 4 条改写**

```markdown
4. **每次开发收尾必须更新 CHANGELOG**(详见 dev-standards §4):改动归属哪个
   agent → 更新该 agent 的 `agents/<agent>/CHANGELOG.md` 并 bump 该 agent 版本号
   (当前最大号 +1);纯项目级(common/compile_service/依赖)→ 根 `CHANGELOG.md`
   项目级区。测试通过后 commit 推送。
```

(替换现 CLAUDE.md 第 17 行。)

- [ ] **Step 2: kingdee CLAUDE.md 常用操作区加收尾条目**

在 `agents/kingdee_plugin_agent/CLAUDE.md` 「常用操作」区(「跑测试」条目后)加:

```markdown
- **收尾更新 CHANGELOG**:改动归本 agent → 写本 agent 的
  `agents/kingdee_plugin_agent/CHANGELOG.md`,bump 版本号(当前最大号 +1,现 v1.26.0 → 下版 v1.27);
  纯项目级(common/compile_service/依赖)→ 根 `CHANGELOG.md` 项目级区。
```

- [ ] **Step 3: sentiment CLAUDE.md 常用操作区加收尾条目**

在 `agents/sentiment_query_agent/CLAUDE.md` 合适位置(常用操作/开发流程区)加:

```markdown
- **收尾更新 CHANGELOG**:改动归本 agent → 写本 agent 的
  `agents/sentiment_query_agent/CHANGELOG.md`,bump 版本号(当前最大号 +1,现 v1.24.0 → 下版 v1.25);
  纯项目级(common/依赖)→ 根 `CHANGELOG.md` 项目级区。
```

- [ ] **Step 4: 验证无破坏**

Run: `grep -n "CHANGELOG" CLAUDE.md agents/kingdee_plugin_agent/CLAUDE.md agents/sentiment_query_agent/CLAUDE.md`
Expected: 三文件各含新收尾约定(根第 4 条改写,两 agent 各加一条)

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md agents/kingdee_plugin_agent/CLAUDE.md agents/sentiment_query_agent/CLAUDE.md
git commit -m "docs: 收尾约定改每 agent 独立 CHANGELOG + 版本号(根/kingdee/sentiment CLAUDE.md)"
```

---

### Task 3: 全量验证 + 撞号场景检查

**Files:**
- 无改动(纯验证)

**Interfaces:**
- Consumes: Task 1/2 的产物
- Produces: 验证结论

- [ ] **Step 1: 段数恒等总断言**

Run:
```bash
echo "总数 = 根(1) + kingdee + sentiment:"; echo "$(grep -c '^## v' agents/kingdee_plugin_agent/CHANGELOG.md) + $(grep -c '^## v' agents/sentiment_query_agent/CHANGELOG.md) + $(grep -c '^## v' CHANGELOG.md) = $(($(grep -c '^## v' agents/kingdee_plugin_agent/CHANGELOG.md) + $(grep -c '^## v' agents/sentiment_query_agent/CHANGELOG.md) + $(grep -c '^## v' CHANGELOG.md)))"
```
Expected: `24 + 9 + 1 = 34`

- [ ] **Step 2: 撞号消除断言**

Run: `grep -h '^## v' agents/*/CHANGELOG.md | sort | uniq -d`
Expected: 空输出(两 agent 无跨文件重复版本号;sentiment 内部两个 v1.2.0 是本文件内重复,uniq -d 跨文件不显示)

- [ ] **Step 3: 每文件降序断言**

Run: `awk '/^## v/{gsub(/v/,"",$2); split($2,a,"."); v=a[1]*1000+a[2]*10+a[3]; if(v>prev) print FILENAME": 降序破坏 "prev" -> "v; prev=v}' agents/kingdee_plugin_agent/CHANGELOG.md agents/sentiment_query_agent/CHANGELOG.md`
Expected: 无输出(降序正确)

- [ ] **Step 4: 跑测试确认无回归(文档改动不应碰代码)**

Run: `.venv/bin/python -m pytest tests/test_kingdee_agent.py tests/test_sentiment_query_agent.py -q 2>&1 | tail -2`
Expected: 全过(文档改动零代码影响,回归确认)

- [ ] **Step 5: Commit(如验证脚本本身需留档)**

迁移脚本 `/tmp/split_changelog.py` 不入库(一次性);若 Step 2/3 断言发现异常,修 Task 1 产物后重跑验证,再 commit 修正。

---

## Self-Review 记录

- **Spec 覆盖**:① 文件结构 → Task 1 ✅;② 版本号规则 → Task 1 Step 4/5(当前版本)+ Task 2(续号约定)+ Task 3 Step 2(撞号)✅;③ 历史迁移 34 段 → Task 1 Step 1-3 + 断言 24/9/1 ✅;④ 根重建 → Task 1 Step 3 ✅;⑤ 收尾约定 → Task 2 ✅;⑥ 验证 → Task 3 ✅。
- **占位扫描**:迁移脚本含完整 34 段归属集合(逐版本枚举),无 TBD;重建根文件内容完整给出。
- **类型一致**:`kingdee v1.26.0 / sentiment v1.24.0` 在 Task 1(索引表)、Task 2(续号)、Task 3(首行断言)一致;`24/9/1` 在 Task 1 断言与 Task 3 恒等断言一致。
