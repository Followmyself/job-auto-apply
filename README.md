# Job Auto Apply — 自动求职投递工作流

在 **BOSS 直聘** 与 **前程无忧(51job)** 上自动投递简历的完整工作流：
**筛选 → 投递 → 聊天自动回复 → 每日报表与日志**。

> ⚠️ 免责声明：本项目仅用于个人自动化学习。请遵守各招聘平台的服务条款，控制投递频率，因滥用导致的账号风控/封禁由使用者自行承担。

## 功能

- **自动投递**：BOSS 直聘（CDP 直连独立 Chrome，绕过反自动化检测）+ 前程无忧
- **智能筛选**（首次配置，全部可自定义）：
  - 薪资门槛（默认月薪上限 ≥ 1 万才投；自动解析年薪制，如 `6-8K·24薪` 换算月均 16K）
  - 城市优先级（就近城市优先）
  - 跳过词（默认跳过销售/客服/业务员/电销/地推/猎头/高管等非技术岗位）
  - 搜索关键词多轮换（避免深分页重复）
- **聊天自动回复**（BOSS）：HR 索要简历→自动发送、交换微信→同意、工作地点→接受、技术问题→按模板回复
- **防发错**：会话名双重校验；**防重复**：msgId 去重、已投职位持久化断点续投
- **安全前置检查**：简历文件不存在时拒绝 BOSS 投递；CDP 断线自动重连，点击失败不写入已处理状态
- **每日报表**：Markdown 日报（投递数/跳过统计/薪资/聊天互动），可配计划任务每晚 23:00 自动生成

## 快速开始

```powershell
git clone https://github.com/your-github-username/job-auto-apply.git
cd job-auto-apply
pip install websockets

# 1. 首次配置（交互式引导）
python scripts\guide.py
```

`guide.py` 会依次询问：
- 你的姓名（用于日志/报表）
- 附件简历 PDF 路径
- 一句话背景介绍（用于自动回复模板）
- 核心技术栈 / 代表性项目
- 投递平台（boss / job51 / 两者）
- **薪资门槛**（月薪上限不低于多少千元才投）
- **城市优先级**
- **跳过词**（职位名含这些词一律不投，回车用默认）
- **搜索关键词**（多关键词轮换）
- 浏览器 CDP 端口（默认即可）

配置保存在本机 `scripts/config.json`（已被 .gitignore 排除，不会提交）。

## 使用

```powershell
python scripts\launch_browsers.py            # 启动 Chrome（BOSS 独立实例 + 51job）
python scripts\boss_apply.py --target 100    # BOSS 投递 100 份
python scripts\job51_apply.py --target 100   # 51job 投递 100 份
python scripts\boss_chat_monitor.py          # 常驻监控聊天自动回复（--once 单轮调试）
python scripts\daily_report.py               # 生成今日日报
python scripts\daily_report.py 2026-08-16    # 指定日期
```

## 目录结构

```
job-auto-apply/
├── SKILL.md                  # opencode/AI Agent 技能说明
├── README.md
├── config.example.json       # 配置示例（无隐私）
└── scripts/
    ├── guide.py              # 首次使用交互引导（生成 config.json）
    ├── common.py             # 公共配置加载
    ├── launch_browsers.py    # 启动投递浏览器
    ├── boss_apply.py         # BOSS 直聘投递
    ├── job51_apply.py        # 前程无忧投递
    ├── boss_chat_monitor.py  # BOSS 聊天监控自动回复
    ├── daily_report.py       # 每日报表
    ├── config.json           # 本机配置（.gitignore 排除，不提交）
    ├── boss_applied_ids.json # 已投记录（自动生成）
    └── boss_chat_replied.json# 已回复记录（自动生成）
```

## 筛选规则示例

| 薪资文本 | 解析结果 | 默认门槛(≥1万) |
| --- | --- | --- |
| `7千-9千` | 上限 9K | ✗ 跳过 |
| `9千-1.4万` | 上限 14K | ✓ 投 |
| `6-8K·24薪` | 月均 8×24/12=16K | ✓ 投 |
| `面议` | 未标注 | ✗ 跳过 |
| `AI产品销售代表` | 含"销售" | ✗ 跳过 |
| `AI产品经理` | 无跳过词 | ✓ 按薪资判定 |

## 常见问题

- **BOSS 页面被清空/无限刷新**：风控或反自动化检测。确认 Chrome 以 `--disable-blink-features=AutomationControlled` 启动（`launch_browsers.py` 已内置），并停止操作等待解封
- **深分页重复职位**：脚本会自动切换关键词（重叠率 >60% 或连续 3 页 0 投）
- **51job 卡片选择器失效**：页面改版时，在 `config.json` 的 `job51.selectors` 中调整 CSS 选择器

## License

MIT
