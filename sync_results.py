"""把云端（GitHub Actions）提交到 data/results 的选股结果回填到本地 Excel。

配合 local_run.ps1：先 git pull，再执行本脚本，最后跑本地选股。
重复运行安全：按（日期, 时段, 代码）去重，已存在的行不会重复写入。

2026-11-10 改策略：本机与云端两份名单**都写进表**（不再整份跳过）。
同一只票两端都选到时只留一行，来源标成「TC 本机+云端」，价格等其余列保留先到那份。
"""
import glob
import json
import os

import excel_log

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "data", "results")


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
            if not payload.get("date") or not payload.get("slot"):
                log("跳过 %s：缺少日期或时段" % name)
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
