# -*- coding: utf-8 -*-
"""从本地通达信（国信金太阳专业版）数据目录读取板块信息。

数据来源：
  T0002/hq_cache/tdxhy.cfg           股票 -> 通达信行业代码（Txxxx）
  T0002/hq_cache/tdxzs*.cfg          行业代码 -> 行业名称
  T0002/hq_cache/infoharbor_block.dat 概念板块成分（文本格式，可解析）

解析结果缓存为 boards.csv（随仓库提交，GitHub Actions 上可直接读取）。
本地通达信目录不存在时，静默降级为空映射。
"""
import os
import csv

DEFAULT_ROOT = r"D:\zd_gxzq"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "boards.csv")

# 过于宽泛或与炒作逻辑无关的板块，展示时跳过
NOISE = {
    "ST板块", "次新股", "含H股", "含B股", "含GDR", "含可转债", "融资融券",
    "转融券标的", "沪股通", "深股通", "预盈预增", "预亏预减", "昨日涨停",
    "昨日连板", "昨日触板", "破净股", "低价股", "高股息股", "通达信88",
}


def _read_gbk(path):
    with open(path, "rb") as f:
        return f.read().decode("gbk", "replace")


def parse_tdx(root=DEFAULT_ROOT):
    """返回 (industry_map, concept_map)。

    industry_map: code -> 行业名
    concept_map:  code -> [概念名1, 概念名2]（按板块成分数从小到大，越具体越靠前）
    """
    hq = os.path.join(root, "T0002", "hq_cache")
    if not os.path.isdir(hq):
        return {}, {}

    # 行业代码 -> 名称
    ind_names = {}
    for fn in ("tdxzs.cfg", "tdxzs2.cfg", "tdxzs3.cfg",
               "tdxzsbase.cfg", "tdxzsbase2.cfg"):
        p = os.path.join(hq, fn)
        if not os.path.exists(p):
            continue
        for ln in _read_gbk(p).splitlines():
            parts = ln.split("|")
            if len(parts) >= 6 and parts[5].strip().startswith("T"):
                ind_names.setdefault(parts[5].strip(), parts[0].strip())

    # 股票 -> 行业
    industry = {}
    p = os.path.join(hq, "tdxhy.cfg")
    if os.path.exists(p):
        for ln in _read_gbk(p).splitlines():
            parts = ln.split("|")
            if len(parts) >= 3 and len(parts[1].strip()) == 6:
                industry[parts[1].strip()] = ind_names.get(parts[2].strip(), "")

    # 概念板块成分
    concepts_raw = {}
    p = os.path.join(hq, "infoharbor_block.dat")
    if os.path.exists(p):
        cur, size = None, 9999
        for ln in _read_gbk(p).splitlines():
            if ln.startswith("#"):
                parts = ln[1:].split(",")
                raw = parts[0].strip()
                # 只保留 GN_ 概念板块，剔除 ZS_ 指数、FG_ 风格板块
                if not raw.startswith("GN_"):
                    cur = None
                    continue
                name = raw[3:].strip()
                try:
                    size = int(parts[1])
                except (IndexError, ValueError):
                    size = 9999
                cur = None if name in NOISE else name
                continue
            if not cur:
                continue
            for token in ln.split(","):
                token = token.strip()
                if "#" not in token:
                    continue
                code = token.split("#")[-1]
                if len(code) == 6 and code.isdigit():
                    concepts_raw.setdefault(code, []).append((size, cur))

    concept_map = {}
    for code, items in concepts_raw.items():
        items.sort()
        concept_map[code] = [b for _, b in items[:2]]
    return industry, concept_map


def save(industry, concept_map, path=CSV_PATH):
    codes = sorted(set(industry) | set(concept_map))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["code", "industry", "concept1", "concept2"])
        for code in codes:
            cc = concept_map.get(code, [])
            w.writerow([code, industry.get(code, ""),
                        cc[0] if cc else "", cc[1] if len(cc) > 1 else ""])


def load(path=CSV_PATH):
    """返回 (industry_map, concept_map)；文件不存在时返回空映射。"""
    industry, concept_map = {}, {}
    if not os.path.exists(path):
        return industry, concept_map
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            code = (r.get("code") or "").strip()
            if not code:
                continue
            industry[code] = (r.get("industry") or "").strip()
            cc = [x.strip() for x in (r.get("concept1"), r.get("concept2")) if x and x.strip()]
            concept_map[code] = cc
    return industry, concept_map


def ensure(path=CSV_PATH, root=DEFAULT_ROOT, log=print):
    """优先读缓存；缺失或本地有通达信数据时重新解析。返回 (industry, concepts)。"""
    if os.path.exists(path):
        return load(path)
    industry, concepts = parse_tdx(root)
    if industry or concepts:
        save(industry, concepts, path)
        log("板块映射：%d 只股票、%d 只含概念，已缓存 boards.csv"
            % (len(industry), len(concepts)))
    return industry, concepts


if __name__ == "__main__":
    ind, con = parse_tdx()
    save(ind, con)
    print("行业 %d，概念 %d -> %s" % (len(ind), len(con), CSV_PATH))
