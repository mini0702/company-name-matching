"""候选配对：精确匹配（去后缀全称 / 核心名称）∪ TF-IDF 字符 n-gram 模糊匹配（英文 3–5，中文 1–3）。
中文数据另用 name_std_zh.abbreviations / is_subsequence 补充简称候选（简称与全称长度差很大，TF-IDF 容易漏）。

用法（两个输入 csv 都是 name_std 处理后的"名称变体"表，每个变体一行）：
  python candidates.py 样本.csv 目标.csv 输出.csv [--topk 10] [--min-cos 0.5]
  样本.csv 必须有列：sid（样本编号）、name（原始名称）、nolegal、core
  目标.csv 必须有列：tid（目标名称编号）、name、nolegal、core
输出：每个 (sid, tid) 一行，列 methods / tfidf / name_s / name_c / core_s / core_c / exact_full / exact_core。

"""
import argparse

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer


def run(S, C, topk=10, min_cos=0.5, ngram=(3, 5)):
    """ngram：英文用 (3, 5)；中文用 (1, 3)（汉字信息量大，2–3 字的字号也能比较）。"""
    ex_full = S.merge(C, on="nolegal", suffixes=("_s", "_c")).assign(method="精确-全称")
    ex_core = S.merge(C, on="core", suffixes=("_s", "_c")).assign(method="精确-核心")
    s_str = S.nolegal.drop_duplicates().reset_index(drop=True)
    c_str = C.nolegal.drop_duplicates().reset_index(drop=True)
    pad = lambda x: f" {x} "
    vec = TfidfVectorizer(analyzer="char", ngram_range=ngram, dtype=np.float32, sublinear_tf=True)
    vec.fit(pd.concat([s_str, c_str]).map(pad))
    Xs, Xc = vec.transform(s_str.map(pad)), vec.transform(c_str.map(pad)).T.tocsr()
    rows = []
    # 稀疏计算：目标名称很多（几十万）时，稠密矩阵会占用过多内存；每块先去掉低于门槛的相似度，再取前 topk
    step = max(20, min(500, 20_000_000 // max(Xc.shape[1], 1)))
    for a in range(0, Xs.shape[0], step):
        sim = (Xs[a:a + step] @ Xc).tocsr()
        sim.data[sim.data < min_cos] = 0
        sim.eliminate_zeros()
        for i in range(sim.shape[0]):
            lo, hi = sim.indptr[i], sim.indptr[i + 1]
            if lo == hi:
                continue
            d, j = sim.data[lo:hi], sim.indices[lo:hi]
            if len(d) > topk:
                keep = np.argpartition(-d, topk)[:topk]
                d, j = d[keep], j[keep]
            rows.extend((s_str[a + i], c_str[jj], float(dd)) for dd, jj in zip(d, j))
    fz = pd.DataFrame(rows, columns=["nolegal_s", "nolegal_c", "tfidf"])
    fz = (fz.merge(S.rename(columns={"nolegal": "nolegal_s"}), on="nolegal_s")
            .merge(C.rename(columns={"nolegal": "nolegal_c"}), on="nolegal_c", suffixes=("_s", "_c")).assign(method="TF-IDF"))
    ex = pd.concat([ex_full, ex_core])
    for side, df in (("s", S), ("c", C)):
        idc = "sid" if side == "s" else "tid"
        ex = ex.drop(columns=[c for c in ex.columns if c in (f"core_{side}", f"nolegal_{side}", "core", "nolegal")], errors="ignore")
        ex = ex.merge(df[[idc, "name", "nolegal", "core"]].drop_duplicates([idc, "name"])
                      .rename(columns={"name": f"name_{side}", "nolegal": f"nolegal_{side}", "core": f"core_{side}"}),
                      on=[idc, f"name_{side}"], how="left")
    pad_tf = lambda a, b: np.asarray(vec.transform(a.map(pad)).multiply(vec.transform(b.map(pad))).sum(axis=1)).ravel()
    ex["tfidf"] = pad_tf(ex.nolegal_s.fillna(""), ex.nolegal_c.fillna(""))
    allp = pd.concat([ex, fz], ignore_index=True).sort_values("tfidf", ascending=False)
    g = allp.groupby(["sid", "tid"], sort=False)
    out = g.agg(methods=("method", lambda s: " + ".join(sorted(set(s)))), tfidf=("tfidf", "max"),
                name_s=("name_s", "first"), name_c=("name_c", "first"),
                core_s=("core_s", "first"), core_c=("core_c", "first")).reset_index()
    out["exact_full"] = out.methods.str.contains("精确-全称").astype(int)
    out["exact_core"] = out.methods.str.contains("精确-核心").astype(int)
    out["tfidf"] = out.tfidf.round(3)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sample"); ap.add_argument("target"); ap.add_argument("out")
    ap.add_argument("--topk", type=int, default=10); ap.add_argument("--min-cos", type=float, default=0.5)
    ap.add_argument("--lang", choices=["en", "zh"], default="en", help="zh：字符 1–3 gram")
    a = ap.parse_args()
    S = pd.read_csv(a.sample, dtype=str, keep_default_na=False)
    C = pd.read_csv(a.target, dtype=str, keep_default_na=False)
    res = run(S, C, a.topk, a.min_cos, ngram=(1, 3) if a.lang == "zh" else (3, 5))
    res.to_csv(a.out, index=False, encoding="utf-8-sig")
    print("候选", len(res), "；精确-全称", int(res.exact_full.sum()), "；精确-核心（不含全称）",
          int(((res.exact_core == 1) & (res.exact_full == 0)).sum()), "；只由 TF-IDF", int((res.methods == "TF-IDF").sum()))
