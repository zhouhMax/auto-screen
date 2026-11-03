# -*- coding: utf-8 -*-
"""用本地通达信日线（D:\\zd_gxzq\\vipdoc）代替网络日线缓存。

.day 格式：32 字节/根，字段为
  日期(uint32, YYYYMMDD) | 开 | 高 | 低 | 收（int32, 元×100）
  成交额(float32, 元) | 成交量(int32, 股) | 保留
转换为项目统一单位：价格元、成交量手、成交额元。

用法：
  python tdx.py              # 重建 data/hist.parquet（全市场，最近 160 根）
"""
import os
import struct
import numpy as np
import pandas as pd

ROOT = r"D:\zd_gxzq\vipdoc"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HIST_PATH = os.path.join(DATA_DIR, "hist.parquet")
BASIC_PATH = os.path.join(DATA_DIR, "basic.parquet")
LMT = 300   # F13（盘后）需 250 根历史，160 不够；backtest.py 自用 520 不受影响
REC = 32


def market_of(code):
    if code[0] in ("6", "5"):
        return "sh"
    if code[0] in ("0", "3", "1"):
        return "sz"
    return "bj"


def read_day(code, lmt=LMT, mkt=None):
    """读取单只股票最近 lmt 根日线，返回 dict 或 None。
    mkt：显式指定市场（"sh"/"sz"/"bj"），用于指数等非股票代码（如 sh000001）。"""
    mk = mkt or market_of(code)
    p = os.path.join(ROOT, mk, "lday", "%s%s.day" % (mk, code))
    if not os.path.exists(p):
        return None
    n = os.path.getsize(p) // REC
    if n < 3:
        return None
    start = max(0, n - lmt) * REC
    with open(p, "rb") as f:
        f.seek(start)
        raw = f.read((n - max(0, n - lmt)) * REC)
    rows = []
    for i in range(len(raw) // REC):
        d, o, h, l, c, amt, vol, _ = struct.unpack("<IIIIIfII", raw[i * REC:(i + 1) * REC])
        if d < 19900101 or c <= 0:
            continue
        rows.append((d, o / 100.0, h / 100.0, l / 100.0, c / 100.0,
                     amt, vol / 100.0))
    if len(rows) < 3:
        return None
    rows.sort(key=lambda x: x[0])
    return {
        "date": [str(r[0]) for r in rows],
        "open": np.array([r[1] for r in rows], dtype=float),
        "high": np.array([r[2] for r in rows], dtype=float),
        "low": np.array([r[3] for r in rows], dtype=float),
        "close": np.array([r[4] for r in rows], dtype=float),
        "volume": np.array([r[6] for r in rows], dtype=float),
        "amount": np.array([r[5] for r in rows], dtype=float),
    }


def load_dict(codes, lmt=LMT, log=print):
    """返回 {code: hist dict}，只保留本地有数据的股票。"""
    out = {}
    for i, code in enumerate(codes):
        h = read_day(code, lmt)
        if h is not None:
            out[code] = h
        if log and (i + 1) % 1000 == 0:
            log("  读取 %d/%d" % (i + 1, len(codes)))
    return out


def build_frame(codes, lmt=LMT, log=print):
    """把本地日线拼成 hist.parquet 所需的 DataFrame。"""
    ds = load_dict(codes, lmt, log)
    frames = [pd.DataFrame({"code": code, **h}) for code, h in ds.items()]
    if not frames:
        return pd.DataFrame(columns=["code", "date", "open", "close", "high",
                                     "low", "volume", "amount"])
    df = pd.concat(frames, ignore_index=True)
    return df[["code", "date", "open", "close", "high", "low", "volume", "amount"]]


def offline_frame(hist, basic):
    """离线评估：用日线最后一根当“今日”构造快照，并把该根从历史中剔除。

    返回 (snap, hist_without_last)。字段与 fetcher.fetch_snapshot 对齐。
    """
    names, fshare = {}, {}
    if basic is not None and not basic.empty:
        names = dict(zip(basic["code"], basic["name"]))
        if "float_shares" in basic.columns:
            fshare = dict(zip(basic["code"], basic["float_shares"]))
    rows, out = [], {}
    for code, h in hist.items():
        if len(h["close"]) < 6:
            continue
        price = float(h["close"][-1])
        pc = float(h["close"][-2])
        v = float(h["volume"][-1])
        ma5v = float(np.mean(h["volume"][-6:-1]))
        fs = fshare.get(code)
        fs = float(fs) if fs is not None and not pd.isna(fs) and fs > 0 else 0.0
        rows.append({
            "code": code, "name": names.get(code, ""),
            "price": price, "open": float(h["open"][-1]),
            "high": float(h["high"][-1]), "low": float(h["low"][-1]),
            "prev_close": pc, "volume": v, "amount": float(h["amount"][-1]),
            "circ_mv": fs * price if fs else 0.0,
            "pct": (price / pc - 1) * 100.0 if pc > 0 else 0.0,
            "turnover": v * 100.0 / fs * 100.0 if fs else 0.0,
            "vol_ratio": v / ma5v if ma5v > 0 else 0.0,
        })
        out[code] = {"date": h["date"][:-1], **{k: v[:-1] for k, v in h.items() if k != "date"}}
    return pd.DataFrame(rows), out


def main():
    codes = []
    if os.path.exists(BASIC_PATH):
        basic = pd.read_parquet(BASIC_PATH)
        codes = [str(c) for c in basic["code"]]
        log("按 basic.parquet 读取 %d 只" % len(codes))
    else:
        for m in ("sh", "sz", "bj"):
            d = os.path.join(ROOT, m, "lday")
            if os.path.isdir(d):
                codes += [f[len(m):].split(".")[0] for f in os.listdir(d)
                          if f.endswith(".day") and f[len(m):].split(".")[0].isdigit()]
        log("未找到 basic.parquet，扫描目录 %d 个文件" % len(codes))

    df = build_frame(sorted(set(codes)))
    if df.empty:
        raise RuntimeError("本地日线为空，请确认 %s" % ROOT)
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = HIST_PATH + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, HIST_PATH)
    log("已写入 %s：%d 只、%d 行，日期 %s ~ %s"
        % (HIST_PATH, df["code"].nunique(), len(df), df["date"].min(), df["date"].max()))


def log(*a):
    print(*a, flush=True)


if __name__ == "__main__":
    main()
