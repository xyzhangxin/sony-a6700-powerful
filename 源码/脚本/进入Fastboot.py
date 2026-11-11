"""中文入口：离线校验、检查 Pico 或按提示冷启动注入。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from 核心.注入入口 import main

if __name__ == '__main__':
    main()
