"""公司名称标准化（通用模块，英文名称）。

匹配的两侧必须用同一套规则。
参照 Wasi & Flaaen (2015) stnd_compname 的思路：统一格式 → 拆分复合名称 → 统一常见缩写 → 分出后缀/地名/泛用词 → 核心名称。

主要函数：
  basic(s)          基础清洗：大写、去重音、& → AND、去标点、合并单字母缩写、去开头 THE
  split_compound(s) 拆分 d/b/a、f/k/a、on behalf of、括号等复合名称，返回 [(名称, 角色), ...]
  canon(s)          统一常见缩写和单复数（如 TECHNOLOGIES/TECH → TECH，ASSOCIATION/ASSN → ASSN）
  parts(s, generic) 返回 (标准化全称, 去后缀名称, 核心名称)
"""
import re
import unicodedata

# ---------- 1. 基础清洗 ----------
def basic(s):
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()  # 去重音：É → E
    s = s.upper()
    s = re.sub(r"[’'`´]", "", s)                       # O'REILLY → OREILLY
    s = s.replace("&", " AND ").replace("+", " AND ")
    s = re.sub(r"\.(COM|NET|ORG|IO|CO)\b", r" DOT\1 ", s)  # AMAZON.COM：域名后缀先标记，后面当后缀去掉
    s = re.sub(r"[^A-Z0-9/() ]", " ", s)                # 其余标点变空格（保留 / 和括号供拆分用）
    s = re.sub(r"\s+", " ", s).strip()
    # 单字母序列合并成缩写：I B M → IBM；A T AND T → AT AND T 不处理
    s = re.sub(r"\b(?:[A-Z] ){1,}[A-Z]\b", lambda m: m.group(0).replace(" ", ""), s)
    s = re.sub(r"^THE ", "", s)
    return s


# ---------- 2. 拆分复合名称 ----------
# 角色：main = 主名称；alias = d/b/a 等指向同一实体的另一个名称；represented = on behalf of 后面被代表的客户；
#       filer = on behalf of 前面的提交方（游说公司），不参与匹配
_ALIAS = r"\b(?:D ?/ ?B ?/ ?A|DBA|D B A|F ?/ ?K ?/ ?A|FKA|F K A|A ?/ ?K ?/ ?A|AKA|N ?/ ?K ?/ ?A|NKA|FORMERLY(?: KNOWN AS)?|PREVIOUSLY(?: KNOWN AS)?|NOW KNOWN AS|ALSO KNOWN AS|DOING BUSINESS AS|TRADING AS|T ?/ ?A)\b"
_OBO = r"\b(?:ON BEHALF OF|O ?/ ?B ?/ ?O|OBO|C ?/ ?O|FOR THE BENEFIT OF)\b"


_RELATED = r"\b(?:PARENT|SUBSIDIARY|SUBSIDIARIES|AFFILIATE|AFFILIATES|DIVISION OF|UNIT OF|OWNED BY|MEMBER OF)\b"


