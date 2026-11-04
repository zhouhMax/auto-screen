# -*- coding: utf-8 -*-
"""行情抓取：东方财富快照/日K，新浪批量快照作为备用源。"""
import time
import random
import threading
import requests
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SNAP_HOSTS = [
    "http://push2.eastmoney.com/api/qt/clist/get",
    "http://push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
]
KLINE_HOSTS = [
    "http://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
]
SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "CN_MarketData.getKLineData")
SINA_LIST = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData")
SINA_HOST = "https://hq.sinajs.cn/list="
FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
FIELDS = "f2,f3,f5,f6,f8,f10,f12,f14,f15,f16,f17,f18,f20,f21,f100"

RENAME = {
    "f2": "price", "f3": "pct", "f5": "volume", "f6": "amount", "f8": "turnover",
    "f10": "vol_ratio", "f12": "code", "f14": "name", "f15": "high", "f16": "low",
    "f17": "open", "f18": "prev_close", "f20": "total_mv", "f21": "circ_mv",
    "f100": "industry",
}


def _get(url, params, tries=3, timeout=15):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # 网络抖动重试
            last = e
            time.sleep(1.5 * (i + 1) + random.random())
    raise last


def keep_code(code, name):
    """剔除ST、退市、北交所，只保留沪深主板/创业板/科创板。"""
    if not code or not isinstance(code, str):
        return False
    if code.startswith(("4", "8", "920")):
        return False
    if not code.startswith(("600", "601", "603", "605", "000", "001", "002", "003",
                            "300", "301", "688", "689")):
        return False
    n = name or ""
    if "ST" in n.upper() or "退" in n:
        return False
    return True


def _sina_page(page, num=100):
    headers = dict(UA)
    headers["Referer"] = "https://finance.sina.com.cn"
    r = requests.get(SINA_LIST, params={
        "page": page, "num": num, "sort": "symbol", "asc": 1,
        "node": "hs_a", "symbol": "", "_s_r_a": "page",
    }, headers=headers, timeout=20)
    return r.json() or []


