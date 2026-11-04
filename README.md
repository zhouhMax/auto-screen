# 每日分时段选股 · WxPusher 推送

把金太阳专业版的 9 套选股公式（J1-J3 / W1-W3 / P1-P3）用 Python 重写，跑在 GitHub Actions 上，
每天 09:25 / 14:40 / 15:40 自动选股并通过 WxPusher 推送到微信。

不需要本机开机，不依赖券商客户端，全程免费额度内运行。

## 架构

```
GitHub Actions (每晚 23:10)
  └─ update_history.yml
       ├─ 抓全市场快照（代码/名称/流通股本/流通市值）
       ├─ 逐股抓 160 根日线 → data/hist.parquet
       └─ 存进 Actions Cache（key: hist-*）

GitHub Actions (09:15 / 14:35 / 15:35 触发)
  └─ screen.yml
       ├─ 恢复前一晚的 hist.parquet 缓存
       ├─ 进程内 sleep 到 09:25:05 / 14:40:30 / 15:40:30
       ├─ 只发 1 次全市场快照请求（不做逐股 K 线，保证秒级出结果）
       ├─ 9 套策略逐只打分 → 排序 → 截断
       └─ WxPusher 推送 Markdown 消息
```

关键设计：**盘中只请求 1 次快照**。9:25 窗口内逐股拉 K 线（5000+ 次）必然超时，
所以把日线全部前移到夜间缓存，盘中只取“今天这一笔”的开高低/量额/量比/换手/流通市值。

## 目录

| 文件 | 作用 |
|---|---|
| `fetcher.py` | 行情抓取：东财快照/日K（HTTP 优先）、新浪全市场列表、新浪日线兜底 |
| `strategies.py` | 9 套策略的 Python 实现，与 `.txt` 公式逐条对应 |
| `main.py` | 主程序：等待时点、抓数、选股、组装消息、推送 |
| `boards.py` | 从本地通达信目录解析行业/概念，生成并读取 `boards.csv` |
| `boards.csv` | 板块映射缓存（code,industry,concept1,concept2），需随仓库提交 |
| `.github/workflows/update_history.yml` | 夜间缓存任务 |
| `.github/workflows/screen.yml` | 三个时段推送任务 |

## 推送样式

标题固定为 `【Trae CN】mmdd早盘/尾盘/盘后选股`（例：`【Trae CN】1104早盘选股`，由 `main.push_title()` 统一生成），
写入 WxPusher 的 `summary` 字段（手机通知栏显示）；
正文本体不再重复标题，直接从“信号 时间｜扫描/候选/输出…”开始。2026-11-02 起每次最多输出 **9 只**，
每策略 ≤3 只、不限板块（策略前 3 名进入 Excel 的“精选池”，优中选优优先在排名 2、3 中挑选），
按“策略优先级 → 策略内排名”排序后截断。每只股票包含：

- 名称与代码、所属板块/概念（本地通达信行业 + 前 2 个概念，见 `boards.csv`；均缺失显示“未分类”）
- 命中策略名称与策略内排名（第 1 名=强烈推荐、第 2 名=推荐、第 3 名=谨慎推荐）
- 核心逻辑（策略条件摘要）
- 可成交买入区间（竞价/尾盘/盘后三档系数不同）
- 止盈价、止损价（按策略预设百分比）
- 持有期

## 云端结果回填（仓库中转）

云端无法直接写本地 Excel，因此走仓库中转：

1. 云端运行 `python main.py --slot auto --json-out`，生成 `data/results/<日期>_<时段>.json`
2. 同一工作流的“提交结果到仓库”步骤把该 JSON 提交回 `main` 分支（需 `contents: write`）
3. 本地 `local_run.ps1` 每次启动先 `git fetch ssh` + `git merge --ff-only ssh/main`，再执行
   `python sync_results.py`，把新 JSON 逐条回填到 `选股池-TR.xlsx`
4. 回填按“日期 + 时段 + 股票代码”去重，重复执行不会产生重复行；云端与本地同一时段的结果也不会互相叠加

手动补一次云端历史结果：

```bash
python sync_results.py
```

`data/results/*.json` 已在 `.gitignore` 中单独放行，其余 `data/*`（parquet、缓存）不提交。

## 部署步骤（GitHub Actions）

1. 新建一个**私有**仓库（private 更稳妥，代码与选股结果不外泄）。
2. 把 `auto_screen` 目录内容推到仓库根目录（保留 `.github/workflows/`）。
   ```bash
   cd auto_screen
   git init && git add . && git commit -m "init auto screen"
   git remote add origin git@github.com:<你的账号>/<仓库名>.git
   git push -u origin main
   ```
3. 仓库 → Settings → Secrets and variables → Actions，新增：
   - `WXPUSHER_APP_TOKEN`：WxPusher 应用 token（必填）
   - `WXPUSHER_UIDS`：接收者 UID，多个用英文逗号分隔（必填）
   - `WXPUSHER_TOPIC_IDS`：主题 ID（可选，填了会同时推给主题订阅者）
4. 仓库 → Actions → 选中 `更新历史数据` → Run workflow 跑一次（**必须先跑**，否则盘中任务没有日线数据）。
5. 回到 Actions，选中 `每日选股推送` → Run workflow，slot 选 `close`，验证能收到消息。
6. 之后按 cron 自动运行。

## 本地运行