def split_compound(raw, obo=True):
    """返回 [(清洗后的名称, 角色)]。先在原始大写串上找关键词，再分别清洗。
    obo=True 时识别"代他人申报"（只用于游说客户名称；样本公司名称不是申报，传 obo=False）。"""
    s = basic(raw)
    if not s:
        return []
    out = []
    # on behalf of：前面是提交方，后面是被代表的客户（两边都要有内容，避免把公司名 OBO INC 拆开）
    m = re.search(_OBO, s) if obo else None
    if m and len(s[:m.start()].strip(" (")) >= 2 and len(s[m.end():].strip(" )")) >= 2:
        out.append((s[:m.start()], "filer"))
        rest = [(s[m.end():], "represented")]
    else:
        rest = [(s, "main")]
    for t, role in rest:
        # 括号内容单独作为一个名称（常见：X (D/B/A Y)、X (FORMERLY Y)、X (ITI)）
        inner = re.findall(r"\(([^()]*)\)", t)
        outer = re.sub(r"\([^()]*\)", " ", t)
        pieces = [(outer, role)]
        for x in inner:
            x = x.strip()
            if obo and re.match(r"^(?:FOR|ON BEHALF OF)\b", x):   # X (FOR Y)：Y 是被代表的客户，X 是提交方
                pieces = [(outer, "filer")] + [p for p in pieces[1:]]
                pieces.append((re.sub(r"^(?:FOR|ON BEHALF OF)\s+", "", x), "represented"))
            elif re.search(_RELATED, x):                         # X (PARENT OF ...)：关联方，不是同一实体
                pieces.append((x, "related"))
            else:
                pieces.append((x, "alias"))
        for p, r in pieces:
            # d/b/a、f/k/a 等前后都是同一实体的名称
            segs = re.split(_ALIAS, p)
            for k, seg in enumerate(segs):
                out.append((seg, r if k == 0 else "alias"))
    res = []
    for t, r in out:
        t = re.sub(r"[()/]", " ", t)
        t = re.sub(r"\s+", " ", t).strip(" -,")
        t = re.sub(r"^(?:THE|AND|OF|FOR) ", "", t).strip()
        # 拆出来的别名如果只是后缀或地域词（如 (US)、(INC)），丢掉
        if r != "main" and all(w in LEGAL or w in GEO_TRAIL or w in {"AND", "OF", "THE"} for w in canon(t).split()):
            continue
        if len(t) >= 2:
            res.append((t, r))
    # 去重，保留第一次出现的角色
    seen, final = set(), []
    for t, r in res:
        if t not in seen:
            seen.add(t)
            final.append((t, r))
    return final


# ---------- 3. 统一常见缩写和单复数 ----------
# 左边是各种写法，右边是统一后的写法。包括 OpenSecrets(CRP) 常用的缩写，如 ASSN、FEDN、AMER、NATL。
_CANON = {
    "INCORPORATED": "INC", "CORPORATION": "CORP", "CORPORATIONS": "CORP", "COMPANY": "CO", "COMPANIES": "CO",
    "LIMITED": "LTD", "HOLDINGS": "HLDG", "HOLDING": "HLDG", "HLDGS": "HLDG", "HLDGSCO": "HLDG",
    "TECHNOLOGIES": "TECH", "TECHNOLOGY": "TECH", "TECHNOLOGICAL": "TECH", "TECHS": "TECH", "TECHNOL": "TECH",
    "PHARMACEUTICALS": "PHARMA", "PHARMACEUTICAL": "PHARMA", "PHARMS": "PHARMA", "PHARM": "PHARMA",
    "LABORATORIES": "LAB", "LABORATORY": "LAB", "LABS": "LAB",
    "THERAPEUTICS": "THERAPEUTIC", "SYSTEMS": "SYSTEM", "SYS": "SYSTEM",
    "SOLUTIONS": "SOLUTION", "SERVICES": "SVC", "SERVICE": "SVC", "SVCS": "SVC", "SRVCS": "SVC",
    "COMMUNICATIONS": "COMM", "COMMUNICATION": "COMM", "COMMS": "COMM", "COMMUN": "COMM",
    "NETWORKS": "NETWORK", "NETWRK": "NETWORK", "INTERNATIONAL": "INTL", "INTERNATL": "INTL", "INT": "INTL",
    "NATIONAL": "NATL", "AMERICAN": "AMER", "AMERICA": "AMER", "AMERICAS": "AMER",
    "ASSOCIATION": "ASSN", "ASSOCIATIONS": "ASSN", "ASSOC": "ASSN", "ASSNS": "ASSN",
    "ASSOCIATES": "ASSOCS", "FEDERATION": "FEDN", "INSTITUTE": "INST", "UNIVERSITY": "UNIV",
    "MANAGEMENT": "MGMT", "MANUFACTURING": "MFG", "MANUFACTURERS": "MFRS", "MANUFACTURER": "MFRS",
    "INDUSTRIES": "INDUSTRY", "INDS": "INDUSTRY", "IND": "INDUSTRY", "ENTERPRISES": "ENTERPRISE",
    "PRODUCTS": "PRODUCT", "PRODS": "PRODUCT", "ENTERTAINMENT": "ENTMT", "ENGINEERING": "ENGR",
    "DEVELOPMENT": "DEVEL", "INVESTMENTS": "INVESTMENT", "PARTNERS": "PARTNER", "PTNRS": "PARTNER",
    "VENTURES": "VENTURE", "BROTHERS": "BROS", "GROUP": "GRP", "MEDICAL": "MED", "BIOSCIENCES": "BIOSCIENCE",
    "SCIENCES": "SCIENCE", "SCI": "SCIENCE", "ENERGIES": "ENERGY", "INNOVATIONS": "INNOVATION",
    "DIAGNOSTICS": "DIAGNOSTIC", "DEVICES": "DEVICE", "ELECTRONICS": "ELECTRONIC", "INSTRUMENTS": "INSTRUMENT",
    "RESOURCES": "RESOURCE", "SEMICONDUCTORS": "SEMICONDUCTOR", "MATERIALS": "MATERIAL",
    "WORKS": "WORK", "STUDIOS": "STUDIO", "GAMES": "GAME", "MOTORS": "MOTOR", "FOODS": "FOOD",
    "PLATFORMS": "PLATFORM", "ROBOTICS": "ROBOTIC", "GENOMICS": "GENOMIC", "ANALYTICS": "ANALYTIC",
    "DOTCOM": "COM", "DOTNET": "NET", "DOTORG": "ORG", "DOTIO": "IO", "DOTCO": "CO",
    "AND": "AND", "N": "AND",
}


