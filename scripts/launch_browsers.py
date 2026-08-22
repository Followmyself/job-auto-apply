#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动投递用 Chrome 实例（BOSS 独立实例 + 可选 51job 实例）

- BOSS 直聘：独立端口 + --disable-blink-features=AutomationControlled（否则页面被反自动化检测清空），调试器/MCP 不要连接该实例
- 51job：独立端口

用法：
  python launch_browsers.py            # 启动全部
  python launch_browsers.py --boss     # 只启动 BOSS
  python launch_browsers.py --job51    # 只启动 51job
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request

from common import load_config

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def port_listening(port: int, wait: int = 40) -> bool:
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2)
            return True
        except Exception:
            time.sleep(2)
    return False


def launch(port: int, profile_dir: str, url: str, extra_args=None):
    args = [CHROME, "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            f"--user-data-dir={profile_dir}",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*"]
    if extra_args:
        args.extend(extra_args)
    args.append(url)
    try:
        subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        print(f"启动 Chrome 失败: {e}")
        sys.exit(1)
    if port_listening(port):
        print(f"Chrome {port} 就绪")
    else:
        print(f"Chrome {port} 启动超时（如已运行则复用）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boss", action="store_true")
    ap.add_argument("--job51", action="store_true")
    args = ap.parse_args()
    cfg = load_config()

    do_boss = args.boss or not (args.job51)
    do_job51 = args.job51 or not (args.boss)

    if do_boss:
        b = cfg.get("boss", {})
        launch(int(b.get("port", 9233)),
               b.get("profile_dir", ""),
               "https://www.zhipin.com/web/geek/jobs")
    if do_job51:
        j = cfg.get("job51", {})
        launch(int(j.get("port", 9232)),
               j.get("profile_dir", ""),
               "https://we.51job.com/pc/search?keyword=AI")
    print("浏览器启动完成。投递前请确认对应站点已登录。")


if __name__ == "__main__":
    main()
