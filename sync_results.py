"""把云端（GitHub Actions）提交到 data/results 的选股结果回填到本地 Excel。

配合 local_run.ps1：先 git pull，再执行本脚本，最后跑本地选股。
重复运行安全：按（日期, 时段, 代码）去重，已存在的行不会重复写入。
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
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            n = excel_log.write_payload(payload, log)
            if n:
                log("  来源：%s" % os.path.basename(path))
            total += n
        except Exception as e:
            log("跳过 %s：%s" % (os.path.basename(path), e))
    return total


def main():
    n = sync()
    print("云端结果同步完成，新增 %d 行" % n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
