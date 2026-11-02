# 拉取云端（GitHub Actions）提交到 data/results 的选股结果
# 用 Python 调 git，避免 PowerShell 变量在部分环境下取值为空的问题
import os
import subprocess

GIT = r"D:\Program Files\Git\cmd\git.exe"
ROOT = os.path.dirname(os.path.abspath(__file__))


def _git(*args):
    try:
        p = subprocess.run([GIT] + list(args), cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        out = (p.stdout or "").strip() + (p.stderr or "").strip()
        return p.returncode, out
    except Exception as e:
        return 1, "git 调用失败：%s" % e


def main():
    if not os.path.exists(GIT):
        print("未找到 git：%s，跳过云端结果拉取" % GIT)
        return 0
    rc, out = _git("fetch", "ssh", "--quiet")
    if out:
        print("fetch:", out)
    rc2, out2 = _git("merge", "--ff-only", "ssh/main", "--quiet")
    if out2:
        print("merge:", out2)
    if rc or rc2:
        print("云端结果拉取失败（不影响本地选股）：rc=%s/%s" % (rc, rc2))
    return 0


if __name__ == "__main__":
    main()
