# -*- coding: utf-8 -*-
"""每日选股主程序。
用法：
  python main.py --slot auction|tail|close|auto|update-history [--dry-run] [--no-wait]
"""
import os
import sys
import json
import time
import argparse
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import fetcher
import boards
from strategies import STRATEGIES

CN = ZoneInfo("Asia/Shanghai")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HIST_PATH = os.path.join(DATA_DIR, "hist.parquet")
BASIC_PATH = os.path.join(DATA_DIR, "basic.parquet")
META_PATH = os.path.join(DATA_DIR, "meta.json")

TARGET = {"auction": "09:25:05", "tail": "14:40:30", "close": "15:40:30"}
MINUTES = {"auction": 1, "tail": 220, "close": 240}
SLOT_NAME = {"auction": "早盘", "tail": "尾盘", "close": "盘后"}
SLOT_TIP = {
    "auction": "9:30 开盘后 1-3 分钟内买入，不挂低价等回踩；止损：J1 开盘价-1.5%，J2 昨日最低，J3 -3%。",
    "tail": "14:45 后确认股价在分时均价线上方再买；W1 隔夜，次日冲高 +3%~+5% 分批；W3 打板以涨停价挂单，14:57 未封板放弃。",
    "close": "次日开盘买；高开>3%等回踩分时均价线；P1 不排序，按触发先后取前 3。",
}

# 推送规则（2026-11-02 起）：总量 ≤9，每策略 ≤3，不限板块
# （2026-11-03 下线 W2/P2/P3 后实际构成：竞价 J1/J2/J3/A3、尾盘 W1/W3/C1、盘后 P1；
#   输出条数按当日实际命中自适应，无写死的策略数假设）
MAX_TOTAL = 9
MAX_PER_STRATEGY = 3
PRIORITY = {"auction": ["J2", "J3", "J1", "A3"], "tail": ["W1", "W3", "C1"],
            "close": ["P1"]}
RANK_TEXT = {1: "强烈推荐", 2: "推荐", 3: "谨慎推荐"}

# 策略逻辑、止盈/止损、持有期
REASON = {
    "J1": "高开 1%-4%、量比 2-8、换手 1%-12%，开盘价站上 10 日线且未涨停，竞价资金主动抢筹",
    "J2": "昨日冲板未封住（炸板），今日高开 3%-6%，昨日成交量 ≥ 近 20 日均量 1.5 倍，弱转强",
    "J3": "低开 2%-5% 但昨日未跌停、非一字板，昨日成交额 0.3-3 亿，冷门小盘超跌反转",
    "W1": "尾盘涨幅 2%-5%、量比 >1、换手 3%-18%，收盘站上 5/10 日线与当日分时均价",
    "W3": "今日首板涨停且封板过程有炸板（非一字），成交额 ≥3 亿、流通市值 200-400 亿（中盘段，与 C1 市值互补）",
    "P1": "MACD 金叉（按代码实际判定，未验证零轴位置），收盘处于当日振幅 55% 以上，5 日均额 ≥15 亿，资金聚焦",
    "A3": "前日涨停→昨日收阴洗盘未破前日中位→今日竞价高开 0.5%-6% 反包；昨日量比 ≥2.5、成交额 ≥5 亿、流通市值 50-800 亿（低频，月均不足 1 笔）",
    "C1": "尾盘确认首板涨停（昨日未涨停）、非一字可买、成交额 ≥3 亿、流通市值 400-800 亿（与 W3 的 200-400 亿市值互补，独立票池各出各票）；涨停价打板买入后按公式持有 10 个交易日（区别于 W3 的次日冲高即走）",
}
TP = {"J1": 5, "J2": 8, "J3": 6, "W1": 5, "W3": 5,
      "P1": 8, "A3": 6, "C1": 15}
SL = {"J1": 2.5, "J2": 4, "J3": 4, "W1": 3, "W3": 5,
      "P1": 5, "A3": 4, "C1": 8}
HOLD = {"J1": "1-3 个交易日", "J2": "1-3 个交易日", "J3": "1-3 个交易日",
        "W1": "次日为主，最长 3 日", "W3": "次日冲高即走",
        "P1": "3-5 个交易日",
        "A3": "2 个交易日（公式口径）", "C1": "10 个交易日（公式口径，打板买入起算）"}
