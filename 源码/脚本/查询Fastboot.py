"""中文入口：校验 RAM 程序身份并查询版本／分区表。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from 核心.设备查询 import main

if __name__ == '__main__':
    main()
