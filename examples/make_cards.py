"""示例：把预筛保留的候选和目标一侧的证据拼成证据卡表（每对一行，pid = 样本编号-目标编号）。
真实任务中，证据卡还应加上样本一侧的核对信息（成立年、所在地、行业、简介、收购方）和目标一侧的行业、议题、母公司等。
用法：python make_cards.py 预筛.csv target_names.csv 证据卡.csv
"""
import sys

import pandas as pd

pre = pd.read_csv(sys.argv[1], dtype=str, keep_default_na=False)
tgt = pd.read_csv(sys.argv[2], dtype=str, keep_default_na=False).drop(columns=["name"])
cards = pre[pre.prefilter != "排除"].merge(tgt, on="tid", how="left")
cards.insert(0, "pid", cards.sid + "-" + cards.tid)
cards.to_csv(sys.argv[3], index=False, encoding="utf-8-sig")
print("证据卡", len(cards), "张；预筛排除", int((pre.prefilter == "排除").sum()), "对（留作漏配抽查）")