BUY_RANGE = {"auction": (0.995, 1.015), "tail": (0.99, 1.01), "close": (0.99, 1.025)}


# ------------------------------------------------------------------ 工具
def log(*a):
    print(*a, flush=True)


def now_cn():
    return dt.datetime.now(CN)


def _n(x, default=0.0):
    """NaN / None 安全的数值转换。"""
    try:
        if x is None:
            return default
        v = float(x)
        return default if pd.isna(v) else v
    except Exception:
        return default


def auto_slot(now):
    hm = now.hour * 100 + now.minute
    if 855 <= hm <= 1130:
        return "auction"
    if 1300 <= hm <= 1500:
        return "tail"
    return "close"


def wait_for(slot, enabled=True):
    target = TARGET[slot]
    hh, mm, ss = [int(x) for x in target.split(":")]
    now = now_cn()
    tgt = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    delta = (tgt - now).total_seconds()
    if delta > 0:
        if not enabled:
            log("跳过等待（--no-wait），当前 %s，目标 %s" % (now.strftime("%H:%M:%S"), target))
            return False
        log("等待至 %s（%.0f 秒）" % (target, delta))
        if delta > 900:
            log("警告：距目标超过 15 分钟，可能不是交易时段")
        time.sleep(min(delta, 900))
        return False
    late = -delta
    if late > 120:
        log("警告：启动已晚于目标 %.0f 秒，结果可能偏离信号价" % late)
        return True
    return False


def load_hist():
    if not os.path.exists(HIST_PATH):
        raise RuntimeError("缺少 %s，请先运行 --slot update-history" % HIST_PATH)
    df = pd.read_parquet(HIST_PATH)
    out = {}
    for code, g in df.groupby("code", sort=False):
        g = g.sort_values("date")
        out[code] = {
            "date": g["date"].tolist(),
            "open": g["open"].to_numpy(dtype=float),
            "close": g["close"].to_numpy(dtype=float),
            "high": g["high"].to_numpy(dtype=float),
            "low": g["low"].to_numpy(dtype=float),
            "volume": g["volume"].to_numpy(dtype=float),
            "amount": g["amount"].to_numpy(dtype=float),
        }
    return out


def load_basic():
    if not os.path.exists(BASIC_PATH):
        return pd.DataFrame()
    return pd.read_parquet(BASIC_PATH)


def expected_prev_date(now):
    d = now.date() - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return str(d)


def attach_boards(df):
    """用本地通达信板块数据补齐行业、概念（读取 boards.csv）。"""
    ind, con = boards.ensure(log=log)
    if df.empty or (not ind and not con):
        return df
    df = df.copy()
    local_ind = df["code"].map(ind).fillna("")
    if "industry" in df.columns:
        cur = df["industry"].fillna("").astype(str).str.strip()
        df["industry"] = local_ind.where(local_ind != "", cur)
    else:
        df["industry"] = local_ind
    df["concept"] = df["code"].map(lambda c: "、".join(con.get(c) or []))
    return df


def _save_parquet(df, path, tries=4):
    """先写临时文件再原子替换；对 Windows 偶发占用做退避重试。"""
    for i in range(tries):
        tmp = "%s.%d.tmp" % (path, i)
        try:
            df.to_parquet(tmp, index=False)
            for _ in range(4):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    time.sleep(1)
        except PermissionError:
            pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        time.sleep(1.5 * (i + 1))
    raise RuntimeError("写入失败（多次重试仍被占用）：%s" % path)