```bash
pip install -r requirements.txt

# 1) 先建缓存（首次全市场约 2-8 分钟；--max-codes 用于快速调试）
python main.py --slot update-history
python main.py --slot update-history --max-codes 400

# 2) 干跑，只打印不推送（--no-wait 不等到目标时点）
python main.py --slot close --dry-run --no-wait
python main.py --slot auction --dry-run --no-wait
python main.py --slot tail --dry-run --no-wait

# 3) 真实推送（需先设置环境变量）
set WXPUSHER_APP_TOKEN=AT_xxxx
set WXPUSHER_UIDS=UID_xxxx
python main.py --slot close --no-wait
```

`--slot auto`（默认）按当前时间自动判断时段：8:55-11:30 竞价、13:00-15:00 尾盘、其余盘后。

## 板块数据（本地通达信）

行业与概念来自本机国信金太阳专业版（通达信内核）数据目录 `D:\zd_gxzq`：

| 文件 | 内容 |
|---|---|
| `T0002/hq_cache/tdxhy.cfg` | 股票 → 行业代码（`市场\|代码\|T行业码\|\|\|X概念码`，5666 行） |
| `T0002/hq_cache/tdxzs*.cfg` | 行业代码 → 行业名称（如 `银行\|880471\|2\|1\|1\|T1001`） |
| `T0002/hq_cache/infoharbor_block.dat` | 概念板块成分（文本，269 个 `GN_` 概念，共 5.4k 只股票有概念） |

`python boards.py` 解析上述文件生成 `boards.csv`：

- `industry`：通达信行业（优先于东财 `f100`，本地缺失时回退 `f100`）
- `concept1/2`：按板块成分数升序取前 2 个（越具体越靠前），并剔除 `ST板块`、`次新股`、`融资融券` 等非主题板块

注意：**GitHub Actions 上没有 `D:` 盘**，所以必须把 `boards.csv` 一起提交到仓库（`boards.py` 在无本地目录时静默降级为空）。客户端板块数据更新后，本机重跑 `python boards.py` 再提交即可。

## WxPusher 配置

1. 打开 <https://wxpusher.zjiecode.com>，微信扫码登录。
2. 「应用管理」→ 新建应用 → 复制 **appToken**（形如 `AT_xxx`）。
3. 用微信关注该应用的二维码，在「用户管理」里能看到自己的 **UID**（形如 `UID_xxx`）。
4. token 只放 GitHub Secrets，不要写进代码或提交到仓库。

## 定时说明（重要）

- GitHub cron 只支持 UTC，北京时间 = UTC+8。
- cron 触发本身有 1-15 分钟随机延迟，所以配置为**提前 10 分钟触发**，
  脚本内 `sleep` 到 09:25:05 / 14:40:30 / 15:40:30 再抓数据。
- 若触发晚于目标时点超过 120 秒，消息里会标注 **延迟启动**，结果价可能已偏离信号价。
- 9:25 竞价时点对延迟最敏感（集合竞价撮合价只在那一瞬间有效），这是本方案唯一无法完全消除的风险点；
  14:40 与 15:40 受影响很小。
- GitHub 免费额度：私有仓库每月 2000 分钟。本方案每天约 1 次夜跑（5-10 分钟）+ 3 次盘中（1-2 分钟），
  月均 200-300 分钟，够用。**仓库连续 60 天无提交活动，定时任务会被自动停用**，
  建议每两个月手动改一次 README 或点一次 Run workflow 保活。

## 数据源

- 东方财富 `push2`（快照）/ `push2his`（日K）：HTTP 优先，HTTPS 兜底。本机实测 HTTPS 被中断，HTTP 正常。
- 东方财富不可用时自动切换 **新浪全市场列表**（`Market_Center.getHQNodeData`，分页 100 条并发）。
  该源含价格/涨跌幅/换手率/流通市值，**缺量比**，量比由 `成交量 / (5日均量/240×已交易分钟)` 估算。
- 日K 兜底：新浪 `CN_MarketData.getKLineData`（无成交额，按 `收盘价×成交量` 估算）。

以上接口均为公开行情接口，仅用于个人研究，请勿高频抓取或商用。夜跑已限速（8 线程 + 重试退避）。

## 与金太阳公式的差异

| 指标 | 金太阳 | 本方案 |
|---|---|---|
| `DYNAINFO(17)` 量比 | 客户端实时计算 | 快照字段；缺失时按 5 日均量估算 |
| `DYNAINFO(37)` 换手 | 客户端实时计算 | 快照字段；缺失时按流通股本估算 |
| `FINANCE(40)` 流通市值 | 客户端财务数据 | 快照流通市值，兜底用夜间缓存的流通股本 × 现价 |
| 涨停幅度 | 按板块判定 | 同左（创业板/科创 20%、北交所 30%、ST 5%、主板 10%） |

阈值、排序、截断规则与 `.txt` 公式完全一致（见 `strategies.py` 顶部注释与 `STRATEGIES` 字典）。

## 常见问题

- **某时段 0 只**：严格阈值下正常，不要把消息当“必须出票”。
- **提示历史数据过期**：说明夜跑失败或缓存丢失，手动跑一次 `update-history`。
- **缓存丢失**：Actions Cache 7 天未命中会自动清理；夜跑每天写新 key，正常情况下盘中总能命中。
- **推送失败**：检查 token/UID 是否正确、微信是否已关注应用；脚本会重试 3 次并打印返回码。
- **东财接口失败**：日志会出现“切换新浪全市场列表源”，属预期兜底行为。

> 本项目为量化研究工具，输出不构成投资建议；回测收益不代表未来表现。
