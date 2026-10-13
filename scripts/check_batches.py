"""检查 AI 批次结果：每个 {模型}_NNN.md 是否有对应的 .json，编号是否齐全、有无多出、判断值是否合法。

用法：python check_batches.py 批次文件夹 模型前缀（如 sonnet / opus / web）
漏掉的卡片编号会列出来，单独做一个补判批次即可。
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
B, model = Path(sys.argv[1]), sys.argv[2]
tot, missing = {"是": 0, "否": 0, "不确定": 0}, []
for md in sorted(B.glob(f"{model}_*.md")):
    ids = re.findall(r"### 卡 (\S+)", md.read_text(encoding="utf-8"))
    js = md.with_suffix(".json")
    if not js.exists():
        print(md.stem, "未完成")
        continue
    try:
        j = json.loads(js.read_text(encoding="utf-8"))
    except Exception as e:
        print(md.stem, "JSON 错误", e)
        continue
    got = {x.get("pair_id") for x in j}
    bad = [x for x in j if x.get("judgment") not in tot]
    for x in j:
        if x.get("judgment") in tot:
            tot[x["judgment"]] += 1
    miss, extra = set(ids) - got, got - set(ids)
    missing += sorted(miss)
    print(md.stem, len(j), "缺", sorted(miss) if miss else 0, "多", sorted(extra) if extra else 0, "非法", len(bad))
print("合计", tot, "；缺", len(missing), missing[:20])
