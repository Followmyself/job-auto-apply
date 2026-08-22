#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日求职投递报表生成器（配置驱动）

读取投递/聊天日志，按日期汇总生成 Markdown 报表。
用法：python daily_report.py [YYYY-MM-DD]（缺省=今天）
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from common import load_config, resolve


def read_lines(path: Path):
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise RuntimeError(f"日志读取失败：{path} ({e})") from e


def make_report(cfg, date_str: str) -> str:
    logs_dir = Path(resolve(cfg.get("logs_dir", "logs")))
    report_dir = Path(resolve(cfg.get("report_dir", "reports")))
    report_dir.mkdir(parents=True, exist_ok=True)
    user_name = cfg["user"].get("name", "")

    boss_log = logs_dir / "boss投递日志.md"
    boss_chat_log = logs_dir / "boss聊天日志.md"

    out = [f"# 求职投递日报 {date_str}", "",
           f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]

    # BOSS 投递
    boss_lines = read_lines(boss_log)
    succ, skip_sal, skip_title, other, sals = [], [], [], [], []
    for l in boss_lines:
        if date_str not in l:
            continue
        if "投递成功" in l:
            succ.append(l)
            m = re.search(r"sal=([\d.]+-[\d.]+K)", l)
            if m:
                sals.append(m.group(1))
        elif "salary<" in l:
            skip_sal.append(l)
        elif "skip-title" in l:
            skip_title.append(l)
        elif "[跳过]" in l:
            other.append(l)

    out += ["## 一、BOSS 直聘", "",
            f"- **投递成功：{len(succ)} 份**",
            f"- 薪资不足门槛跳过：{len(skip_sal)} 个",
            f"- 销售/客服/高管等不匹配跳过：{len(skip_title)} 个",
            f"- 其他跳过：{len(other)} 个"]
    if sals:
        out.append(f"- 成功投递薪资区间（部分示例）：{'、'.join(sals[:12])}")
    out.append("")
    if succ:
        out += ["### 成功投递明细（前 30 条）", "", "| 时间 | 职位 | 结果 |", "| --- | --- | --- |"]
        for l in succ[-30:]:
            m = re.match(r"\[([\d-]+ [\d:]+)\] {0,2}\[投递成功 \d+/\d+\] ([^|]+) \| (.*)", l)
            if m:
                out.append(f"| {m.group(1)} | {m.group(2).strip()} | {m.group(3).strip()} |")
        out.append("")

    # 聊天
    chat_lines = read_lines(boss_chat_log)
    actions = [l for l in chat_lines if date_str in l and any(k in l for k in ("自动回复", "点击同意", "点击可以接受"))]
    out += ["## 二、BOSS 聊天互动", "", f"- **自动处理交互：{len(actions)} 次**", ""]
    if actions:
        out += ["| 时间 | 动作 |", "| --- | --- |"]
        for l in actions[-20:]:
            m = re.match(r"\[([\d-]+ [\d:]+)\] (.+)", l)
            if m:
                out.append(f"| {m.group(1)} | {m.group(2)} |")
        out.append("")

    # 51job
    job51_log = logs_dir / "51job投递日志.md"
    j51 = read_lines(job51_log)
    j51_today = [l for l in j51 if date_str in l]
    out += ["## 三、前程无忧(51job)", ""]
    if j51_today:
        succ51 = len([l for l in j51_today if "已申请" in l or "投递成功" in l])
        out.append(f"- **投递成功：{succ51} 份**（当日日志 {len(j51_today)} 条）")
    else:
        out.append("- 无当日记录")
    out.append("")

    out += ["---", f"*由 job-auto-apply 自动生成：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*", ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"日期格式错误: {args.date}，应为 YYYY-MM-DD")
        sys.exit(1)
    cfg = load_config()
    report = make_report(cfg, args.date)
    report_dir = Path(resolve(cfg.get("report_dir", "reports")))
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / f"{args.date}.md"
    target.write_text(report, encoding="utf-8")
    print(f"报表已生成: {target}")


if __name__ == "__main__":
    main()
