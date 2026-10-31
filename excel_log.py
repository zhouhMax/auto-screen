# -*- coding: utf-8 -*-
"""把选股结果按选股池表格样式回填到 Excel。

目标：E:\\DouBao\\股票相关\\选股池-TR.xlsx
工作表：
  每日选股  —— 每时段全部输出（≤6 只）
  精选池    —— 每日选股中“策略内第 1 名 / 强烈推荐”的个股
  优中选优  —— 在精选池基础上，按板块热度与资金强弱再优选 1 只
  分析      —— 与每日选股同源，附带精选池/优中选优标记与区间涨幅（自动回算）

“资金流向”用快照自身的成交额×涨跌幅、量比、换手率做代理（不新增网络请求）。
"""
import os
import time
from datetime import datetime

import numpy as np
import pandas as pd

import main as M

LOG_PATH = r"E:\DouBao\股票相关\选股池-TR.xlsx"
SHEET_DAILY = "每日选股"
SHEET_PICK = "精选池"
SHEET_BEST = "优中选优"
SHEET_ANALYSIS = "分析"

COL_ANALYSIS = [
    "选股日期", "选股时段", "策略代号", "股票代码", "股票名称", "选股价格",
    "建议买入时机", "策略止盈时间", "策略卖出价格", "按策略止盈涨幅",
    "第2日收盘涨幅", "第3日收盘涨幅", "第5日收盘涨幅", "第10日收盘涨幅",
    "第20日收盘涨幅", "策略内排名", "推荐程度", "是否在精选池", "是否在优中选优",
]
RET_COLS = [(2, "第2日收盘涨幅"), (3, "第3日收盘涨幅"), (5, "第5日收盘涨幅"),
            (10, "第10日收盘涨幅"), (20, "第20日收盘涨幅")]
SLOT_TEXT = {"auction": "竞价", "tail": "尾盘", "close": "盘后"}


def _slot_text(slot):
    return SLOT_TEXT.get(slot, slot)


def _code_int(code):
    return int(code) if str(code).isdigit() else str(code)


def _sector(h):
    sec = (h.get("industry") or "未分类").strip()
    con = (h.get("concept") or "").strip()
    return sec + ("｜" + con if con else "")


def _base(h, slot):
    return float(h.get("open")) if (slot == "auction" and h.get("open")) else float(h["price"])


def _buy_time(slot):
    return {"auction": "9:26竞价结束后", "tail": "14:40后现价",
            "close": "次日开盘买入"}.get(slot, "现价")


def _common(h, slot, date_str, snap_map):
    sid = h["sid"]
    code = h["code"]
    base = _base(h, slot)
    s = snap_map.get(code, {})
    amt = float(s.get("amount") or 0)
    vr = float(s.get("vol_ratio") or 0)
    to = float(s.get("turnover") or 0)
    st = M.STRATEGIES[sid]["name"]
    reason = ("%s(%s)，%s 触发；板块=%s；成交 %.2f 亿、量比 %.2f、换手 %.1f%%；"
              "策略内第 %d 名。" % (h["name"], code, sid, _sector(h), amt / 1e8, vr, to, h["rank"]))
    return {
        "选股日期": int(date_str),
        "股票代码": _code_int(code),
        "股票名称": h["name"],
        "所属板块": _sector(h),
        "板块": (h.get("industry") or "未分类"),
        "选股时段": _slot_text(slot),
        "策略代号": sid,
        "策略依据": st,
        "选股价格": round(base, 2),
        "建议买入时机": _buy_time(slot),
        "槽位分配比例": "%.1f%%" % (100.0 / M.MAX_TOTAL),
        "槽位分配(比例)": "%.1f%%" % (100.0 / M.MAX_TOTAL),
        "计划持股时间": M.HOLD[sid],
        "持股时间": M.HOLD[sid],
        "计划止盈时间": "+%d%%" % M.TP[sid],
        "计划止损时间": "-%s%%" % ("%.1f" % M.SL[sid]).rstrip("0").rstrip("."),
        "卖出建议": "%s：+%d%% 止盈，-%s%% 止损"
                % (st, M.TP[sid], ("%.1f" % M.SL[sid]).rstrip("0").rstrip(".")),
        "策略内排名": "%s第%d名" % (st, h["rank"]),
        "推荐程度": M.RANK_TEXT.get(h["rank"], "谨慎推荐"),
        "推荐理由": reason,
        "来源": "Trae CN",
        "备注": "",
        "备注.1": "",
    }