def fetch_snapshot_sina_market(pages=60, log=print):
    """新浪全市场列表（备用主源）：分页并发拉取，含流通市值/换手率，缺量比。"""
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_sina_page, p) for p in range(1, pages + 1)]
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception:
                continue
            if r:
                out.extend(r)
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df = df.rename(columns={"code": "code", "name": "name", "trade": "price",
                            "changepercent": "pct", "settlement": "prev_close",
                            "open": "open", "high": "high", "low": "low",
                            "amount": "amount", "turnoverratio": "turnover"})
    df["code"] = df["code"].astype(str).str.zfill(6)
    for c in ["price", "pct", "open", "high", "low", "prev_close", "amount", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce") / 100.0   # 股 -> 手
    df["circ_mv"] = pd.to_numeric(df.get("nmc"), errors="coerce") * 1e4       # 万元 -> 元
    df["total_mv"] = pd.to_numeric(df.get("mktcap"), errors="coerce") * 1e4
    df["vol_ratio"] = np.nan
    df = df[df.apply(lambda r: keep_code(r["code"], r["name"]), axis=1)]
    df = df.drop_duplicates(subset="code").reset_index(drop=True)
    log("新浪列表源：%d 只" % len(df))
    return df


def _em_snapshot(host, log=print):
    """东财全市场快照分页（2026-11-04 修复）：
    接口实际每页固定返回 100 条，`pz=1000` 被服务端忽略，因此按 100 条/页翻页，
    一直拉到 `total` 满额或连续两页空；旧实现最多 10 页（且常在第 7 页中断），
    导致只扫到 605xxx-689xxx 段 618 只（覆盖 12%）——所有时段选股都受影响。
    返回 (rows, total)。"""
    rows, total, page, empty = [], 0, 1, 0
    while page <= 80:                      # 80 页 × 100 = 8000，足够全市场
        js = _get(host, {"pn": page, "pz": 100, "po": 1, "np": 1,
                         "fltt": 2, "invt": 2, "fid": "f12",
                         "fs": FS, "fields": FIELDS})
        data = js.get("data") or {}
        diff = data.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        if not total:
            total = int(data.get("total") or 0)
        if not diff:
            empty += 1
            if empty >= 2:                 # 连续两页空 → 认为已拉完
                break
            page += 1
            continue
        empty = 0
        rows.extend(diff)
        if total and len(rows) >= total:
            break
        page += 1
    return rows, total


def fetch_snapshot(fallback_codes=None, log=print, min_coverage=0.70):
    """全市场快照，返回 DataFrame（成交量单位：手，成交额单位：元）。
    覆盖校验：东财结果不足全市场 min_coverage 时自动切新浪全市场列表源；
    两个源都不足则抛错，避免静默用残缺名单进入选股/推送。"""
    expect = len(set(fallback_codes)) if fallback_codes else 0
    total = 0
    best = None
    for host in SNAP_HOSTS:
        try:
            rows, total = _em_snapshot(host)
        except Exception:
            continue
        if not rows:
            continue
        df = _snapshot_frame(rows)
        if best is None or len(df) > len(best):
            best = df
        denom = total or expect
        if denom and len(df) >= denom * min_coverage:
            break                          # 覆盖达标，直接使用
    if best is None:
        log("东方财富快照失败，切换新浪全市场列表源…")
    else:
        denom = total or expect
        if not denom or len(best) < denom * min_coverage:
            log("警告：东方财富快照仅 %d 只（全市场 %s 只，覆盖 %.0f%%），切换新浪全市场列表源…"
                % (len(best), denom or "未知",
                   len(best) / denom * 100 if denom else 0))
            best = None
    if best is None:
        alt = fetch_snapshot_sina_market(log=log)
        if not alt.empty:
            denom = expect or total
            if denom and len(alt) < denom * min_coverage:
                raise RuntimeError("快照覆盖不足：新浪源仅 %d 只（应有 %d 只），拒绝继续"
                                   % (len(alt), denom))
            return alt
        if not fallback_codes:
            raise RuntimeError("东方财富与新浪列表源均失败，且未提供备用代码列表")
        df = fetch_snapshot_sina(list(fallback_codes))
        if len(df) < expect * min_coverage:
            raise RuntimeError("快照覆盖不足：新浪批量源仅 %d 只（应有 %d 只），拒绝继续"
                               % (len(df), expect))
        return df
    return best


def _snapshot_frame(rows):
    df = pd.DataFrame(rows).rename(columns=RENAME)
    for c in ["price", "pct", "volume", "amount", "turnover", "vol_ratio",
              "high", "low", "open", "prev_close", "total_mv", "circ_mv"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df.drop_duplicates(subset="code")
    df = df[df.apply(lambda r: keep_code(r["code"], r["name"]), axis=1)].copy()
    return df.reset_index(drop=True)


def _sina_symbol(code):
    return ("sh" if code.startswith(("6", "68")) else "sz") + code


def fetch_snapshot_sina(codes=None):
    """备用源：新浪批量行情。缺少量比/换手/流通市值，由调用方用本地基础数据补算。"""
    if codes is None:
        raise RuntimeError("新浪源需要提供股票代码列表")
    out = []
    headers = dict(UA)
    headers["Referer"] = "https://finance.sina.com.cn"
    for i in range(0, len(codes), 500):
        batch = codes[i:i + 500]
        url = SINA_HOST + ",".join(_sina_symbol(c) for c in batch)
        try:
            r = requests.get(url, headers=headers, timeout=15)
            r.encoding = "gbk"
            text = r.text
        except Exception:
            continue
        for line in text.split(";"):
            if "hq_str_" not in line:
                continue
            body = line.split('="')
            if len(body) < 2:
                continue
            code = body[0].strip().split("_")[-1][2:]
            parts = body[1].strip('"').split(",")
            if len(parts) < 32:
                continue
            out.append({
                "code": code, "name": parts[0],
                "open": float(parts[1] or 0), "prev_close": float(parts[2] or 0),
                "price": float(parts[3] or 0), "high": float(parts[4] or 0),
                "low": float(parts[5] or 0),
                "volume": float(parts[8] or 0) / 100.0,   # 股 -> 手
                "amount": float(parts[9] or 0),
            })
        time.sleep(0.3)
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df["pct"] = (df["price"] / df["prev_close"] - 1) * 100
    df["turnover"] = np.nan
    df["vol_ratio"] = np.nan
    df["circ_mv"] = np.nan
    df["total_mv"] = np.nan
    df["high"] = df[["high", "price"]].max(axis=1)
    df["low"] = df[["low", "price"]].min(axis=1)
    return df


def _secid(code):
    return ("1." if code.startswith("6") else "0.") + code


# 东财日K连续失败达到阈值后，本次运行内直接走新浪，避免每次都白等重试
_em_fail = {"n": 0}
_em_lock = threading.Lock()
EM_FAIL_LIMIT = 15


def _em_disabled():
    with _em_lock:
        return _em_fail["n"] >= EM_FAIL_LIMIT


def _em_note(ok):
    with _em_lock:
        _em_fail["n"] = 0 if ok else _em_fail["n"] + 1


def fetch_kline(code, lmt=160):
    js = None
    if not _em_disabled():
        for host in KLINE_HOSTS:
            try:
                js = _get(host, {
                    "secid": _secid(code),
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                    "klt": 101, "fqt": 1, "end": "20500101", "lmt": lmt,
                }, tries=2)
                if js and (js.get("data") or {}).get("klines"):
                    break
            except Exception:
                js = None
        _em_note(bool(js and (js.get("data") or {}).get("klines")))
    if js:
        data = js.get("data") or {}
        lines = data.get("klines") or []
        if lines:
            rows = []
            for ln in lines:
                p = ln.split(",")
                if len(p) < 7:
                    continue
                rows.append((p[0], float(p[1]), float(p[2]), float(p[3]), float(p[4]),
                             float(p[5]), float(p[6])))
            if rows:
                df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low",
                                                 "volume", "amount"])
                df["code"] = code
                return df
    return fetch_kline_sina(code, lmt)


def fetch_kline_sina(code, lmt=160):
    """新浪日线备用源：无成交额字段，按收盘价×成交量估算。"""
    try:
        js = requests.get(SINA_KLINE, params={
            "symbol": _sina_symbol(code), "scale": 240, "ma": "no", "datalen": lmt,
        }, headers=UA, timeout=15).json()
    except Exception:
        return None
    if not js:
        return None
    rows = []
    for it in js:
        try:
            o = float(it["open"]); c = float(it["close"])
            hi = float(it["high"]); lo = float(it["low"])
            vol = float(it["volume"]) / 100.0          # 股 -> 手
            rows.append((it["day"], o, c, hi, lo, vol, c * vol * 100.0))
        except Exception:
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume", "amount"])
    df["code"] = code
    return df


def fetch_history(codes, workers=8, lmt=160, log=print):
    frames = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_kline, c, lmt): c for c in codes}
        for f in as_completed(futs):
            done += 1
            try:
                r = f.result()
                if r is not None:
                    frames.append(r)
            except Exception:
                pass
            if done % 500 == 0:
                log("  进度 %d/%d" % (done, len(codes)))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def fetch_snapshot_by_codes(codes):
    """新浪备用源：按代码列表取快照。"""
    return fetch_snapshot_sina(codes)
