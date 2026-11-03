# -*- coding: utf-8 -*-
"""策略实现（对应金太阳公式 J1-J3 / W1-W3 / P1-P3）。实盘注册表 STRATEGIES
现含 7 套：J1-J3 / A3（竞价）、W1 / W3 / C1（尾盘）；盘后时段 2026-11-03 整体
停用（P1 待替换策略），W2 / P2 / P3 同日回测下线——四个函数均保留，见各自注释。
输入：今日快照 t（含 open/high/low/price/volume/amount/turnover/vol_ratio/circ_mv）
      历史日线 h（不含今日，按日期升序，volume 单位手，amount 单位元）
"""
import numpy as np


def ztl(code, name):
    if code.startswith(("300", "301", "688", "689")):
        return 1.20
    if code.startswith(("4", "8", "920")):
        return 1.30
    if "ST" in (name or "").upper():
        return 1.05
    return 1.10


def _ma(arr, n, today=None):
    if len(arr) < (n if today is None else n - 1):
        return np.nan
    if today is None:
        return float(np.mean(arr[-n:]))
    return float(np.mean(np.concatenate([arr[-(n - 1):], [today]])))


def _ema(arr, n):
    a = np.asarray(arr, dtype=float)
    out = np.empty_like(a)
    k = 2.0 / (n + 1)
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = a[i] * k + out[i - 1] * (1 - k)
    return out


def _base(t, h):
    pc = float(h["close"][-1])
    return {
        "pc": pc, "o": float(t["open"]), "h": float(t["high"]), "l": float(t["low"]),
        "c": float(t["price"]), "v": float(t["volume"]), "amt": float(t["amount"]),
        "hs": float(t.get("turnover") or 0), "lb": float(t.get("vol_ratio") or 0),
        "mv": float(t.get("circ_mv") or 0), "z": ztl(t["code"], t.get("name", "")),
    }


# ---------------------------------------------------------------- 竞价 9:25
def ev_a3(t, h):
    """A3 竞价·N字反包：前日涨停 -> 昨日收阴洗盘未破中位 -> 今日竞价高开反包。

    参数（公式默认）：N1=昨日成交额≥5亿 N2/N3=流通市值50-800亿
    N4/N5=高开0.5%-6% N6=昨日量比≥2.5（0=不启用）
    """
    if len(h["close"]) < 23:
        return None
    b = _base(t, h)
    prev, o = b["pc"], b["o"]
    if prev <= 0 or o <= 0:
        return None
    c2, c3 = float(h["close"][-2]), float(h["close"][-3])
    o1, c1 = float(h["open"][-1]), float(h["close"][-1])
    lo1 = float(h["low"][-1])
    mid = (float(h["open"][-2]) + c2) / 2.0
    amt1 = float(h["amount"][-1]) / 1e8
    fmv = b["mv"] / 1e8
    gap = (o / prev - 1) * 100
    vr1 = float(h["volume"][-1]) / float(np.mean(h["volume"][-20:]))
    if c2 < c3 * b["z"] * 0.995:               # 前日涨停
        return None
    if not (o1 > c1 and lo1 >= mid):           # 昨日收阴洗盘且最低不破前日中位
        return None
    if amt1 < 5 or not (50 <= fmv <= 800):     # 昨日成交额 / 流通市值
        return None
    if not (0.5 <= gap <= 6):                  # 今日竞价高开区间
        return None
    if vr1 < 2.5:                              # 昨日量比（历史口径，非实时量比列）
        return None
    return {"gap": gap, "amt1": amt1, "mv": fmv, "price": o}


def ev_j1(t, h):
    b = _base(t, h)
    pc, o = b["pc"], b["o"]
    if pc <= 0 or o <= 0:
        return None
    gap = (o / pc - 1) * 100
    lb, hs = b["lb"], b["hs"]
    ma10p = _ma(h["close"], 10)
    if np.isnan(ma10p):
        return None
    if not (1 < gap < 4 and 2 < lb < 8 and 1 < hs < 12):
        return None
    if o <= ma10p or o >= pc * b["z"] - 0.005:
        return None
    return {"gap": gap, "lb": lb, "hs": hs, "price": o}