def canon(s):
    return " ".join(_CANON.get(w, w) for w in s.split())


# ---------- 4. 后缀与地名 ----------
LEGAL = {"INC", "CORP", "CO", "LLC", "L L C", "LP", "LLP", "LTD", "PLC", "PC", "PLLC", "PA", "SA", "AG", "NV",
         "BV", "GMBH", "SRL", "SPA", "AB", "AS", "OY", "KK", "PTY", "PTE", "SE", "LLLP", "HLDG", "COM", "NET",
         "ORG", "IO", "THE"}
GEO_TRAIL = {"US", "USA", "U S", "AMER", "NORTH AMER", "NA", "INTL", "GLOBAL", "WORLDWIDE"}


def strip_legal(s):
    """去掉末尾的法律后缀（可连续多个，如 HLDG CORP）和末尾的地域词（如 USA）。"""
    w = s.split()
    while len(w) > 1 and (w[-1] in LEGAL or w[-1] in GEO_TRAIL):
        w.pop()
    # 中间的 INC / CORP（如 "XYZ INC OF DELAWARE"）只在后面还有词时去掉 "OF ..." 部分
    s2 = " ".join(w)
    s2 = re.sub(r" (?:INC|CORP|LLC|LTD|CO) OF [A-Z ]+$", "", s2)
    return s2


def parts(name, generic):
    """name 为 split_compound 输出的单个名称；generic 为泛用词集合。
    返回 (std 标准化全称, nolegal 去后缀名称, core 核心名称, core_is_fallback)。"""
    std = canon(name)
    nolegal = strip_legal(std)
    toks = [t for t in nolegal.split() if t not in generic and t not in LEGAL and t not in {"AND", "OF", "THE", "FOR"}]
    if toks:
        return std, nolegal, " ".join(toks), 0
    # 全是泛用词（如 DIGITAL SIGNAL）：核心名称退回去后缀名称，并标记
    return std, nolegal, nolegal, 1


