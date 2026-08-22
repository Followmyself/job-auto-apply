#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次使用引导：交互式生成 config.json

询问：简历路径、投递平台、目标城市、薪资门槛、跳过词、搜索关键词、个人背景简介等。
生成 config.json（不入库，不上传 GitHub）。
用法：python guide.py
"""
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG = SCRIPT_DIR / "config.json"

DEFAULT_SKIP = [
    "销售", "Sales", "客服", "业务员", "电销", "陌拜", "地推", "推销",
    "客户代表", "招商经理", "招聘顾问", "猎头", "业务拓展", "BD", "客户成功",
    "总监", "VP", "首席", "负责人", "C-Level", "CEO", "CTO", "总裁",
    "合伙人", "专家岗", "校长", "总经理", "副总",
]
DEFAULT_BOSS_PROFILE = "./runtime/boss-profile"
DEFAULT_JOB51_PROFILE = "./runtime/job51-profile"
PRESERVED_USER_FILTERS = ("negative_title_words", "match_title_words", "industry_words")


def ask(prompt, default=None):
    if default is not None:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    val = input(prompt).strip()
    return val if val else (default if default is not None else "")


def ask_list(prompt, default=None):
    raw = ask(prompt, default)
    return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]


def main():
    print("=" * 50)
    print("Job Auto Apply - 首次使用配置引导")
    print("（所有信息只保存在本机 config.json，不会上传）")
    print("=" * 50)

    preserved = {}
    if CONFIG.exists():
        try:
            old_cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"已有 config.json 无法解析，未覆盖：{e}")
            sys.exit(1)
        old_user = old_cfg.get("user", {})
        preserved = {key: old_user[key] for key in PRESERVED_USER_FILTERS if key in old_user}

    cfg = {}

    # 用户信息
    name = ask("你的姓名（用于日志/报表）", "Your Name")
    resume = ask("附件简历 PDF 完整路径")
    if not resume or not Path(resume).exists():
        print("警告：简历路径不存在或未填写，BOSS 投递时将被拒绝，请先修正 config.json")
    bg = ask("一句话介绍你的背景（用于自动回复模板，如：AI应用开发方向，主攻Python、Agent、RAG）",
             "AI应用开发方向，主攻Python、Agent、RAG")
    stack = ask("核心技术栈（逗号分隔）", "Python, Agent, RAG, .NET")
    projects = ask("代表性项目（逗号分隔）", "AutoCAD智能绘图Agent, 本地RAG知识库")

    # 投递平台
    platforms = ask("投递平台（boss/job51/两者）", "boss")
    if platforms not in ("boss", "job51", "两者", "both"):
        platforms = "boss"

    # 筛选条件
    salary_k = ask("薪资门槛：月薪上限不低于多少千元才投（如 10 = 1万元）", "10")
    try:
        salary_k = int(salary_k)
    except ValueError:
        salary_k = 10
    cities = ask_list("城市优先级（逗号分隔，靠前的优先）", "长沙,武汉,广州,北京,上海,深圳")
    skip = ask_list("职位名含以下词一律不投（逗号分隔，回车用默认）",
                    ",".join(DEFAULT_SKIP))
    if not skip:
        skip = DEFAULT_SKIP
    queries = ask_list("搜索关键词（逗号分隔，多关键词轮换）", "AI应用,AI开发,Agent,RAG,大模型应用,Python,全栈")

    # 浏览器端口（默认即可）
    boss_port = ask("BOSS 直聘 Chrome CDP 端口（独立实例，防反自动化）", "9233")
    job51_port = ask("51job Chrome CDP 端口", "9232")
    try:
        boss_port = int(boss_port)
        job51_port = int(job51_port)
    except ValueError:
        boss_port, job51_port = 9233, 9232

    # 回复模板
    print("\n聊天自动回复模板（可在 config.json 中修改）：")
    t_intro = ask("通用回复（{user_bg} 会被替换）",
                  "您好！感谢您的回复。我的背景是{user_bg}，有多个0到1交付的项目经验。请问方便介绍一下岗位的具体情况和团队吗？期待进一步沟通！")
    t_sales = ask("被问销售经验时回复",
                  "您好！我过往没有销售相关的工作经验。我的背景是{user_bg}，如果贵司有技术方向的岗位需求，我可以详细介绍一下项目经验。")
    t_exp = ask("被问技术/经验时回复",
                "您好！我是{user_bg}方向，主攻{user_stack}，有{user_projects}等完整项目经验，可以把技术落地到实际业务场景。期待进一步沟通！")

    cfg = {
        "user": {
            "name": name,
            "pdf_resume": resume.replace("\\", "/"),
            "background": bg,
            "tech_stack": stack,
            "projects": projects,
            "target_salary_k": salary_k,
            "cities_priority": cities,
            "skip_title_words": skip,
            "queries": queries,
            **preserved,
        },
        "boss": {
            "port": boss_port,
            "profile_dir": DEFAULT_BOSS_PROFILE,
            "chat_poll_interval": 90,
            "chat_reply_templates": {
                "intro": t_intro,
                "sales_question": t_sales,
                "experience_question": t_exp,
            },
        },
        "job51": {
            "port": job51_port,
            "profile_dir": DEFAULT_JOB51_PROFILE,
        },
        "logs_dir": str(SCRIPT_DIR.parent / "logs").replace("\\", "/"),
        "report_dir": str(SCRIPT_DIR.parent / "reports").replace("\\", "/"),
    }

    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n配置已保存到: {CONFIG}")
    print("下一步：")
    print("  1. python scripts/launch_browsers.py   启动浏览器")
    print("  2. python scripts/boss_apply.py --target 100   开始投递")
    print("  3. python scripts/boss_chat_monitor.py   常驻监控聊天自动回复")
    print("  4. python scripts/daily_report.py   生成每日报表")


if __name__ == "__main__":
    main()
