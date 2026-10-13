"""第 05a 步预筛：精确匹配的候选全部保留；只由 TF-IDF 找到的候选，满足任一条才保留，其余排除（留表供第 06 步抽查漏配）。

  首词相同：样本核心名称的第一个词出现在目标核心名称中（名称的辨识部分通常在前，后面多是行业词）
  只差空格：去掉空格后相同（核心名称或去后缀全称都比，泛用词被拆开写时也能找回，如 SupplyScape ↔ Supply Scape）
  拼写相近：至少 7 个字母、Damerau 距离 ≤ 1，且首尾字母相同、数字相同（排除 PerimeterX/Perimeter、100Plus/500Plus）

用法：python prefilter.py 候选.csv 输出.csv
  候选.csv 为 candidates.py 的输出（列 core_s、core_c、nolegal_s/name_s、nolegal_c/name_c、exact_full、exact_core）
输出：加列 prefilter（精确-全称 / 精确-核心 / 首词相同 / 只差空格 / 拼写相近 / 排除）
"""
import re
import sys

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")


def damerau1(a, b):
    """Damerau 距离是否 ≤ 1（一处增删改，或相邻两字母调换）。"""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def rule(r):
    if int(r.get("exact_full", 0)):
        return "精确-全称"
    if int(r.get("exact_core", 0)):
        return "精确-核心"
    cs, cc = str(r["core_s"]), str(r["core_c"])
    first = cs.split()[0] if cs.split() else ""
    if first and first in cc.split() or (len(first) >= 4 and first in cc.replace(" ", "")):
        return "首词相同"
    ns, nc = str(r.get("nolegal_s", r.get("name_s", ""))), str(r.get("nolegal_c", r.get("name_c", "")))
    if cs.replace(" ", "") == cc.replace(" ", "") or ns.replace(" ", "") == nc.replace(" ", ""):
        return "只差空格"
    a, b = cs.replace(" ", ""), cc.replace(" ", "")
    if min(len(a), len(b)) >= 7 and a[0] == b[0] and a[-1] == b[-1] and re.findall(r"\d+", a) == re.findall(r"\d+", b) and damerau1(a, b):
        return "拼写相近"
    return "排除"


def run(c):
    c = c.copy()
    c["prefilter"] = [rule(r) for r in c.to_dict("records")]
    return c


if __name__ == "__main__":
    c = run(pd.read_csv(sys.argv[1], dtype=str, keep_default_na=False))
    c.to_csv(sys.argv[2], index=False, encoding="utf-8-sig")
    print(c.prefilter.value_counts().to_string())
