"""AI 判断的批次准备、检查与合并（第 05b 步；审核已有匹配时也用它）。

两种方向（--mode，或在证据卡表中用 mode 列逐行指定）：
  match（新匹配，防错配）：Sonnet 判否 → 排除；判是/不确定 → Opus 复核；两个都判是 → 接受；其余 → 上网核实
  audit（审核已有配对）：Sonnet 判是 → 维持；判否/不确定 → Opus 复核；两个都判否 → 错配；其余 → 上网核实
  上网核实：是 / 否 直接定；不确定 → 研究者判断；研究者判断不出（不确定）→ match 按不匹配、audit 按错配（证据不足）

用法：
  python ai_batches.py prepare sonnet --cards 证据卡.csv --dir 批次 [--labels 标签.json] [--cols a,b,c] [--mode match]
  python ai_batches.py prepare opus   --cards ...                     （按上面的规则挑出需要 Opus 复核的）
  python ai_batches.py prepare web    --cards ... --decisions 判断结果.csv   （每批 10 对）
  python ai_batches.py check  sonnet --dir 批次                         （每批是否齐全、有无非法值；缺的再 prepare 一次即补出）
  python ai_batches.py prompt sonnet --dir 批次 --guide AI判断说明.md     （打印给子代理的提示词）
  python ai_batches.py merge --cards ... --dir 批次 --out 判断结果.csv [--manual 人工判断.csv] [--weak 上网弱证据.csv]
                                                                       → 判断结果.csv + 判断结果.xlsx（人工判断表）
证据卡表必须有 pid 列（配对编号，不含空格）；其余列按 --cols 顺序写进卡片，标签取 --labels（{列名: 中文标签（注明来源）}）。
研究者在 xlsx「人工判断」表第 2 列填的结果每次 merge 都先读回并存进 --manual，不会被覆盖。
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from xlsx_style import write_xlsx  # noqa: E402

VALID = {"是", "否", "不确定"}


def v(x, n=None):
    if x is None or (not isinstance(x, str) and pd.isna(x)) or x == "":
        return "—"
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    x = str(x).replace("\n", " ")
    return x[:n] + "…" if n and len(x) > n else x


def card(r, cols, labels, maxlen=220):
    return "\n".join([f"### 卡 {r['pid']}"] + [f"- {labels.get(c, c)}：{v(r.get(c), maxlen)}" for c in cols]) + "\n"


def read_results(d, stage):
    out = []
    for f in sorted(Path(d).glob(f"{stage}_*.json")):
        out += json.loads(f.read_text(encoding="utf-8-sig"))     # 子代理写的 JSON 可能带 BOM
    df = pd.DataFrame(out, columns=["pair_id", "judgment", "confidence", "reason", "evidence"]).rename(columns={"pair_id": "pid"})
    return df.drop_duplicates("pid", keep="last")


def load(a):
    c = pd.read_csv(a.cards, dtype=str, keep_default_na=False)
    if "mode" not in c:
        c["mode"] = a.mode
    labels = json.loads(Path(a.labels).read_text(encoding="utf-8")) if a.labels else {}
    cols = a.cols.split(",") if a.cols else [x for x in c.columns if x not in ("pid", "mode")]
    return c, labels, cols


def write_batches(df, stage, d, cols, labels, size, extra=None):
    d = Path(d); d.mkdir(exist_ok=True)
    n = len(list(d.glob(f"{stage}_*.md")))
    df = df.sample(frac=1, random_state=2026)
    for i in range(0, len(df), size):
        n += 1
        part = df.iloc[i:i + size]
        body = [card(r, cols, labels) + (extra(r) if extra else "") for r in part.to_dict("records")]
        (d / f"{stage}_{n:03d}.md").write_text(f"# {stage} 第 {n} 批（{len(part)} 对）。— 表示缺失。\n\n" + "\n".join(body), encoding="utf-8")
    print(f"{stage}：{len(df)} 对，批次编号到 {n}")


def prepare(a):
    c, labels, cols = load(a)
    done = set(read_results(a.dir, a.stage).pid)
    if a.stage == "sonnet":
        todo = c[~c.pid.isin(done)]
        write_batches(todo, "sonnet", a.dir, cols, labels, a.size)
    elif a.stage == "opus":
        s = c.pid.map(read_results(a.dir, "sonnet").set_index("pid").judgment)
        need = ((c["mode"] == "match") & s.isin(["是", "不确定"])) | ((c["mode"] == "audit") & s.isin(["否", "不确定"]))
        write_batches(c[need & ~c.pid.isin(done)], "opus", a.dir, cols, labels, a.size)
    else:
        dec = pd.read_csv(a.decisions, dtype=str, keep_default_na=False)
        todo = dec[(dec.decision == "上网核实") & ~dec.pid.isin(done)]
        x = c.merge(todo[["pid", "sonnet_judgment", "sonnet_reason", "opus_judgment", "opus_reason"]], on="pid")
        write_batches(x, "web", a.dir, cols, labels, 10, extra=lambda r: (
            f"- 两个 AI 的意见：Sonnet {v(r['sonnet_judgment'])}（{v(r['sonnet_reason'])}）；Opus {v(r['opus_judgment'])}（{v(r['opus_reason'])}）\n"))


def check(a):
    tot, missing = {k: 0 for k in VALID}, []
    for md in sorted(Path(a.dir).glob(f"{a.stage}_*.md")):
        ids = set(re.findall(r"### 卡 (\S+)", md.read_text(encoding="utf-8")))
        js = md.with_suffix(".json")
        if not js.exists():
            print(md.stem, "未完成"); missing += sorted(ids); continue
        try:
            j = json.loads(js.read_text(encoding="utf-8-sig"))
        except Exception as e:
            print(md.stem, "JSON 错误", e); missing += sorted(ids); continue
        got = {x.get("pair_id") for x in j}
        bad = [x for x in j if x.get("judgment") not in VALID]
        nourl = [x for x in j if a.stage == "web" and x.get("judgment") != "不确定" and "http" not in str(x.get("evidence", ""))]
        for x in j:
            if x.get("judgment") in VALID:
                tot[x["judgment"]] += 1
        miss = ids - got
        missing += sorted(miss)
        print(md.stem, len(j), "缺", len(miss), "多", len(got - ids), "非法", len(bad), ("；上网判定却没有网址 " + str(len(nourl))) if nourl else "")
    print("合计", tot, "；缺", len(missing), "（再运行 prepare 同一阶段即可补出缺的卡）")


def prompt(a):
    md = sorted(Path(a.dir).glob(f"{a.stage}_*.md"))
    p = (md[0] if md else Path(a.dir) / f"{a.stage}_001.md").resolve()
    a.guide, a.web_guide = Path(a.guide).resolve(), Path(a.web_guide).resolve()
    if a.stage == "web":
        print(f"""你是上网核实员。先读取：{a.guide}、{a.web_guide}、{p}（约 10 对）。按说明用 WebSearch / WebFetch 逐对核实（每对 1 次搜索，必要时再打开 1 个网页），不要读取本机其他文件。