def ev_j2(t, h):
    if len(h["close"]) < 25:
        return None
    b = _base(t, h)
    prev, prev2 = float(h["close"][-1]), float(h["close"][-2])
    zpt1 = prev2 * b["z"]
    zb = h["high"][-1] >= zpt1 * 0.995 and zpt1 * 0.995 > prev
    amt1 = float(h["amount"][-1]) / 1e8
    fmv = b["mv"] / 1e8
    gap = (b["o"] / prev - 1) * 100 if prev > 0 else -99
    vr1 = float(h["volume"][-1]) / float(np.mean(h["volume"][-20:]))
    ma10p = _ma(h["close"], 10)
    dev10 = (prev - ma10p) / ma10p * 100
    if not (zb and amt1 >= 8 and 50 <= fmv <= 800 and 3 <= gap <= 6
            and vr1 >= 1.5 and dev10 < 20):
        return None
    return {"gap": gap, "lb": vr1, "amt1": amt1, "mv": fmv, "price": b["o"]}


def ev_j3(t, h):
    if len(h["close"]) < 3:
        return None
    b = _base(t, h)
    pc, o = b["pc"], b["o"]
    if pc <= 0 or o <= 0:
        return None
    gap = (o / pc - 1) * 100
    amt1 = float(h["amount"][-1]) / 1e8
    prev2 = float(h["close"][-2])
    if not (-5 <= gap <= -2 and 0.3 <= amt1 <= 3 and 3 <= pc <= 20):
        return None
    if o <= pc * (2 - b["z"]) * 1.001:          # 一字跌停
        return None
    if prev2 <= 0 or pc / prev2 >= b["z"] * 0.995:
        return None
    return {"gap": gap, "amt1": amt1, "price": o}


# ---------------------------------------------------------------- 尾盘 14:40
def ev_w1(t, h):
    if len(h["close"]) < 10:
        return None
    b = _base(t, h)
    if b["pc"] <= 0 or b["c"] <= 0 or b["v"] <= 0:
        return None
    zf = (b["c"] / b["pc"] - 1) * 100
    lb = b["lb"]
    if lb <= 0:
        minutes = float(t.get("minutes") or 240)
        ma5v = float(np.mean(h["volume"][-5:]))
        lb = b["v"] / (ma5v / 240.0 * minutes) if ma5v > 0 else 0
    hs = b["hs"]
    ma5 = _ma(h["close"], 5, b["c"])
    ma10 = _ma(h["close"], 10, b["c"])
    avgp = b["amt"] / (b["v"] * 100) if b["v"] > 0 else 0
    if not (2 < zf < 5 and lb > 1 and 3 < hs < 18):
        return None
    if b["c"] <= ma5 or b["c"] <= ma10 or b["c"] < avgp:
        return None
    return {"gap": zf, "lb": lb, "hs": hs, "avgp": avgp, "price": b["c"]}


# 【已下线 2026-11-03】回测（九套同口径，70/30 样本内外）：W2 样本内 +0.21%
# 但样本外均收益 -1.06%（同期基准 -0.27%），样本外塌方、过拟合嫌疑，停用。
# 保留实现备查，恢复前需重测并通过样本外检验。
def ev_w2(t, h):
    if len(h["close"]) < 21:
        return None
    b = _base(t, h)
    zf = (b["c"] / b["pc"] - 1) * 100 if b["pc"] > 0 else -99
    hh20 = max(float(np.max(h["high"][-19:])), b["h"])
    cross = b["c"] > float(h["high"][-1]) and float(h["close"][-1]) < float(h["high"][-2])
    vup = b["v"] > float(h["volume"][-1]) * 1.1
    fmv = b["mv"] / 1e8
    if not (b["c"] > hh20 * 0.98 and cross and vup and 30 <= fmv <= 300
            and 1 < zf < 7):
        return None
    if b["c"] >= b["pc"] * b["z"] - 0.005:
        return None
    return {"gap": zf, "lb": b["v"] / float(h["volume"][-1]) if h["volume"][-1] else 0,
            "mv": fmv, "price": b["c"]}


def _ev_first_board(t, h, lo, hi):
    """首板打板判定（W3/C1 共用）：流通市值限定在 [lo, hi] 亿。"""
    if len(h["close"]) < 2:
        return None
    b = _base(t, h)
    ztp = b["pc"] * b["z"] - 0.005
    zt = b["c"] >= ztp
    prev_zt = float(h["close"][-1]) >= float(h["close"][-2]) * b["z"] - 0.005
    kegou = b["h"] > b["l"] * 1.001
    amt = b["amt"] / 1e8
    fmv = b["mv"] / 1e8
    if not (zt and not prev_zt and kegou and amt >= 3 and lo <= fmv <= hi):
        return None
    return {"gap": (b["c"] / b["pc"] - 1) * 100 if b["pc"] else 0,
            "mv": fmv, "amt": amt, "price": b["c"]}


