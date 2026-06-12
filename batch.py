"""批量回测指定股票"""

import sys
import os

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))

import config
from main import run_single_stock

# 要测试的股票列表
STOCKS = [
    "000421.SZ.csv",  # 南京公用
    "000553.SZ.csv",  # 安道麦A
    "000521.SZ.csv",  # 长虹美菱
    "002454.SZ.csv",  # 松芝股份
]

if __name__ == "__main__":
    for stock in STOCKS:
        filepath = os.path.join(config.DATA_DIR, stock)
        if not os.path.exists(filepath):
            print(f"\n文件不存在，跳过: {stock}")
            continue
        try:
            run_single_stock(filepath)
        except Exception as e:
            print(f"\n{stock} 回测出错: {e}")