def _heat(snap, industry):
    """板块热度：同行业平均涨幅、强势家数、板块规模。"""
    if not industry or snap.empty:
        return 0.0, 0, 0
    sub = snap[snap["industry"] == industry]
    if sub.empty:
        return 0.0, 0, 0
    pct = pd.to_numeric(sub["pct"], errors="coerce").fillna(0)
    return float(pct.mean()), int((pct >= 3).sum()), int(len(sub))


def _score(h, snap, snap_map):
    """优中选优打分：板块热度 + 资金强弱代理 + 量比。"""
    ind = h.get("industry") or ""
    avg, strong, size = _heat(snap, ind)
    s = snap_map.get(h["code"], {})
    amt = float(s.get("amount") or 0) / 1e8
    pct = float(s.get("pct") or 0)
    vr = float(s.get("vol_ratio") or 0)
    flow = amt * (pct / 100.0)          # 亿元·涨幅，正负代表资金方向
    return 0.6 * avg + 0.2 * pct + 1.0 * flow + 0.3 * vr, (avg, strong, size, flow)


def _write_rows(ws, rows, log):
    """按表头列名写入：优先填到表格已有空白行，避免撑破预置格式。"""
    header = [c.value for c in ws[1]]
    start = 2
    for r in ws.iter_rows(min_row=2, max_col=min(6, ws.max_column)):
        if any(c.value not in (None, "") for c in r):
            start = r[0].row + 1
    for i, data in enumerate(rows):
        vals = [data.get(str(h).strip(), None) if h is not None else None for h in header]
        for j, v in enumerate(vals, start=1):
            ws.cell(row=start + i, column=j).value = v
    if rows:
        log("  %s +%d 行（自第 %d 行）" % (ws.title, len(rows), start))
    return len(rows)


def _existing(ws):
    """返回已有 (日期, 时段, 代码) 集合，用于去重。"""
    header = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
    try:
        i_d = header.index("选股日期")
        i_s = header.index("选股时段")
        i_c = header.index("股票代码")
    except ValueError:
        return set()
    out = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[i_d] is None:
            continue
        out.add((str(row[i_d]), str(row[i_s] or ""), str(row[i_c])))
    return out


def _hist_index(hist):
    return {code: {str(d).replace("-", ""): i for i, d in enumerate(h["date"])}
            for code, h in hist.items()}


def fill_returns(wb, hist, log=print):
    """用本地日线回算各表“第 N 日收盘涨幅”。"""
    idx = _hist_index(hist)
    total = 0
    for name in (SHEET_DAILY, SHEET_PICK, SHEET_BEST, SHEET_ANALYSIS):
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        header = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
        try:
            i_d, i_c, i_p = (header.index("选股日期"), header.index("股票代码"),
                             header.index("选股价格"))
        except ValueError:
            continue
        cols = [(n, header.index(c)) for n, c in RET_COLS if c in header]
        for row in ws.iter_rows(min_row=2, values_only=False):
            d = row[i_d].value
            code = str(row[i_c].value).split(".")[0].zfill(6)
            price = row[i_p].value
            if d is None or not price or code not in idx:
                continue
            ds = str(int(d)) if isinstance(d, (int, float)) else str(d).replace("-", "")
            if ds not in idx[code]:
                continue
            i0 = idx[code][ds]
            closes = hist[code]["close"]
            for n, ci in cols:
                if i0 + n >= len(closes):
                    continue
                cell = row[ci]
                if cell.value not in (None, ""):
                    continue
                cell.value = float(closes[i0 + n] / float(price) - 1)
                cell.number_format = "0.00%"
                total += 1
    if total:
        log("  回算涨幅 %d 个单元格" % total)