# ---------- 5. 默认泛用词表（从 Crunchbase 140 万公司名的高频词中人工挑选，约 230 个；新任务应由研究者核对或补充） ----------
GENERIC = {
    "机构类型": """GRP SOLUTION SVC SYSTEM VENTURE MGMT PARTNER CONSULTING ASSOCS BUSINESS AGENCY INDUSTRY ENTERPRISE
        CENTER CENTRE INST ASSN FEDN UNIV COLLEGE SCHOOL FOUNDATION ACADEMY CLUB SOCIETY COUNCIL ALLIANCE COALITION UNION
        TRUST FIRM ADVISORS ADVISORY CONSULTANTS CONSULTANCY STRATEGIES STRATEGIC PRODUCTIONS PRODUCTION PRODUCT PROJECT
        TEAM HUB PLATFORM STUDIO WORK CONCEPTS INDUSTRIAL CORPORATE PROFESSIONAL COMMERCIAL GROUPE GRUPO ASSOCIATED""",
    "行业词": """TECH SOFTWARE MEDIA DIGITAL DESIGN DESIGNS LAB NETWORK MARKETING HEALTH HEALTHCARE CARE MED MEDICINE PHARMA
        THERAPEUTIC THERAPY BIO BIOTECH BIOSCIENCE SCIENCE DIAGNOSTIC DEVICE GENOMIC ROBOTIC ANALYTIC DATA INFORMATION
        INFO IT COMPUTER COMPUTING ELECTRONIC ENERGY POWER SOLAR OIL GAS CAPITAL INVESTMENT INVEST FUND FUNDING
        FINANCIAL FINANCE BANK CREDIT INSURANCE MORTGAGE EQUITY WEALTH ASSET SECURITIES REALTY ESTATE PROPERTY
        PROPERTIES CONSTRUCTION BUILDING BUILDERS ENGR MFG MFRS MATERIAL INSTRUMENT EQUIPMENT AUTOMATION PRECISION COMM
        TELECOM WIRELESS MOBILE APP APPS ONLINE INTERNET WEB CLOUD SECURITY INTERACTIVE ENTMT GAME MUSIC VIDEO
        PUBLISHING ADVERTISING FOOD LOGISTICS TRANSPORTATION TRANSPORT AVIATION AUTOMOTIVE RETAIL COMMERCE TRADING
        EDUCATION LEARNING TRAINING RESEARCH INNOVATION INNOVATIVE ADVANCED APPLIED INTEGRATED ENVIRONMENTAL RESOURCE
        SUPPLY DISTRIBUTION STAFFING RECRUITMENT IMAGING SEMICONDUCTOR DEVEL INFOTECH TECHNICAL SOFT BIOPHARMA
        BIOMEDICAL BIOLOGICS SURGICAL CLINICAL VACCINES ONCOLOGY NEUROSCIENCE OPTICS PHOTONICS PHARMACY HOSPITAL CLINIC
        DENTAL ANIMAL AGRICULTURE AGRITECH FINTECH INSURTECH EDTECH MEDTECH CLEANTECH NANOTECH BIOSYSTEM""",
    "地域词": """AMER NATL INTL GLOBAL WORLDWIDE US USA NA JAPAN INDIA CHINA UK CANADA ASIA EUROPE AFRICA AUSTRALIA KOREA
        GERMANY FRANCE ISRAEL SINGAPORE""",
    "虚词": """AND OF FOR THE IN ON BY TO AT A AN DE LA DU DES""",
}
GENERIC_SET = {w for v in GENERIC.values() for w in v.split()}


# ---------- 6. 名称表 → 名称变体表（供 candidates.py 使用） ----------
def variants(df, id_col, name_col, obo=False, generic=GENERIC_SET):
    """每个原始名称拆分、标准化，返回每个变体一行：id、name（原始名称）、part、role、nolegal、core、core_fallback。
    样本一侧 obo=False（公司名不是代人申报）；目标一侧（如游说客户）obo=True，filer / related 角色不参与匹配。"""
    rows = []
    for i, raw in zip(df[id_col], df[name_col]):
        for part, role in split_compound(raw, obo=obo):
            if role in ("filer", "related"):
                continue
            _, nolegal, core, fb = parts(part, generic)
            rows.append({id_col: i, "name": raw, "part": part, "role": role, "nolegal": nolegal, "core": core, "core_fallback": fb})
    import pandas as pd
    return pd.DataFrame(rows).drop_duplicates([id_col, "nolegal"])


if __name__ == "__main__":
    import argparse
    import pandas as pd
    ap = argparse.ArgumentParser(description="名称表 → 名称变体表")
    ap.add_argument("inp"); ap.add_argument("out")
    ap.add_argument("--id", required=True, help="编号列：样本一侧通常命名为 sid，目标一侧为 tid")
    ap.add_argument("--name", default="name", help="名称列")
    ap.add_argument("--obo", action="store_true", help="目标一侧是申报类数据（有 on behalf of）时加上")
    a = ap.parse_args()
    d = pd.read_csv(a.inp, dtype=str, keep_default_na=False)
    v = variants(d, a.id, a.name, obo=a.obo)
    v.to_csv(a.out, index=False, encoding="utf-8-sig")
    print("名称", len(d), "→ 变体", len(v), "；核心名称全是泛用词的", int(v.core_fallback.sum()))
