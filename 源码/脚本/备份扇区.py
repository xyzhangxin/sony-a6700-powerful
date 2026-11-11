"""中文入口：指定范围原始扇区备份，可进行双读比较。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from 核心.扇区备份 import main

if __name__ == '__main__':
    main()
