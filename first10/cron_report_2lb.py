"""
cron_report_2lb.py — 每日 2lb 流水线执行 + Top 1% 候选检测 + 摘要输出

供 Hermes cron (no_agent=True) 使用：
  退出码 0 + stdout 非空 → cron 发送飞书消息
  退出码 0 + stdout 为空 → cron 静默（不发消息）
  退出码非 0 → cron 自动告警

非交易日（周末/法定节假日）静默退出，不产生任何消息。

用法：
  python first10/cron_report_2lb.py
"""

import os
import re
import subprocess
import sys
import time
from datetime import datetime, date
from chinese_calendar import is_holiday

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRST10 = os.path.join(PROJ_ROOT, "first10")
DUCKDB_PATH = os.path.join(PROJ_ROOT, "stock_data_tushare.duckdb")


def _ensure_duckdb_unlocked(db_path: str) -> None:
    """检测 duckdb 是否被占用，若被占用则自动杀进程后重试。"""
    result = subprocess.run(
        [sys.executable, "-c",
         f"import duckdb; duckdb.connect(r'{db_path}').close()"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode == 0:
        return

    pid_match = re.search(r"PID (\d+)", result.stderr)
    if not pid_match:
        print(f"⚠️  duckdb 无法访问且未识别占用进程：{result.stderr[:300]}")
        return

    pid = pid_match.group(1)
    print(f"⚠️  duckdb 被 PID {pid} 占用，自动杀进程...")
    subprocess.run(
        ["powershell", "-Command", f"Stop-Process -Id {pid} -Force"],
        capture_output=True,
    )
    time.sleep(2)


def is_trading_day(d: date) -> bool:
    """判断是否为 A 股交易日：周一至周五且非法定节假日。"""
    if d.weekday() >= 5:
        return False
    if is_holiday(d):
        return False
    return True


def main():
    today_str = datetime.now().strftime("%Y%m%d")
    html_path = os.path.join(FIRST10, "html_output", f"ml_score_2lb_v3_{today_str}.html")

    if not is_trading_day(date.today()):
        sys.exit(0)

    _ensure_duckdb_unlocked(DUCKDB_PATH)

    result = subprocess.run(
        [sys.executable, os.path.join(FIRST10, "daily_2lb.py")],
        cwd=PROJ_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().split("\n")[-8:]
        print(f"每日选股流水线 **失败**（退出码 {result.returncode}）\n")
        print("最后输出：")
        for line in tail:
            print(f"  `{line}`")
        print("\n常见原因：Tushare / ClickHouse 网络超时，或 duckdb 文件被占用。可稍后手动重跑验证。")
        sys.exit(result.returncode)

    if not os.path.exists(html_path):
        print(f"流水线完成但未找到产出文件：`{html_path}`\n请检查 ml_score_2lb_v3.py 是否正常生成 HTML。")
        sys.exit(0)

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    date_match = re.search(r"交易日 (\d{8})", html)
    signal_match = re.search(r"(\d+) 板信号 (\d+) 个", html)
    regime_match = re.search(r"市场状态：[^\n<]*?([^<\n]+?)</div>", html)
    auc_match = re.search(r"AUC ([\d.]+)", html)
    top1_thresh = re.search(r"Top 1% proba≥<b>([\d.]+)</b>", html)
    top5_thresh = re.search(r"Top 5% proba≥<b>([\d.]+)</b>", html)
    board_match = re.search(r"最高连板</span><span class=.v.>(\d+)板</span>", html)

    date_str = date_match.group(1) if date_match else today_str
    signals = f"{signal_match.group(2)} 个" if signal_match else "?"
    regime = regime_match.group(1) if regime_match else "?"
    auc = auc_match.group(1) if auc_match else "?"
    max_board = board_match.group(1) if board_match else "?"

    print(f"**每日选股报告 {date_str}**\n")
    print(f"市场状态：{regime}")
    print(f"最高连板：{max_board}板 · 2板信号：{signals} · OOS AUC：{auc}")

    if top1_thresh and top5_thresh:
        print(f"Top 1% 阈值 proba≥{top1_thresh.group(1)} · Top 5% 阈值 proba≥{top5_thresh.group(1)}")

    table_match = re.search(r"候选 Top 表</h2>(.*?)(?:</table>|<h2>)", html, re.DOTALL)
    top1_candidates = []
    top5_candidates = []

    if table_match:
        rows = re.findall(r"<tr>(.*?)</tr>", table_match.group(1), re.DOTALL)
        for row in rows[1:]:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) < 5:
                continue
            code = re.sub(r"<[^>]+>", "", cells[1]).strip()
            name = re.sub(r"<[^>]+>", "", cells[2]).strip()
            proba = re.sub(r"<[^>]+>", "", cells[3]).strip()
            percentile = re.sub(r"<[^>]+>", "", cells[4]).strip()

            if "Top 1%" in percentile:
                top1_candidates.append((code, name, proba))
            elif "Top 5%" in percentile:
                top5_candidates.append((code, name, proba))

    if top1_candidates:
        print(f"\n**Top 1% 达标候选（{len(top1_candidates)} 个）**：")
        for code, name, proba in top1_candidates:
            print(f"  · {code} {name}（{proba}）")
    else:
        print("\n**今日无 Top 1% 达标候选**")

    if top5_candidates:
        print(f"\nTop 5% 候选（{len(top5_candidates)} 个）：")
        for code, name, proba in top5_candidates:
            print(f"  · {code} {name}（{proba}）")

    print(f"\n完整报告：`{html_path}`")


if __name__ == "__main__":
    main()
