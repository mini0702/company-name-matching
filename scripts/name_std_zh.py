"""中文企业名称标准化与简称匹配（通用模块）。

思路（依据：网上公开的中文企业名称匹配经验，以及英文流程 name_std.py 的对应关系）：
  1. 规范化：全角转半角、去空格和标点、统一括号
  2. 结构化：把全称拆成 地区 R + 字号 X + 行业 I + 组织形式 O（如 深圳市/万网博通/科技/有限公司 → RXIO），
     组织形式用后缀词库，地区用行政区划词库 + jieba 词性 ns，行业用行业词库，其余为字号
  3. 生成可能的简称：X、RX、R首字+X、R首字+I首字（上海汽车 → 上汽）、XI 等，可按需要增加规则
  4. 加权相似度：分别计算字号、行业、地区的相似度，按 8:1:1 加权（字号最重要），
     避免"麟云科技"与"云遥科技"因共享"科技"而高分
  5. "跳字"简称（上海汽车 → 上汽、京东方科技 → 京东方）：is_subsequence 判断简称的字是否按顺序出现在全称中

主要函数：
  normalize(s)        规范化
  parse(s)            → dict(R, X, I, O, branch, bracket, structure)
  core(s)             字号（核心名称）；字号为空时退回 行业，并标记
  abbreviations(p)    由 parse 结果生成可能的简称集合
  similarity(a, b)    Indel 相似度 = (len(a)+len(b)-编辑距离[插删 1、替换 2]) / (len(a)+len(b))
  weighted_score(a, b) 两个全称的加权相似度（字号 0.8、行业 0.1、地区 0.1）
  best_abbr_score(short, full)  简称与全称：取全称所有可能简称与该简称相似度的最高分
  is_subsequence(short, full)   简称的每个字是否按顺序出现在全称中

注意：分词和词性标注的准确率直接决定效果；行业词库、地区词库要按具体数据补充（新任务请研究者先核对）。
"""
import re
import unicodedata

import jieba
import jieba.posseg as pseg
from rapidfuzz.distance import Indel

jieba.setLogLevel(60)

# ---------- 词库（可按任务补充） ----------
# 组织形式（按长度从长到短匹配）
ORG_SUFFIX = sorted("""
集团股份有限公司 股份有限公司 有限责任公司 集团有限公司 控股有限公司 有限公司 股份公司 集团公司 总公司 公司
有限合伙 普通合伙 合伙企业 个人独资企业 企业 集团 控股 股份 工作室 事务所 研究院 研究所 合作社 商行 厂 店
""".split(), key=len, reverse=True)
# 分支机构：不是同一法人，需单独标记（是否归到总公司由研究者定口径）
BRANCH = ["分公司", "分行", "支行", "办事处", "营业部", "分厂", "分店", "分所", "代表处"]
# 省级行政区（全称与简称）及常见前缀；城市、区县由 jieba 词性 ns 识别，或按任务补充
PROVINCES = """
北京 天津 上海 重庆 河北 山西 辽宁 吉林 黑龙江 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 海南 四川 贵州 云南
陕西 甘肃 青海 台湾 内蒙古 广西 西藏 宁夏 新疆 香港 澳门 中国 中华 全国
""".split()
REGION_SUFFIX = ("省", "市", "自治区", "特别行政区", "自治州", "地区", "盟", "区", "县", "自治县", "旗", "镇", "乡")
# 常见城市（不带"市"时 jieba 不一定能识别；按任务补充）
CITIES = set("""
深圳 广州 杭州 南京 苏州 无锡 宁波 温州 成都 武汉 西安 长沙 郑州 合肥 济南 青岛 大连 沈阳 长春 哈尔滨 厦门 福州 泉州
南昌 昆明 贵阳 南宁 海口 三亚 石家庄 太原 呼和浩特 兰州 西宁 银川 乌鲁木齐 拉萨 东莞 佛山 珠海 中山 惠州 常州 南通
徐州 扬州 绍兴 嘉兴 金华 台州 烟台 潍坊 临沂 唐山 保定 洛阳 宜昌 襄阳 芜湖 绵阳 柳州 桂林 珠三角 长三角 浦东 滨海 中关村
""".split())
REGIONS = set(PROVINCES) | CITIES
# 行业 / 经营特点词（泛用词）。统计全库词频后人工补充
INDUSTRY = set("""
科技 技术 信息 信息技术 网络 网络科技 软件 电子 电子商务 通信 通讯 数据 数字 智能 智慧 云 互联网 计算机 系统 集成
医药 制药 生物 生物科技 生物技术 医疗 医疗器械 器械 健康 药业 基因
能源 新能源 电力 电气 光电 光伏 环保 环境 材料 新材料 化工 化学 机械 设备 装备 制造 工业 汽车 汽车零部件 半导体 集成电路
投资 管理 投资管理 资产管理 资本 基金 金融 证券 保险 银行 融资租赁 担保 小额贷款
贸易 商贸 国际贸易 进出口 实业 发展 建设 工程 建筑 房地产 置业 地产 物业 物流 供应链 运输 航空 航天
传媒 文化 文化传媒 广告 教育 培训 咨询 服务 餐饮 食品 农业 农牧 酒店 旅游 体育 娱乐 影视 游戏
""".split())