# ------------------------------------------------------------------ 更新历史
def update_history(max_codes=0):
    os.makedirs(DATA_DIR, exist_ok=True)
    log("拉取全市场快照（用于名称与流通股本）…")
    snap = fetcher.fetch_snapshot()
    if snap.empty:
        raise RuntimeError("快照为空，无法更新")
    snap["float_shares"] = np.where(snap["price"] > 0, snap["circ_mv"] / snap["price"], np.nan)
    cols = ["code", "name", "float_shares", "circ_mv", "price"]
    if "industry" in snap.columns:
        cols.append("industry")
    basic = snap[cols].copy()
    basic["date"] = now_cn().strftime("%Y-%m-%d")
    if max_codes and max_codes > 0:
        basic = basic.head(max_codes).copy()
    basic = attach_boards(basic)
    _save_parquet(basic, BASIC_PATH)
    log("基础数据 %d 只" % len(basic))

    codes = list(basic["code"])
    log("拉取 %d 只个股日线（约 2-8 分钟）…" % len(codes))
    hist = fetcher.fetch_history(codes, workers=16, lmt=160, log=log)
    if hist.empty:
        raise RuntimeError("日线为空，无法更新")
    _save_parquet(hist, HIST_PATH)
    meta = {"updated": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
            "last_date": str(hist["date"].max()), "rows": int(len(hist))}
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    log("完成：%d 行，最新交易日 %s" % (len(hist), meta["last_date"]))


# ------------------------------------------------------------------ 选股
def build_rows(snap, hist, basic, slot):
    minutes = MINUTES[slot]
    fshare = {}
    if not basic.empty and "float_shares" in basic.columns:
        fshare = dict(zip(basic["code"], basic["float_shares"]))
    ind_map = {}
    if not basic.empty and "industry" in basic.columns:
        ind_map = {k: ("" if pd.isna(v) else str(v)) for k, v in
                   zip(basic["code"], basic["industry"])}
    con_map = {}
    if not basic.empty and "concept" in basic.columns:
        con_map = {k: ("" if pd.isna(v) else str(v)) for k, v in
                   zip(basic["code"], basic["concept"])}
    rows = []
    for r in snap.itertuples(index=False):
        code = r.code
        h = hist.get(code)
        if h is None or len(h["close"]) < 3:
            continue
        price = _n(r.price)
        if price <= 0:
            continue
        pc = _n(r.prev_close) or float(h["close"][-1])
        o = _n(r.open) or price
        hi = max(_n(r.high), price, o)
        lo = min([x for x in [_n(r.low), price, o] if x > 0] or [price])
        v = _n(r.volume)
        amt = _n(r.amount)
        mv = _n(r.circ_mv)
        if mv <= 0 and fshare.get(code, np.nan) and not np.isnan(fshare.get(code, np.nan)):
            mv = float(fshare[code]) * price
        ma5v = float(np.mean(h["volume"][-5:])) if len(h["volume"]) >= 5 else 0
        lb = _n(r.vol_ratio)
        if lb <= 0 and ma5v > 0:
            lb = v / (ma5v / 240.0 * minutes)
        hs = _n(r.turnover)
        if hs <= 0 and fshare.get(code) and not np.isnan(fshare.get(code, np.nan)):
            hs = v * 100.0 / float(fshare[code]) * 100.0
        rows.append({
            "code": code, "name": r.name, "price": price, "open": o, "high": hi, "low": lo,
            "volume": v, "amount": amt, "prev_close": pc, "circ_mv": mv,
            "turnover": hs, "vol_ratio": lb, "minutes": minutes,
            "industry": ind_map.get(code, "") or
                        str(getattr(r, "industry", "") or "").strip(),
            "concept": con_map.get(code, "") or
                       str(getattr(r, "concept", "") or "").strip(),
        })
    return rows


