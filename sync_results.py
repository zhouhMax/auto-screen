"""把云端（GitHub Actions）提交到 data/results 的选股结果回填到本地 Excel。

配合 local_run.ps1：先 git pull，再执行本脚本，最后跑本地选股。
重复运行安全：按（日期, 时段, 代码）去重，已存在的行不会重复写入。

2026-11-10 起加“先到者为准”：云端与本机可能在同一日同一时段各选出一份不同的名单
（快照时刻不同）。若表格里该日该时段已经有行，且这份 JSON 的名单不完全是已有行的
子集，就整份跳过并记警告——不把第二份名单塞进同一天同一时段。
"""
import glob
import json
import os

import excel_log

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "data", "results")


def existing_codes(date_str, slot, log=print):
    """表格“每日选股”里该日该时段已有的股票代码集合。"""
    from openpyxl import load_workbook
    if not os.path.exists(excel_log.LOG_PATH):
        return set()
    wb = load_workbook(excel_log.LOG_PATH, read_only=True)
    try:
        if excel_log.SHEET_DAILY not in wb.sheetnames:
            return set()
        ws = wb[excel_log.SHEET_DAILY]
        rows = ws.iter_rows(values_only=True)
        header = [str(c).strip() if c is not None else "" for c in next(rows, [])]
        try:
            i_d = header.index("选股日期")
            i_s = header.index("选股时段")
            i_c = header.index("股票代码")
        except ValueError:
            return set()
        slot_text = excel_log._slot_text(slot)
        out = set()
        for row in rows:
            if row[i_d] is None:
                continue
            if str(row[i_d]).replace("-", "") != str(date_str):
                continue
            if str(row[i_s] or "") != slot_text:
                continue
            out.add(excel_log._code_text(row[i_c]))
        return out
    finally:
        wb.close()


def sync(log=print, out_dir=RESULT_DIR):
    files = sorted(glob.glob(os.path.join(out_dir, "*.json")))
    if not files:
        return 0
    total = 0
    for path in files:
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                log("跳过 %s：不是对象" % name)
                continue
            if payload.get("status", "ok") != "ok":   # 老文件没有 status，按完整处理
                log("跳过 %s：status=%s，不是完整结果" % (name, payload.get("status")))
                continue
            if not isinstance((payload.get("sheets") or {}).get(excel_log.SHEET_DAILY), list):
                log("跳过 %s：缺少每日选股列表" % name)
                continue
            date_str = str(payload.get("date"))
            slot = payload.get("slot")
            codes = {excel_log._code_text(r.get("股票代码"))
                     for r in (payload.get("sheets") or {}).get(excel_log.SHEET_DAILY) or []}
            have = existing_codes(date_str, slot, log)
            if have and not codes <= have:
                log("跳过 %s：%s %s 时段已有 %d 只（%s），本文件是后到的另一份名单"
                    "（%d 只，%s），按“先到者为准”整份不写入"
                    % (name, date_str, excel_log._slot_text(slot), len(have),
                       ",".join(sorted(have)), len(codes), ",".join(sorted(codes))))
                continue
            n = excel_log.write_payload(payload, log)
            if n:
                log("  来源：%s" % name)
            total += n
        except Exception as e:
            log("跳过 %s：%s" % (name, e))
    return total


def main():
    n = sync()
    print("云端结果同步完成，新增 %d 行" % n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
