#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共配置加载。所有脚本统一从这里读 config.json。"""
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"


def load_config():
    if not CONFIG_PATH.exists():
        print("缺少 config.json！请先运行：python scripts/guide.py 完成初始化配置")
        sys.exit(1)
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"config.json 解析失败: {e}")
        sys.exit(1)


def resolve(path: str) -> str:
    """将配置中的相对路径解析为绝对路径"""
    if not path:
        return path
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str((SCRIPT_DIR.parent / path).resolve())


def city_rank(text: str, cities: list[str]) -> int:
    """返回文本命中的城市优先级，未命中时排在所有配置城市之后。"""
    text = text or ""
    for index, city in enumerate(cities or []):
        if city and city in text:
            return index
    return len(cities or [])
