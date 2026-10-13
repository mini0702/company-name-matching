"""第 06 步：分层抽样审核与错误率（新匹配和审核已有匹配都适用）。

用法：
  python audit.py make    --pop 总体.csv --design 设计.csv --dir 审核     抽样 → 审核表.xlsx（只能运行一次）
  python audit.py ruling  --dir 审核 [--suggest 复核.json]                 初审标出问题的行 → 研究者裁定.xlsx
  python audit.py score   --dir 审核                                      读回初审与裁定 → 审核结果.xlsx

总体.csv：每个待审对象一行，必须有 pid、layer（层名）；其余列是给审核员看的证据（列名用英文，说明见 --labels）。
设计.csv：layer、n（抽样数，空 = 全审）、bad（这一层判哪个答案算错：接受类的层填"否"，排除类的层填"是"）、question（这一层要回答的问题）。
原则：
  - 各层总数在抽样时固定（写进 抽样设计.csv）；改正后集合会变，推算错误率仍用抽样时的总数。
  - make 只能运行一次，否则会换掉已审样本；要追加审核层，另建一个设计文件与目录。
  - score 只写 审核结果.xlsx，不覆盖 审核表.xlsx、研究者裁定.xlsx（研究者可能正在填）。
  - 初审由另一个大模型做时，判错/不确定的交研究者裁定；可先让第三个模型按口径复核并给出建议（--suggest）。
  - 报告两种口径：只计确认的错误；"不确定"也计为错误（严格）。零错误层给 3/n 的 95% 上界（未审核部分）。
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from xlsx_style import write_xlsx  # noqa: E402

SEED = 2026


def paths(d):
    d = Path(d)
    return d / "抽样设计.csv", d / "审核表.xlsx", d / "研究者裁定.xlsx", d / "研究者裁定.csv", d / "审核结果.xlsx"


def labels_of(a):
    return json.loads(Path(a.labels).read_text(encoding="utf-8")) if getattr(a, "labels", None) else {}


def make(a):
    DESIGN, SHEET, *_ = paths(a.dir)
    Path(a.dir).mkdir(exist_ok=True)
    if DESIGN.exists():
        sys.exit("抽样设计.csv 已存在：不要重新抽样（会换掉已审样本）。追加审核层请用新目录。")
    pop = pd.read_csv(a.pop, dtype=str, keep_default_na=False)
    des = pd.read_csv(a.design, dtype=str, keep_default_na=False)
    rows, rec = [], []
    for r in des.itertuples():
        x = pop[pop.layer == r.layer]
        n = int(r.n) if str(r.n).strip() else len(x)
        s = x if n >= len(x) else x.sample(n, random_state=SEED)
        rows.append(s.assign(question=r.question, 审核="", 审核理由=""))
        rec.append({"layer": r.layer, "总数": len(x), "抽样数": len(s), "bad": r.bad})
    sheet = pd.concat(rows, ignore_index=True)
    ev = [c for c in sheet.columns if c not in ("pid", "layer", "question", "审核", "审核理由")]
    sheet = sheet[["pid", "审核", "审核理由", "layer", "question"] + ev]
    pd.DataFrame(rec).to_csv(DESIGN, index=False, encoding="utf-8-sig")
    desc = {"pid": "编号", "审核": "【请填】是 / 否 / 不确定（回答 question 列的问题）", "审核理由": "【请填】一句话理由",
            "layer": "审核层", "question": "本行要回答的问题"}
    desc.update({c: labels_of(a).get(c, c) for c in ev})
    write_xlsx(SHEET, {"说明": ["各层随机抽样（小层全审）。每行回答 question 列的问题，在『审核』填 是 / 否 / 不确定，在『审核理由』写一句话。",
                              "第 1 行是字段名，第 2 行是说明和来源。审核方式要在文档中如实写明（如：GPT 初审、研究者裁定）。"],
                       "审核表": (sheet, desc)})
    print(pd.DataFrame(rec).to_string(index=False), "\n合计", len(sheet))


def flagged(sheet, design):
    bad = design.set_index("layer").bad
    a = sheet["审核"].fillna("").str.strip()
    return sheet[(a == "不确定") | (a == sheet.layer.map(bad))]


def read_ruling(d):
    _, _, RX, RC, _ = paths(d)
    r = pd.read_csv(RC, dtype=str).set_index("pid").ruling if RC.exists() else pd.Series(dtype=str)
    if RX.exists():
        x = pd.read_excel(RX, sheet_name="裁定", dtype=str).iloc[1:]
        new = x.set_index("pid")["你的裁定"].fillna("").str.strip()
        new = new[new.isin(["是", "否", "不确定"])]
        r = pd.concat([r[~r.index.isin(new.index)], new])
        r.rename("ruling").rename_axis("pid").reset_index().to_csv(RC, index=False, encoding="utf-8-sig")
    return r


def ruling(a):
    DESIGN, SHEET, RX, *_ = paths(a.dir)
    sh = pd.read_excel(SHEET, sheet_name="审核表", dtype=str).iloc[1:]
    need = flagged(sh, pd.read_csv(DESIGN, dtype=str)).copy()
    prev = read_ruling(a.dir)
    need.insert(1, "你的裁定", need.pid.map(prev).fillna(""))
    if a.suggest:
        s = pd.DataFrame(json.loads(Path(a.suggest).read_text(encoding="utf-8-sig"))).set_index("pair_id")
        need.insert(3, "suggest", need.pid.map(s.judgment)); need.insert(4, "suggest_reason", need.pid.map(s.reason))
    head = pd.read_excel(SHEET, sheet_name="审核表", dtype=str, nrows=1).iloc[0].to_dict()
    head.update({"你的裁定": "【请填】是 / 否 / 不确定", "审核": "初审结果", "suggest": "复核建议（另一模型按口径只看证据）", "suggest_reason": "复核理由"})
    if RX.exists():
        print("提示：研究者裁定.xlsx 已存在，已读回其中的裁定后重新生成")
    write_xlsx(RX, {"说明": ["初审判错或不确定的行。请在第 2 列填 是 / 否 / 不确定，保存并关闭后运行 score。"], "裁定": (need, head)},
               fill_cols=("你的裁定",))
    print("待裁定", len(need))


def score(a):
    DESIGN, SHEET, _, _, RES = paths(a.dir)
    des = pd.read_csv(DESIGN, dtype=str).set_index("layer")
    sh = pd.read_excel(SHEET, sheet_name="审核表", dtype=str).iloc[1:]
    sh["final"] = sh.pid.map(read_ruling(a.dir)).fillna(sh["审核"].fillna("").str.strip())
    out = []
    for layer, g in sh.groupby("layer", sort=False):
        N, n, bad = int(des.loc[layer, "总数"]), len(g), des.loc[layer, "bad"]
        conf, uns, blank = int((g.final == bad).sum()), int((g.final == "不确定").sum()), int((g.final == "").sum())
        full = n >= N
        out.append({"层": layer, "总数": N, "已审": n, "未填": blank, "确认错误": conf, "不确定": uns,
                    "推算错误（确认）": round(conf / n * N, 1), "推算错误（含不确定）": round((conf + uns) / n * N, 1),
                    "全审已改正": "是" if full else "", "零错误时未审部分 95% 上界（3/n）": "" if full or conf + uns else round(3 / n * (N - n), 1)})
    res = pd.DataFrame(out)
    write_xlsx(RES, {"说明": ["推算错误 = 抽样错误比例 × 抽样时该层总数。全审层的错误改正后剩余为 0；抽样层中审到的错误改正，未审部分按比例推算。",
                             "『确认』只计裁定为错的；『含不确定』把不确定也计为错（严格口径）。"],
                     "错误率": res})
    print(res.to_string(index=False))
    left = sh[sh.final == ""]
    if len(left):
        print("还有", len(left), "行没有初审结果")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "ruling", "score"])
    ap.add_argument("--dir", required=True); ap.add_argument("--pop"); ap.add_argument("--design"); ap.add_argument("--labels")
    ap.add_argument("--suggest")
    a = ap.parse_args()
    {"make": make, "ruling": ruling, "score": score}[a.cmd](a)
