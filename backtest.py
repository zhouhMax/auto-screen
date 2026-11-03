# -*- coding: utf-8 -*-
"""A3（竞价·N字反包）与 C1（首板）回测。

与实盘同源：本地通达信日线（D:\\zd_gxzq\\vipdoc），与 tdx.py / 离线模式完全一致。

口径与可信度说明（重要）：
- A3 的"今日竞价价"用当日开盘价 —— 9:25 集合竞价结果即开盘价，口径准确。
- A3 买入 = 信号日开盘价；卖出 = 第 2 个交易日收盘（公式要点"持有满 2 个交易日"）。
- C1 两种买入口径：
    A（打板版）= 信号日收盘价（近似当日 14:40 涨停价打板成交，假设打板必成交）；
    B（盘后版）= 次日开盘价（盘后信号次日执行，公式自述"次日开盘买是负期望"，用于验证）。
  卖出 = 自买入日起第 10 个交易日收盘（口径 B 为 T+1 日起算 10 日 → 信号日 T 的 T+10 收盘）。
- 流通市值 = float_shares（basic.parquet，当前值）× 当日收盘 —— 股本用当前值回溯，
  未回溯历史股本变动与送转，可信度：中。
- ST 判定用当前股票简称，历史 ST 状态不可得，可信度：中。
- 未模拟涨停无法成交、跌停无法卖出、滑点与手续费（毛收益口径）。
- 幸存者偏差：以当前在市股票为基础，已退市股票不在样本内。

用法：
  python backtest.py            # 跑基线参数 + 参数敏感性 + 样本内/外切分
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tdx
from strategies import ztl

LMT = 520          # 每只股票读取的日线根数（约 2 年）
IN_RATIO = 0.7     # 样本内占比（按信号日期切分）

# ---------------- 基线参数（公式默认值） ----------------
A3 = dict(N1=5.0, N2=50.0, N3=800.0, N4=0.5, N5=6.0, N6=2.5, recent40=False)
C1 = dict(N1=3.0, N2=200.0, N3=800.0, hold=10)


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ 数据加载
def load_universe():
    basic = pd.read_parquet(tdx.BASIC_PATH)
    uni = {}
    for r in basic.itertuples(index=False):
        h = tdx.read_day(str(r.code), lmt=LMT)
        if h is None or len(h["date"]) < 40:
            continue
        fs = float(r.float_shares) if r.float_shares and not pd.isna(r.float_shares) else 0.0
        uni[str(r.code)] = dict(name=str(r.name), fs=fs, **h)
    return uni


def precompute(code, u):
    """每只股票预计算与参数无关的特征数组。"""
    o, c, hi, lo = u["open"], u["close"], u["high"], u["low"]
    v, amt = u["volume"], u["amount"]
    n = len(c)
    z = ztl(code, u["name"])        # 涨停幅按代码前缀判（300/301/688=20cm），修正旧版仅按简称判的口径
    feat = {"z": z}

    # --- A3 特征（i 为信号日"今日"，条件全部用 i-1 及更早 + 当日开盘）---
    if n > 22:
        zt2 = np.zeros(n, bool)
        # zt2[k] = c[k-2] >= c[k-3]*z*0.995，即"前日涨停"（与 ev_a3 的 h[-2]/h[-3] 对齐）
        zt2[3:] = c[1:-2] >= c[:-3] * z * 0.995
        yin = np.zeros(n, bool)
        yin[2:] = o[1:-1] > c[1:-1]
        mid = np.full(n, np.nan)
        mid[2:] = (o[:-2] + c[:-2]) / 2.0
        hold = np.zeros(n, bool)
        hold[2:] = lo[1:-1] >= mid[2:]
        amt1 = np.full(n, np.nan)
        amt1[1:] = amt[:-1] / 1e8
        fmv1 = np.full(n, np.nan)
        if u["fs"] > 0:
            fmv1[1:] = u["fs"] * c[:-1] / 1e8
        gap = np.full(n, np.nan)
        gap[1:] = (o[1:] / c[:-1] - 1.0) * 100.0
        vr1 = np.full(n, np.nan)
        if n >= 21:
            ma20 = np.convolve(v, np.ones(20) / 20.0, mode="valid")   # ma[k]=mean(v[k..k+19])
            vr1[20:] = v[19:-1] / ma20[:-1]      # vr1[i]=v[i-1]/mean(v[i-20..i-1])
        # 近 10 日涨幅（信号日前一日收盘 / 11 日前收盘）
        recent = np.full(n, np.nan)
        if n >= 12:
            recent[11:] = c[10:-1] / c[:-11] - 1.0
        feat.update(zt2=zt2, yin=yin, hold=hold, amt1=amt1, fmv1=fmv1,
                    gap=gap, vr1=vr1, recent=recent)

    # --- C1 特征（i 为信号日"今日"）---
    zt = np.zeros(n, bool)
    zt[1:] = c[1:] >= c[:-1] * z - 0.005
    kegou = hi > lo * 1.001
    fmv = np.full(n, np.nan)
    if u["fs"] > 0:
        fmv[:] = u["fs"] * c / 1e8
    feat.update(zt=zt, kegou=kegou, amty=amt / 1e8, fmv=fmv)
    return feat


# ------------------------------------------------------------------ 信号扫描
def scan_a3(uni, feats, p):
    """A3：返回交易列表 [{date, code, entry, exit, adverse, gap}]。"""
    out = []
    for code, u in uni.items():
        f = feats[code]
        if "zt2" not in f:
            continue
        n = len(u["close"])
        m = (f["zt2"] & f["yin"] & f["hold"]
             & (f["amt1"] >= p["N1"])
             & (f["fmv1"] >= p["N2"]) & (f["fmv1"] <= p["N3"])
             & (f["gap"] >= p["N4"]) & (f["gap"] <= p["N5"]))
        if p["N6"] > 0:
            m &= (f["vr1"] >= p["N6"])
        if p.get("recent40"):
            with np.errstate(invalid="ignore"):
                m &= ~(f["recent"] > 0.40)
        for i in np.nonzero(m)[0]:
            if i + 2 >= n:
                continue
            out.append(dict(
                date=u["date"][i], code=code, gap=float(f["gap"][i]),
                entry=float(u["open"][i]),
                ret=(float(u["close"][i + 2]) / float(u["open"][i]) - 1) * 100,
                adverse=(float(np.min(u["low"][i:i + 3])) / float(u["open"][i]) - 1) * 100))
    return out


def scan_c1(uni, feats, p, entry="A"):
    """C1：entry=A 打板（信号日收盘进），B 次日开盘进。hold 个交易日后收盘卖出。"""
    out = []
    hold = int(p["hold"])
    for code, u in uni.items():
        f = feats[code]
        n = len(u["close"])
        z = f["z"]
        prev_zt = np.zeros(n, bool)
        prev_zt[2:] = u["close"][1:-1] >= u["close"][:-2] * z - 0.005
        m = (f["zt"] & ~prev_zt & f["kegou"]
             & (f["amty"] >= p["N1"])
             & (f["fmv"] >= p["N2"]) & (f["fmv"] <= p["N3"]))
        for i in np.nonzero(m)[0]:
            if entry == "A":
                if i + hold >= n:
                    continue
                e, s = i, i + hold
            else:
                if i + 1 + hold >= n:
                    continue
                e, s = i + 1, i + 1 + hold
            ep = float(u["open"][e]) if entry == "B" else float(u["close"][i])
            if ep <= 0:
                continue
            out.append(dict(
                date=u["date"][i], code=code, mv=float(f["fmv"][i]),
                entry=ep,
                ret=(float(u["close"][s]) / ep - 1) * 100,
                adverse=(float(np.min(u["low"][e:s + 1])) / ep - 1) * 100))
    return out


# ------------------------------------------------------------------ 统计
def stats(trades, label):
    if not trades:
        return dict(label=label, n=0)
    ret = np.array([t["ret"] for t in trades])
    adv = np.array([t["adverse"] for t in trades])
    win, loss = ret[ret > 0], ret[ret <= 0]
    pf = float(win.mean() / abs(loss.mean())) if len(win) and len(loss) else float("inf")
    return dict(label=label, n=len(trades),
                win=float((ret > 0).mean() * 100), avg=float(ret.mean()),
                med=float(np.median(ret)), pf=pf,
                worst_adv=float(adv.min()), best=float(ret.max()),
                worst=float(ret.min()))


def cap_by_day(trades, key, top, rev=True):
    """按日分组取前 top（截面上限，对应公式"涨幅前3/市值前5"）。"""
    by = {}
    for t in trades:
        by.setdefault(t["date"], []).append(t)
    out = []
    for d, ts in sorted(by.items()):
        ts.sort(key=lambda x: x.get(key, 0) or 0, reverse=rev)
        out.extend(ts[:top])
    return out


def split_io(trades):
    """按信号日期 70/30 切分样本内/外。"""
    if not trades:
        return [], []
    ds = sorted({t["date"] for t in trades})
    cut = ds[int(len(ds) * IN_RATIO)]
    return [t for t in trades if t["date"] < cut], [t for t in trades if t["date"] >= cut]


def show(rows, title):
    log("\n== %s ==" % title)
    log("%-42s %6s %7s %7s %7s %8s %8s" %
        ("口径", "笔数", "胜率%", "均收益", "中位数", "盈亏比", "最差浮亏"))
    for r in rows:
        if r.get("n", 0) == 0:
            log("%-42s %6d" % (r["label"], 0))
            continue
        log("%-42s %6d %7.1f %+7.2f %+7.2f %7.2f %+8.1f" %
            (r["label"], r["n"], r["win"], r["avg"], r["med"], r["pf"], r["worst_adv"]))


# ------------------------------------------------------------------ 主流程
def main():
    log("加载本地日线（每只最近 %d 根）…" % LMT)
    uni = load_universe()
    log("股票 %d 只，日期 %s ~ %s"
        % (len(uni), min(u["date"][0] for u in uni.values()),
           max(u["date"][-1] for u in uni.values())))
    log("预计算特征…")
    feats = {c: precompute(c, u) for c, u in uni.items()}

    # ---------- A3 基线 ----------
    base_a3 = scan_a3(uni, feats, A3)
    show([stats(base_a3, "A3 全信号（无截取）"),
          stats(cap_by_day(base_a3, "gap", 3), "A3 每日涨幅前3（公式用法）")],
         "A3 基线 N1=5 N2=50 N3=800 N4=0.5 N5=6 N6=2.5")

    # ---------- A3 参数敏感性（样本内）----------
    isA, _ = split_io(cap_by_day(base_a3, "gap", 3))
    log("\nA3 样本内日期切点：%s" % (sorted({t["date"] for t in isA})[-1] if isA else "-"))
    rows = []
    for n6 in (0, 1.5, 2.5, 3.5):
        for n1 in (3, 5, 8):
            p = dict(A3, N6=n6, N1=n1)
            rows.append(stats(cap_by_day(split_io(scan_a3(uni, feats, p))[0], "gap", 3),
                              "N6=%s N1=%s" % (n6, n1)))
    show(rows, "A3 敏感性：N6 × N1（样本内，每日前3）")
    rows = []
    for n4, n5 in ((0.5, 6), (1, 5), (2, 7), (0.5, 9)):
        p = dict(A3, N4=n4, N5=n5)
        rows.append(stats(cap_by_day(split_io(scan_a3(uni, feats, p))[0], "gap", 3),
                          "高开 %.1f-%.1f%%" % (n4, n5)))
    p = dict(A3, recent40=True)
    rows.append(stats(cap_by_day(split_io(scan_a3(uni, feats, p))[0], "gap", 3),
                      "排除近10日涨幅>40%"))
    show(rows, "A3 敏感性：高开区间 / 连板过滤（样本内，每日前3）")

    # ---------- C1 基线 ----------
    base_c1a = scan_c1(uni, feats, C1, entry="A")
    base_c1b = scan_c1(uni, feats, C1, entry="B")
    show([stats(base_c1a, "C1 全信号（无截取）"),
          stats(cap_by_day(base_c1a, "mv", 5), "C1 每日市值前5·打板进（公式用法）"),
          stats(cap_by_day(base_c1b, "mv", 5), "C1 每日市值前5·次日开盘进")],
         "C1 基线 N1=3 N2=200 N3=800 持有10日")

    # ---------- C1 参数敏感性（样本内）----------
    rows = []
    for n2 in (50, 100, 200, 400):
        p = dict(C1, N2=n2)
        rows.append(stats(cap_by_day(split_io(scan_c1(uni, feats, p, "A"))[0], "mv", 5),
                          "N2=%d亿 打板" % n2))
    show(rows, "C1 敏感性：市值下限（样本内，每日前5，打板进）")
    rows = []
    for hold in (3, 5, 10):
        p = dict(C1, hold=hold)
        rows.append(stats(cap_by_day(split_io(scan_c1(uni, feats, p, "A"))[0], "mv", 5),
                          "持有%d日 打板" % hold))
        rows.append(stats(cap_by_day(split_io(scan_c1(uni, feats, p, "B"))[0], "mv", 5),
                          "持有%d日 次日开盘进" % hold))
    show(rows, "C1 敏感性：持有期 × 买入口径（样本内，每日前5）")

    # ---------- 选定参数的样本内/外对照 ----------
    log("\n（样本内/外对照见上方切分日之后的口径，最终结论由分析输出汇总）")


if __name__ == "__main__":
    main()