def screen(slot, hist, basic, snap):
    rows = build_rows(snap, hist, basic, slot)
    cands = []
    per_strategy = {}
    for sid, cfg in STRATEGIES.items():
        if cfg["slot"] != slot:
            continue
        hits = []
        for t in rows:
            h = hist.get(t["code"])
            try:
                m = cfg["func"](t, h)
            except Exception:
                m = None
            if m:
                item = dict(m)
                item.update({"code": t["code"], "name": t["name"],
                             "industry": t.get("industry", ""),
                             "concept": t.get("concept", ""), "sid": sid})
                hits.append(item)
        key = cfg.get("sort")
        if key:                             # sort=None（P1）不排序，按触发顺序
            rev = not (sid == "J3")        # J3 按昨成交额升序，其余降序
            hits.sort(key=lambda x: x.get(key, 0) or 0, reverse=rev)
        hits = hits[:MAX_PER_STRATEGY]
        for i, h in enumerate(hits):
            h["rank"] = i + 1
        per_strategy[sid] = len(hits)
        cands.extend(hits)

    prio = PRIORITY[slot]
    cands.sort(key=lambda x: (prio.index(x["sid"]) if x["sid"] in prio else 99,
                              x["rank"]))
    # 时段内代码级去重：同一只股票只保留排序最前（优先级最高、排名最靠前）
    # 策略的信号。C1 与 W3 判定同源，W3 优先级更高，因此 W3 已选中的股票
    # C1 不再重复计入，避免表格重复行与微信重复条目。
    picked, seen_codes = [], set()
    for x in cands:
        if len(picked) >= MAX_TOTAL:
            break
        if x["code"] in seen_codes:
            continue
        seen_codes.add(x["code"])
        picked.append(x)
    return picked, len(rows), len(cands), per_strategy


# ------------------------------------------------------------------ 推送
def build_message(slot, picked, scanned, n_cands, late, meta, per_strategy=None):
    now = now_cn()
    per_strategy = per_strategy or {}
    lines = ["信号 %s｜扫描 %d 只｜候选 %d 只｜输出 %d 只%s"
             % (now.strftime("%H:%M:%S"), scanned, n_cands, len(picked),
                "｜**延迟启动**" if late else ""),
             "历史数据截至 %s" % meta.get("last_date", "未知"), ""]
    if not picked:
        lines += ["今日无信号。严格阈值下 0 只属于正常现象，不要为凑数放宽条件。", ""]
    base_key = "open" if slot == "auction" else "price"
    lo_k, hi_k = BUY_RANGE[slot]
    for i, h in enumerate(picked):
        sid = h["sid"]
        base = float(h.get(base_key) or h["price"])
        lo, hi = base * lo_k, base * hi_k
        tp = base * (1 + TP[sid] / 100.0)
        sl = base * (1 - SL[sid] / 100.0)
        sec = h.get("industry") or "未分类"
        con = (h.get("concept") or "").strip()
        sector = sec + ("｜" + con if con else "")
        lines += [
            "### %d｜%s %s · %s" % (i + 1, h["name"], h["code"], RANK_TEXT.get(h["rank"], "谨慎推荐")),
            "- 命中：%s %s（策略内第 %d 名）" % (sid, STRATEGIES[sid]["name"], h["rank"]),
            "- 板块：%s" % sector,
            "- 逻辑：%s" % REASON[sid],
            "- 买入区间：%.2f - %.2f" % (lo, hi),
            "- 止盈 %.2f（+%d%%）｜止损 %.2f（-%s%%）"
            % (tp, TP[sid], sl, ("%.1f" % SL[sid]).rstrip("0").rstrip(".")),
            "- 持有期：%s" % HOLD[sid],
            "",
        ]
    dist = "｜".join("%s %d" % (k, v) for k, v in per_strategy.items() if v)
    if dist:
        lines.append("> 本时段信号分布：%s" % dist)
        lines.append("")
    lines += ["> " + SLOT_TIP[slot],
              "",
              "> 本消息为量化研究输出，不构成投资建议；回测收益不代表未来表现。"]
    return "\n".join(lines)


