# -*- coding: utf-8 -*-
"""策略实现（对应金太阳公式 J1-J3 / W1-W3 / P1-P3）。实盘注册表 STRATEGIES
现含 8 套：J1-J3 / A3（竞价）、W1 / W3 / C1（尾盘）、P1（盘后）；
W2 / P2 / P3 已于 2026-11-03 回测后下线（函数保留，见各函数上方注释）。
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
    # P1 排序改造（2026-11-03 回测）：原"5日均额降序前 3"样本外 -1.57%（基准
    # -0.43%）；去排序按触发顺序取前 3 为 -0.90%，样本内 +0.06% 不明显恶化，
    # 按"样本外不劣于基线"规则启用。完全买不截取样本外 +0.22% 最优，但受单
    # 时段推送总量 ≤9 约束暂未采用——若放开总量上限可重测后再改。
    "P1": dict(func=ev_p1, name="盘后·MACD金叉", slot="close",
               sort=None, desc="不排序，按触发（扫描）顺序取前 3"),
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