def ev_w3(t, h):
    """W3 尾盘·首板打板：流通市值 200-400 亿段（与 C1 的 400-800 亿互补不重叠）。"""
    return _ev_first_board(t, h, 200, 400)


def ev_c1(t, h):
    """C1 尾盘·首板确认：判定与 W3 同源，市值段 400-800 亿（独立票池）。"""
    return _ev_first_board(t, h, 400, 800)


# ---------------------------------------------------------------- 盘后 15:40
# 【已停用 2026-11-03】P1 随盘后时段整体停用，待替换策略（样本外 -0.90%
# 截断后 / +0.22% 不截断，部署形态无正期望优势）。实现保留备查/恢复。
def ev_p1(t, h):
    if len(h["close"]) < 35:
        return None
    b = _base(t, h)
    if b["amt"] < 1e8:
        return None
    closes = np.concatenate([h["close"], [b["c"]]])
    dif = _ema(closes, 12) - _ema(closes, 26)
    dea = _ema(dif, 9)
    cross = dif[-1] > dea[-1] and dea[-2] >= dif[-2]
    cpos = (b["c"] - b["l"]) / (b["h"] - b["l"]) * 100 if b["h"] > b["l"] else np.nan
    amt5 = float(np.mean(np.concatenate([h["amount"][-4:], [b["amt"]]]))) / 1e8
    if not (cross and b["h"] > b["l"] and cpos >= 55 and amt5 >= 15):
        return None
    return {"gap": (b["c"] / b["pc"] - 1) * 100 if b["pc"] else 0,
            "cpos": cpos, "amt5": amt5, "price": b["c"]}


# 【已下线 2026-11-03】回测（九套同口径）：P2 样本外均收益 -3.36%（同期基准
# -0.89%），全期中位数 -2.34%，负期望明确，停用。保留实现备查，恢复前需重测。
def ev_p2(t, h):
    if len(h["close"]) < 61:
        return None
    b = _base(t, h)
    c = b["c"]
    ma5 = _ma(h["close"], 5, c)
    ma10 = _ma(h["close"], 10, c)
    ma20 = _ma(h["close"], 20, c)
    ma60 = _ma(h["close"], 60, c)
    pma5 = _ma(h["close"], 5)
    pma10 = _ma(h["close"], 10)
    pma20 = _ma(h["close"], 20)
    zf = (c / b["pc"] - 1) * 100 if b["pc"] else -99
    ma5v = float(np.mean(np.concatenate([h["volume"][-4:], [b["v"]]])))
    vup = b["v"] >= ma5v * 1.5
    newh = c > float(np.max(h["high"][-20:]))
    fmv = b["mv"] / 1e8
    dup = ma5 > ma10 > ma20 > ma60 and ma5 > pma5 and ma10 > pma10 and ma20 > pma20
    if not (dup and 3 <= zf <= 9 and vup and newh and 30 <= fmv <= 500):
        return None
    return {"gap": zf, "mv": fmv, "price": c}


# 【已下线 2026-11-03】回测（九套同口径）：P3 样本外均收益 -2.00%（同期基准
# -0.89%），负期望，停用。保留实现备查，恢复前需重测。
def ev_p3(t, h):
    if len(h["close"]) < 61:
        return None
    b = _base(t, h)
    c = b["c"]
    ma5 = _ma(h["close"], 5, c)
    ma10 = _ma(h["close"], 10, c)
    ma20 = _ma(h["close"], 20, c)
    ma60 = _ma(h["close"], 60, c)
    hmax = max(ma5, ma10, ma20, ma60)
    lmin = min(ma5, ma10, ma20, ma60)
    spread = hmax / lmin if lmin > 0 else 99
    zf = (c / b["pc"] - 1) * 100 if b["pc"] else -99
    ma5v = float(np.mean(np.concatenate([h["volume"][-4:], [b["v"]]])))
    vup = b["v"] >= ma5v * 1.5
    fmv = b["mv"] / 1e8
    if not (spread < 1.03 and 2 <= zf <= 8 and vup and c > b["o"] and c > hmax
            and 20 <= fmv <= 300):
        return None
    return {"gap": zf, "spread": (spread - 1) * 100, "mv": fmv, "price": c}