def push(content, summary):
    token = os.environ.get("WXPUSHER_APP_TOKEN", "").strip()
    uids = [x.strip() for x in os.environ.get("WXPUSHER_UIDS", "").split(",") if x.strip()]
    topics = [x.strip() for x in os.environ.get("WXPUSHER_TOPIC_IDS", "").split(",") if x.strip()]
    if not token:
        log("未配置 WXPUSHER_APP_TOKEN，跳过推送。内容如下：\n")
        log(content)
        return 0
    payload = {"appToken": token, "content": content, "summary": summary,
               "contentType": 3, "uids": uids}
    if topics:
        payload["topicIds"] = topics
    import requests
    for i in range(3):
        try:
            r = requests.post("https://wxpusher.zjiecode.com/api/send/message",
                              json=payload, timeout=20)
            js = r.json()
            log("WxPusher 返回：%s" % json.dumps(js, ensure_ascii=False))
            if js.get("code") == 1000:
                return 0
        except Exception as e:
            log("推送失败（第 %d 次）：%s" % (i + 1, e))
        time.sleep(2)
    return 1


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="auto",
                    choices=["auto", "auction", "tail", "close", "update-history"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--max-codes", type=int, default=0, help="仅更新前 N 只，用于调试")
    ap.add_argument("--offline", action="store_true",
                    help="离线：用本地通达信日线最后一日当快照，不联网")
    ap.add_argument("--excel", action="store_true",
                    help="把结果回填到选股池-TR.xlsx（每日选股/精选池/优中选优/分析）")
    ap.add_argument("--json-out", action="store_true",
                    help="把结果写入 data/results/<日期>_<时段>.json，供云端提交后本地回填")
    args = ap.parse_args()

    now = now_cn()
    slot = auto_slot(now) if args.slot == "auto" else args.slot
    if slot == "update-history":
        update_history(args.max_codes)
        return 0

    meta = {}
    if os.path.exists(META_PATH):
        with open(META_PATH, encoding="utf-8") as f:
            meta = json.load(f)
    exp = expected_prev_date(now)
    last = meta.get("last_date", "")
    if last and last < exp:
        oldest = (dt.date.fromisoformat(exp) - dt.date.fromisoformat(last)).days
        if oldest >= 3:
            log("警告：历史数据最新为 %s，早于预期 %s，请先跑 update-history" % (last, exp))

    late = wait_for(slot, enabled=not args.no_wait)

    hist = load_hist()
    basic = attach_boards(load_basic())

    t0 = time.time()
    if args.offline:
        import tdx
        snap, hist = tdx.offline_frame(hist, basic)
        log("离线模式：%d 只，信号价取本地日线 %s 收盘" % (len(snap), meta.get("last_date", "未知")))
    else:
        log("时段：%s，开始拉取快照…" % slot)
        fb = list(basic["code"]) if not basic.empty else None
        snap = fetcher.fetch_snapshot(fallback_codes=fb)
        if snap.empty:
            raise RuntimeError("快照为空")
        today = now.strftime("%Y%m%d")
        n_trim = 0
        for code, h in hist.items():
            if h["date"] and str(h["date"][-1]).replace("-", "") == today:
                hist[code] = {"date": h["date"][:-1],
                              **{k: v[:-1] for k, v in h.items() if k != "date"}}
                n_trim += 1
        if n_trim:
            log("日线已含今日，剔除 %d 只的当日 K 线以避免重复计价" % n_trim)
    snap = attach_boards(snap)
    if snap["circ_mv"].isna().all() or (snap["circ_mv"] == 0).all():
        log("快照缺少流通市值，改用本地基础数据补算（新浪备用源）")
    picked, scanned, n_cands, per_strategy = screen(slot, hist, basic, snap)
    log("扫描 %d 只，候选 %d 只，最终输出 %d 只，耗时 %.1fs"
        % (scanned, n_cands, len(picked), time.time() - t0))
    for h in picked:
        sector = h.get("industry") or "未分类"
        if h.get("concept"):
            sector += "｜" + h["concept"]
        log("  %d. %s %s【%s·第%d】%s" % (h["rank"], h["code"], h["name"], h["sid"],
                                        h["rank"], sector))

    msg = build_message(slot, picked, scanned, n_cands, late, meta, per_strategy)
    if args.json_out:
        try:
            import excel_log
            payload = excel_log.build_payload(slot, picked, snap, now)
            out_dir = os.path.join(DATA_DIR, "results")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, "%s_%s.json" % (payload["date"], slot))
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
            log("结果已写入 %s" % path)
        except Exception as e:
            log("结果 JSON 写入失败：%s" % e)
    if args.excel:
        try:
            import excel_log
            excel_log.write(slot, picked, snap, hist, now, log)
        except Exception as e:
            log("Excel 回填失败：%s" % e)
    if args.dry_run:
        log(msg)
        return 0
    summary = "【Trae CN】%s%s选股结果" % (now.strftime("%m%d"), SLOT_NAME[slot])
    return push(msg, summary)


if __name__ == "__main__":
    sys.exit(main())