def normalize(s):
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKC", s)              # 全角 → 半角（（）→ ()、ＡＢＣ → ABC）
    s = s.replace("【", "(").replace("】", ")").replace("[", "(").replace("]", ")")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[·•、,，.。:：;；\"'“”‘’!！?？*＊/\\\-—_]", "", s)
    return s.upper()                                  # 名称中的英文字母统一大写


def _is_region(w, flag=None):
    """地区：省级名称或常见城市（可带 省/市/区/县 等后缀），或以行政区划后缀结尾且 jieba 标为地名的词。
    不单凭 jieba 的 ns 判断（"云遥"这类字号常被误标为地名）。"""
    if w in REGIONS:
        return True
    for suf in REGION_SUFFIX:
        if w.endswith(suf) and (w[: -len(suf)] in REGIONS or (flag == "ns" and len(w) - len(suf) >= 2)):
            return True
    return False


def _peel_region_prefix(w):
    """词开头的地区（jieba 把"中国移动""上海汽车"切成一个词时）：返回 (地区, 剩余)。剩余至少 2 个字才拆。"""
    for r in sorted(REGIONS, key=len, reverse=True):
        for cand in [r + suf for suf in REGION_SUFFIX] + [r]:
            if w.startswith(cand) and len(w) - len(cand) >= 2:
                return cand, w[len(cand):]
    return "", w


def _peel_industry_suffix(w):
    """词末尾的行业词（jieba 把"博通科技"切成一个词时）：返回 (剩余, 行业)。剩余至少 2 个字才拆。"""
    for ind in sorted(INDUSTRY, key=len, reverse=True):
        if w.endswith(ind) and len(w) - len(ind) >= 2:
            return w[: -len(ind)], ind
    return w, ""

def parse(s):
    """返回 {R, X, I, O, branch, bracket, structure}。括号内容（如（上海）（集团））单独放在 bracket，地区类的并入 R。"""
    s = normalize(s)
    out = {"R": "", "X": "", "I": "", "O": "", "branch": "", "bracket": "", "structure": ""}
    if not s:
        return out
    # 括号内容
    br = re.findall(r"\(([^()]*)\)", s)
    s = re.sub(r"\([^()]*\)", "", s)
    out["bracket"] = "|".join(br)
    # 分支机构
    for b in BRANCH:
        m = re.search(b + "$", s)
        if m:
            out["branch"] = s[m.start():]
            s = s[:m.start()]
            break
    # 分支机构前面通常是"总公司全称 + 地名"（中国工商银行股份有限公司北京分行）：先去掉末尾地名，作为分支所在地
    if out["branch"]:
        for r in sorted(REGIONS, key=len, reverse=True):
            for cand in [r + suf for suf in REGION_SUFFIX] + [r]:
                if s.endswith(cand) and len(s) > len(cand):
                    out["branch"] = cand + out["branch"]
                    s = s[: -len(cand)]
                    break
            else:
                continue
            break
    # 组织形式（可连续，如 集团有限公司）
    o = []
    changed = True
    while changed and s:
        changed = False
        for suf in ORG_SUFFIX:
            if s.endswith(suf) and len(s) > len(suf):
                o.insert(0, suf)
                s = s[: -len(suf)]
                changed = True
                break
    out["O"] = "".join(o)
    # 开头的地区（可连续，如 中国/上海、广东省/深圳市）：先查词库；词库没有的，用 jieba 第一个词的地名标记（须带行政区划后缀）
    R, I = [], []
    while True:
        r0, rest = _peel_region_prefix(s)
        if not r0:
            first = next(iter(pseg.cut(s)), None)
            if first is not None and _is_region(first.word, first.flag) and len(s) - len(first.word) >= 2:
                r0, rest = first.word, s[len(first.word):]
        if not r0:
            break
        R.append(r0)
        s = rest
    # 末尾的行业词（可连续，如 信息/科技）：在整串上从后往前剥，不依赖分词边界；剩余至少 2 个字
    while True:
        x0, ind = _peel_industry_suffix(s)
        if not ind:
            break
        I.insert(0, ind)
        s = x0
    X = s
    # 剩下的只有行业词时（上海汽车 → R=上海、I=汽车），字号为空
    if X in INDUSTRY:
        I.insert(0, X)
        X = ""
    struct = ("R" if R else "") + ("X" if X else "") + ("I" if I else "")
    for b in br:                                      # 括号里的地区并入 R（放在后面）
        if _is_region(b):
            R.append(b)
            continue
    out.update(R="".join(R), X=X, I="".join(I), structure=struct + ("O" if o else ""))
    return out