# ------------------------------------------------- F13 底部首阳D8（独立函数，未上注册表）
# 【2026-11-03 回测口径】5011 只 / 2024-06-10~2026-10-30 / 信号日收盘确认→次日开买→hold10 收盘卖。
#   全信号口径：全期 n=1548 胜53.9% 均+2.62% | 内 n=504 -0.85% | 外 n=1044 +4.30%（同期全市场基准 外 -0.89%）。
#   部署口径（每日按流通市值升序截 3）：内 +1.01% / 外 +2.48%，样本外唯一为正的 F13 部署形态。
# 【择时开关回测结论：证据不足，不启用（enabled 默认 False）】
#   ① 指数趋势类全部反向：上证/沪深300 C>MA20、C>MA60、MA20>MA60、连续5/10日、20日动量>0
#      的开关版本样本外均大幅劣于基线（F13 是超跌首阳，天然在指数弱势段触发，趋势开关方向相反）。
#   ② 唯一双优候选"全市场站上MA20占比>0.40"（开58%天数）：全信号口径内 -0.85%→+0.84%、
#      外 +4.30%→+4.65%；但 bootstrap 90%CI [-1.15, +2.37] 跨零不显著；且部署口径（每日截3）
#      样本外反而 +2.48%→+1.75%（CI [-7.74, +3.17]），两口径样本外结论相反。
#   ③ 波动/回撤类（20日振幅、距60日高点回撤<5/8/10%）样本外无稳定增益。
#   按判定规则（样本外不劣于基线才启用）→ 不启用；参数与逻辑保留在 F13_TIMING/gate 供日后重测，
#   启用方式：F13_TIMING["enabled"]=True（手动总开关，随时可整体回到纯 F13）。
F13_TIMING = {
    "enabled": False,        # 手动总开关：False=纯 F13（不择时）
    "kind": "breadth_ma20",  # breadth_ma20=全市场站上MA20占比 | index_ma20=指数收盘>MA20
    "th": 0.40,              # breadth_ma20 阈值（回测扫描 0.40~0.55 平台，0.40 为下沿）
    "index_code": "000001",  # index_ma20 用指数（配合 index_mkt）
    "index_mkt": "sh",       # sh000001 上证；可换 sh000300 沪深300
    "on_missing": "pass",    # 数据缺失默认放行：回测显示择时无显著增益、阻断无价值，
                             # 放行可避免因指数文件/宽度数据问题静默空仓错失信号
}


def f13_breadth_ma20(closes):
    """全市场站上MA20占比（gate 的 breadth_ma20 数据源）。
    closes：每只股票含今日收盘的一维序列（长度>=20），可传生成器。
    少于 20 根或含非正值的股票跳过；全市场无有效样本时返回 None（gate 按缺失处理）。"""
    up = tot = 0
    for a in closes:
        a = np.asarray(a, dtype=float)
        if len(a) < 20:
            continue
        a = a[-20:]
        if a[-1] <= 0 or not np.isfinite(a).all():
            continue
        tot += 1
        up += a[-1] > a.mean()
    return up / tot if tot else None


def gate_f13_timing(breadth=None, index_close=None, timing=None):
    """F13 择时闸门。返回 (放行bool, 原因str)，原因 ∈ off/gate/missing。
    因果性：所有判定只用信号日（盘后收盘）当时可得的数据，无未来函数。
      - breadth：f13_breadth_ma20() 的当日全市场统计（盘后扫描全市场时顺带可算）
      - index_close：指数含今日收盘序列（>=20 根；可由 tdx.read_day(index_code, mkt=index_mkt) 取）
    缺失/异常时按 F13_TIMING["on_missing"]：pass=放行（默认，理由见上）/ block=阻断。"""
    T = dict(F13_TIMING, **(timing or {}))
    if not T["enabled"]:
        return True, "off"
    try:
        if T["kind"] == "breadth_ma20":
            if breadth is None:
                raise ValueError("breadth 数据缺失")
            ok = breadth > T["th"]
        elif T["kind"] == "index_ma20":
            a = np.asarray(index_close, dtype=float)
            if len(a) < 20 or a[-1] <= 0:
                raise ValueError("指数数据缺失")
            ok = a[-1] > a[-20:].mean()
        else:
            raise ValueError("未知 kind: %s" % T["kind"])
    except Exception:
        return T["on_missing"] != "block", "missing"
    return bool(ok), "gate"