然后用 Write 把结果写入 {p.with_suffix('.json')}：合法 JSON 数组（UTF-8，不带 BOM），字段 pair_id / judgment / confidence / reason / evidence，一对都不能少；evidence 只写本次真实看到的网址，查不到判"不确定"。写完只回复：已写入 N 条。""")
    else:
        print(f"""你是一个独立的判断员。只读取下面两个文件，不要读取或搜索任何其他文件，不要上网：
1. {a.guide}（判断标准和输出格式）
2. {p}（约 100 张证据卡；请分次读取，每次 350 行）
逐张判断全部卡片，然后用 Write 把结果写入 {p.with_suffix('.json')}：合法 JSON 数组（UTF-8，不带 BOM），字段 pair_id / judgment / confidence / reason，pair_id 与"### 卡 xxx"完全一致，一张都不能少。写完只回复：已写入 N 条。""")
    print(f"\n（对其余批次，把文件名中的 {p.stem} 换掉即可；model 用 {'sonnet' if a.stage != 'opus' else 'opus'}；并发子代理不要超过约 12 个，上网批次会被限流）")


def decide(c):
    s, o, w, m = c.sonnet_judgment, c.opus_judgment, c.web_judgment, c.manual_judgment
    match = c["mode"] == "match"
    yes, no = match.map({True: "接受", False: "维持"}), match.map({True: "排除", False: "错配"})
    dec = pd.Series("缺 Sonnet 结果", index=c.index)
    dec[match & (s == "否")] = "排除（Sonnet 判否）"
    dec[~match & (s == "是")] = "维持（Sonnet 判是）"
    need_o = (match & s.isin(["是", "不确定"])) | (~match & s.isin(["否", "不确定"]))
    dec[need_o & o.isna()] = "缺 Opus 结果"
    dec[match & need_o & (s == "是") & (o == "是")] = "接受（两个 AI 都判是）"
    dec[~match & need_o & (s == "否") & (o == "否")] = "错配（两个 AI 都判否）"
    web = need_o & o.notna() & ~dec.str.startswith(("接受", "错配"))
    dec[web] = "上网核实"
    dec[web & (w == "是")] = yes[web & (w == "是")] + "（上网核实）"
    dec[web & (w == "否")] = no[web & (w == "否")] + "（上网核实）"
    hum = web & (w == "不确定")
    dec[hum] = "人工判断"
    dec[hum & (m == "是")] = yes[hum & (m == "是")] + "（人工判断）"
    dec[hum & m.isin(["否", "不确定"])] = no[hum & m.isin(["否", "不确定"])] + "（人工判断）"
    return dec, hum


def merge(a):
    c, labels, cols = load(a)
    for st in ("sonnet", "opus", "web"):
        r = read_results(a.dir, st).add_prefix(f"{st}_").rename(columns={f"{st}_pid": "pid"})
        c = c.merge(r, on="pid", how="left")
    if a.weak and Path(a.weak).exists():            # 上网判"是"但证据弱（品牌延续、被收购后仍用原名、兄弟公司）→ 降为不确定
        wk = pd.read_csv(a.weak, dtype=str).set_index("pid").reason
        m = c.pid.isin(wk.index) & (c.web_judgment == "是")
        c.loc[m, "weak_reason"], c.loc[m, "web_judgment"] = c.loc[m, "pid"].map(wk), "不确定"
    out_x = Path(a.out).with_suffix(".xlsx")
    man = pd.read_csv(a.manual, dtype=str).set_index("pid").judgment if a.manual and Path(a.manual).exists() else pd.Series(dtype=str)
    if out_x.exists():                                # 先读回研究者在 xlsx 里填的
        h = pd.read_excel(out_x, sheet_name=None, dtype=str).get("人工判断")
        if h is not None and "你的判断" in h:
            h = h.iloc[1:]
            new = h.set_index("pid")["你的判断"].fillna("").str.strip()
            new = new[new.isin(VALID)]
            man = pd.concat([man[~man.index.isin(new.index)], new])
    if a.manual:
        man.rename("judgment").rename_axis("pid").reset_index().to_csv(a.manual, index=False, encoding="utf-8-sig")
    c["manual_judgment"] = c.pid.map(man)
    c["decision"], need = decide(c)
    c.to_csv(a.out, index=False, encoding="utf-8-sig")
    stats = c.groupby(["mode", "decision"]).size().rename("数量").reset_index()
    print(stats.to_string(index=False))
    desc = {"pid": "配对编号", "你的判断": "【请填】是 / 否 / 不确定（是否同一家公司）"}
    desc.update({k: labels.get(k, k) for k in cols})
    desc.update({f"{s}_{f}": f"{n}{t}" for s, n in (("sonnet", "Sonnet "), ("opus", "Opus "), ("web", "上网核实 "))
                 for f, t in (("judgment", "判断"), ("reason", "理由"), ("evidence", "依据网址"))})
    desc.update({"weak_reason": "【提示】上网判是但证据弱的原因", "decision": "当前结论（括号内为由哪一步决定）"})
    hv = c[need].assign(你的判断=lambda d: d.manual_judgment.fillna(""))
    hcols = ["pid", "你的判断"] + cols + [x for x in ("sonnet_judgment", "sonnet_reason", "opus_judgment", "opus_reason",
                                                    "web_judgment", "web_reason", "web_evidence", "weak_reason") if x in c]
    write_xlsx(out_x, {
        "说明": ["判断结果汇总见『全部』表；『人工判断』表请在第 2 列填 是 / 否 / 不确定，保存并关闭后重新运行 merge。",
               "第 1 行是字段名，第 2 行是说明和来源。"] + [f"{r.mode} | {r.decision}：{r.数量}" for r in stats.itertuples()],
        "人工判断": (hv.reindex(columns=hcols), desc),
        "全部": (c.reindex(columns=["pid", "decision"] + cols + hcols[2 + len(cols):]), desc),
    })


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "check", "prompt", "merge"])
    ap.add_argument("stage", nargs="?", choices=["sonnet", "opus", "web"])
    ap.add_argument("--cards"); ap.add_argument("--dir", required=True); ap.add_argument("--labels"); ap.add_argument("--cols")
    ap.add_argument("--mode", default="match", choices=["match", "audit"]); ap.add_argument("--size", type=int, default=100)
    ap.add_argument("--decisions"); ap.add_argument("--out"); ap.add_argument("--manual"); ap.add_argument("--weak")
    ap.add_argument("--guide", default="AI判断说明.md"); ap.add_argument("--web-guide", default="上网核实说明.md")
    a = ap.parse_args()
    {"prepare": prepare, "check": check, "prompt": prompt, "merge": merge}[a.cmd](a)
