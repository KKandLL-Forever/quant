"""
daily_2lb.py — 每日 2板→3板 选股一键流水线

按顺序执行两步，任一步失败立即中止并打印明确错误：
  1) cache_tushare.py --update        更新当日数据，并在末尾自动重建 market_state
  2) ml_score_2lb_v3.py               用最新数据打分（含集合竞价），产出 HTML

用法：
  python first10/daily_2lb.py                 # 完整两步
  python first10/daily_2lb.py --top 20        # 打分展示 top 20
  python first10/daily_2lb.py --skip-update   # 跳过更新（数据已是最新时复盘用）
  python first10/daily_2lb.py --date 20260527 # 指定历史日期打分（自动跳过更新）
  python first10/daily_2lb.py --force         # 非交易日也强制运行

非交易日（周末/法定节假日）自动静默退出（exit 0）；--date 复盘或 --force 不受此限制。

退出码：0 全部成功（含非交易日跳过）；非 0 表示某步失败（已打印是哪一步）。
建议每天 18:00 后运行（Tushare 涨停板数据当晚才完整）。
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import date

from chinese_calendar import is_holiday

sys.stdout.reconfigure(encoding="utf-8")

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRST10    = os.path.join(PROJ_ROOT, "first10")
PY         = sys.executable


def _run(label: str, args: list) -> None:
    """在项目根目录下跑一步子进程，失败则抛 SystemExit 并打印明确定位。"""
    print(f"\n{'='*60}\n[{label}] {' '.join(args)}\n{'='*60}", flush=True)
    t = time.time()
    r = subprocess.run([PY] + args, cwd=PROJ_ROOT)
    if r.returncode != 0:
        print(f"\n❌ 步骤失败：[{label}]（退出码 {r.returncode}）。后续步骤已中止。",
              flush=True)
        raise SystemExit(r.returncode)
    print(f"✅ [{label}] 完成（{time.time()-t:.1f}s）", flush=True)


def is_trading_day(d: date) -> bool:
    """判断是否为 A 股交易日：周一至周五且非法定节假日。"""
    return d.weekday() < 5 and not is_holiday(d)


def main():
    """流水线入口：更新 → 重建市场状态 → 打分。"""
    p = argparse.ArgumentParser(description="每日 2板→3板 选股一键流水线")
    p.add_argument("--top", type=int, default=15, help="打分展示 top N（默认 15）")
    p.add_argument("--date", default=None, help="指定打分日期 YYYYMMDD（隐含 --skip-update）")
    p.add_argument("--skip-update", action="store_true", help="跳过数据更新与市场状态重建")
    p.add_argument("--force", action="store_true", help="非交易日也强制运行")
    args = p.parse_args()

    if args.date is None and not args.force and not is_trading_day(date.today()):
        print("⚠️  今日非 A 股交易日，已跳过。指定 --date 复盘或 --force 强制运行。",
              flush=True)
        sys.exit(0)

    t0 = time.time()
    skip_update = args.skip_update or (args.date is not None)

    if skip_update:
        print("⚠️  已跳过数据更新与 market_state 重建（复盘/指定日期模式）。"
              "若库非最新，打分结果将基于旧数据。", flush=True)
    else:
        _run("1/2 更新数据（含 market_state 重建）",
             [os.path.join(FIRST10, "cache_tushare.py"), "--update"])

    score_args = [os.path.join(FIRST10, "ml_score_2lb_v3.py"), "--top", str(args.top)]
    if args.date:
        score_args += ["--date", args.date]
    _run("2/2 打分", score_args)

    print(f"\n🎉 全部完成，总耗时 {time.time()-t0:.1f}s。", flush=True)


if __name__ == "__main__":
    main()