def core(s):
    p = parse(s)
    return (p["X"], 0) if p["X"] else (p["I"] or p["R"], 1)


def abbreviations(p):
    """由 parse 结果生成可能的简称（规则可按需要增加；资料中的例子：中国移动 → 中移动 = R首字+X，中国联通 = RX）。"""
    R, X, I = p["R"], p["X"], p["I"]
    c = {X, R + X, X + I, R + X + I}
    if R:
        c |= {R[0] + X, R[:2] + X}
        if I:
            c |= {R[0] + I[0], R[0] + I[:2]}         # 上海汽车 → 上汽
    if len(X) >= 3:
        c |= {X[:2], X[:3]}
    if I:
        c |= {X + I[:2]}
    return {a for a in c if len(a) >= 2}


def similarity(a, b):
    """资料中的相似度：(len(a)+len(b) - 编辑距离) / (len(a)+len(b))，插入、删除代价 1，替换代价 2，即 Indel 相似度。"""
    if not a and not b:
        return 1.0
    return Indel.normalized_similarity(a, b)


WEIGHTS = {"X": 0.8, "I": 0.1, "R": 0.1}


def weighted_score(a, b):
    """两个全称的加权相似度。字号缺失的一方退回整体相似度。
    地区：两边都有且不同，通常意味着不同公司（同名不同地），这里只按权重扣分，判断交给证据卡。"""
    pa, pb = parse(a), parse(b)
    if not pa["X"] or not pb["X"]:
        return similarity(normalize(a), normalize(b))
    score, wsum = 0.0, 0.0
    for k, w in WEIGHTS.items():
        if pa[k] and pb[k]:                           # 只比较两边都有的部分；一边缺失不扣分
            score += w * similarity(pa[k], pb[k])
            wsum += w
    return score / wsum


def is_subsequence(short, full):
    short, full = normalize(short), normalize(full)
    it = iter(full)
    return len(short) >= 2 and all(ch in it for ch in short)


def best_abbr_score(short, full):
    """简称 short 与全称 full 的匹配分：全称所有可能简称中与 short 相似度最高者；另返回该简称。"""
    p = parse(full)
    s = normalize(short)
    for suf in ORG_SUFFIX:                            # 简称末尾的组织形式（上汽集团、比亚迪股份）先去掉
        if s.endswith(suf) and len(s) - len(suf) >= 2:
            s = s[: -len(suf)]
            break
    best = max(((similarity(s, ab), ab) for ab in abbreviations(p)), default=(0.0, ""))
    return best


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for n in ["深圳市万网博通科技有限公司", "上海景栗信息科技有限公司", "中国移动有限责任公司", "云遥网络科技（上海）有限公司",
              "上海汽车集团股份有限公司", "京东方科技集团股份有限公司", "中国工商银行股份有限公司北京分行"]:
        p = parse(n)
        print(n, "→", {k: v for k, v in p.items() if v}, "| 简称候选:", sorted(abbreviations(p))[:8])
    print("麟云科技 vs 云遥科技 未加权", round(similarity("麟云科技", "云遥科技"), 2),
          "加权", round(weighted_score("深圳市麟云科技有限公司", "云遥科技有限公司"), 2))
    print("中移动 ↔ 中国移动有限责任公司", best_abbr_score("中移动", "中国移动有限责任公司"))
    print("上汽 ↔ 上海汽车集团股份有限公司", best_abbr_score("上汽", "上海汽车集团股份有限公司"),
          "跳字子序列", is_subsequence("上汽", "上海汽车集团股份有限公司"))