def build_payload(slot, picked, snap, now=None):
    """构造待回填数据；云端 --json 与本地 write 共用同一份结构。"""
    now = now or M.now_cn()
    date_str = now.strftime("%Y%m%d")
    snap_map = snap.set_index("code").to_dict("index") if not snap.empty else {}

    common = {h["code"]: _common(h, slot, date_str, snap_map) for h in picked}
    pool = [h for h in picked if h["rank"] <= 2]   # 策略前 2 进入精选池
    best = None
    if pool:
        scored = [(_score(h, snap, snap_map), h) for h in pool]
        scored.sort(key=lambda x: x[0][0], reverse=True)
        best = scored[0][1]
        avg, strong, size, flow = scored[0][0][1]
        ind = best.get("industry") or "未分类"
        note = ("板块热度：%s 均涨 %.2f%%，强势 %d/%d 家；资金代理 %.2f 亿·涨幅"
                % (ind, avg, strong, size, flow))
        if len(scored) > 1:
            note += "；精选池共 %d 只，按热度+资金+量比取最优" % len(scored)
        common[best["code"]]["备注"] = note

    best_code = best["code"] if best else ""
    analysis = []
    for h in picked:
        r = dict(common[h["code"]])
        sid = h["sid"]
        r["策略止盈时间"] = "+%d%%" % M.TP[sid]
        r["策略卖出价格"] = round(_base(h, slot) * (1 + M.TP[sid] / 100.0), 2)
        r["按策略止盈涨幅"] = M.TP[sid] / 100.0
        r["是否在精选池"] = "是" if h["rank"] <= 2 else "否"
        r["是否在优中选优"] = "是" if h["code"] == best_code else "否"
        analysis.append(r)

    return {"date": date_str, "slot": slot, "sheets": {
        SHEET_DAILY: [common[h["code"]] for h in picked],
        SHEET_PICK: [common[h["code"]] for h in pool],
        SHEET_BEST: [common[best["code"]]] if best else [],
        SHEET_ANALYSIS: analysis,
    }}


def _apply(wb, payload, log):
    date_str = str(payload.get("date"))
    slot = payload.get("slot")
    n = 0
    for name in (SHEET_DAILY, SHEET_PICK, SHEET_BEST, SHEET_ANALYSIS):
        rows = (payload.get("sheets") or {}).get(name) or []
        if name not in wb.sheetnames:
            if name != SHEET_ANALYSIS:
                continue
            ws = wb.create_sheet(name, index=wb.sheetnames.index(SHEET_DAILY) + 1)
            ws.append(COL_ANALYSIS)
            log("  新建工作表：%s" % name)
        ws = wb[name]
        seen = _existing(ws)
        keep = [r for r in rows
                if (date_str, _slot_text(slot), str(r.get("股票代码"))) not in seen]
        n += _write_rows(ws, keep, log)
    return n


def _save(wb):
    tmp = LOG_PATH + ".tmp"
    for i in range(3):
        try:
            wb.save(tmp)
            os.replace(tmp, LOG_PATH)
            return
        except PermissionError:
            if i == 2:
                raise RuntimeError("表格被占用，请先在 Excel 中关闭 %s" % LOG_PATH)
            time.sleep(2)


def write(slot, picked, snap, hist, now=None, log=print):
    """回填一次选股结果，返回追加行数。"""
    from openpyxl import load_workbook
    if not os.path.exists(LOG_PATH):
        raise RuntimeError("找不到表格：%s" % LOG_PATH)
    wb = load_workbook(LOG_PATH)
    n = _apply(wb, build_payload(slot, picked, snap, now), log)
    fill_returns(wb, hist, log)
    _save(wb)
    log("Excel 回填完成：%s（%d 行）" % (LOG_PATH, n))
    return n


def write_payload(payload, log=print):
    """把云端产出的 JSON 结果回填到表格（自动去重）。"""
    from openpyxl import load_workbook
    if not os.path.exists(LOG_PATH):
        raise RuntimeError("找不到表格：%s" % LOG_PATH)
    wb = load_workbook(LOG_PATH)
    n = _apply(wb, payload, log)
    _save(wb)
    if n:
        log("云端结果回填：%s %s 时段 %s，%d 行"
            % (payload.get("date"), payload.get("slot"), LOG_PATH, n))
    return n