def ev_f13(t, h):
    """F13 底部首阳D8：60日深跌池内缩量阴跌后的放量首阳（超跌反抽）。
    回测口径直译，独立函数未上注册表；实盘部署时需先重测样本外。
    条件（dk：30/68 开头 0.20，4/8/92 开头 0.30，其余 0.10）：
      非ST · 近60日（含今日）无单日跌幅>dk+1% · 今收<60日最高收×0.7 · 今收<MA60
      · 今量>30日最高量×0.8 · 今量>5日均量 且 昨量>昨日5日均量
      · 20日涨幅<15% · 收阳（今收>昨收）且涨幅>2% · 成交额>5000万 · 上市满250根"""
    if len(h["close"]) < 250:
        return None
    b = _base(t, h)
    c, v, pc = b["c"], b["v"], b["pc"]
    if c <= 0 or pc <= 0 or v <= 0:
        return None
    nm = str(t.get("name", "")).upper()
    if "ST" in nm or "*" in nm:
        return None
    code = t["code"]
    if code[:2] in ("30", "68"):
        dk = 0.20
    elif code[:1] in ("4", "8") or code[:2] == "92":
        dk = 0.30
    else:
        dk = 0.10
    hc = np.asarray(h["close"], dtype=float)
    hv = np.asarray(h["volume"], dtype=float)
    rc = np.concatenate([hc[-59:] / hc[-60:-1] - 1, [c / pc - 1]])
    if (rc < -dk - 0.01).any():
        return None
    hhc60 = max(hc[-59:].max(), c)
    ma60 = (hc[-59:].sum() + c) / 60.0
    hhv30 = max(hv[-29:].max(), v)
    vma5 = (hv[-4:].sum() + v) / 5.0
    if not (c < hhc60 * 0.7 and c < ma60):
        return None
    if not (v > hhv30 * 0.8 and v > vma5 and hv[-1] > hv[-5:].mean()):
        return None
    if not (c / hc[-20] - 1 < 0.15 and c > pc and (c / pc - 1) * 100 > 2.0):
        return None
    if b["amt"] <= 5e7:
        return None
    return {"gap": (c / pc - 1) * 100, "mv": b["mv"] / 1e8, "price": c}


STRATEGIES = {
    "A3": dict(func=ev_a3, name="竞价·N字反包", slot="auction",
               sort="gap", desc="按高开幅度降序，取前 3；低频补位，优先级最低"),
    "J1": dict(func=ev_j1, name="竞价·温和抢筹", slot="auction",
               sort="gap", desc="按高开幅度降序，取前 3"),
    "J2": dict(func=ev_j2, name="竞价·炸板弱转强", slot="auction",
               sort="gap", desc="按高开幅度降序，取前 3"),
    # J3 排序拖累备忘（2026-11-03 回测，暂不改逻辑）：全信号口径样本内均
    # +2.13%，但"昨日成交额升序前 5"的冷门优先部署砍到 +0.75%；且全信号样本外
    # 仅 +0.03%（部署口径 +0.35%）。若日后调整排序，需先重测样本外。
    "J3": dict(func=ev_j3, name="竞价·低开反转", slot="auction",
               sort="amt1", desc="按昨日成交额升序（越冷门越优先），取前 5"),
    "W1": dict(func=ev_w1, name="尾盘·承接强势", slot="tail",
               sort="gap", desc="按涨幅降序，取前 3"),
    # W2（尾盘·放量突破）已于 2026-11-03 下线：样本外均 -1.06% 劣于基准 -0.27%。
    "W3": dict(func=ev_w3, name="尾盘·首板打板", slot="tail",
               sort="mv", desc="按流通市值降序，取前 5"),
    # P1（盘后·MACD金叉）已于 2026-11-03 随盘后时段整体停用，待替换策略：
    # 回测样本外 -0.90%（去排序截断后）/ +0.22%（完全买不截断，受推送总量
    # ≤9 约束无法落地），部署形态无正期望优势。main() 对无注册策略的时段
    # 直接跳过选股/推送/回填；恢复或替换策略时需先重测样本外。
    # P2（盘后·多头创新高）/ P3（盘后·均线粘合）已于 2026-11-03 下线：
    # 样本外均 -3.36% / -2.00%，均劣于基准 -0.89%，负期望。
    # C1 与 W3 判定同源但市值段互补（W3=200-400 亿、C1=400-800 亿），互不重叠、
    # 各出各票；screen() 的时段内代码级去重保留作兜底（W1 等仍可能同票）。
    # 差异化备忘（2026-11-03 回测否决）：曾测"首板次日强势承接"与"首板次日缩量
    # 回踩不破位"两类独立逻辑，样本外均为负（-2.5% / -1.3%），且与 C1 原文要点
    # "次日开盘买是负期望"一致，故维持同源判定 + 市值切分的打板口径。
    "C1": dict(func=ev_c1, name="尾盘·首板确认", slot="tail",
               sort="mv", desc="按流通市值降序，取前 5；市值段 400-800 亿，与 W3 互补"),
}
