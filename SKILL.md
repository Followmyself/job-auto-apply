---
name: job-auto-apply
description: 自动求职投递工作流：在 BOSS 直聘与前程无忧(51job)上按筛选条件（薪资门槛、城市优先、跳过销售/客服/高管）自动投递简历、自动回复 HR 聊天消息、自动生成每日投递报表并写日志。当用户要求"自动投简历/投递 100 份/自动回复 HR/生成求职日报"时使用。
---

# Job Auto Apply 自动求职投递

自动化求职投递：BOSS 直聘 + 前程无忧(51job)，含筛选、投递、聊天自动回复、日报与日志。

## 前置条件

- Chrome 已安装；Python `python` 可用（需 `websockets` 库：`pip install websockets`）
- 首次使用运行引导：`python scripts\guide.py`（交互式填写简历路径、平台、筛选条件，生成 `config.json`）
- 各平台需在对应 Chrome profile 中登录过（扫码登录一次，登录态持久化）
- 投递前必须确认 `user.pdf_resume` 指向存在的 PDF；简历缺失时 BOSS 投递会被拒绝，不会伪记为成功

## 配置

所有私人信息在 `config.json`（由 `guide.py` 生成，不入库；参考 `config.example.json`）：

| 配置项 | 说明 |
| --- | --- |
| user.pdf_resume | 附件简历 PDF 路径 |
| user.target_salary_k | 薪资门槛（月薪上限 ≥ N 千才投，默认 10） |
| user.cities_priority | 城市优先级（就近城市优先） |
| user.skip_title_words | 职位名含这些词一律不投（销售/客服/高管等） |
| user.queries | 搜索关键词列表（多关键词轮换避免深分页重复） |
| boss.port / job51.port | CDP 调试端口（BOSS 独立实例，避免反自动化检测） |
| logs_dir / report_dir | 日志与日报目录 |

## 使用流程

### 1. 初始化（首次）
```powershell
python scripts\guide.py
```
按提示回答：简历 PDF 路径、投递平台（boss/job51/两者）、目标城市、薪资门槛、跳过词、搜索关键词。生成 `config.json`。

### 2. 启动浏览器
```powershell
python scripts\launch_browsers.py
```
- BOSS 直聘：独立 Chrome（端口 `boss.port`），必须加 `--disable-blink-features=AutomationControlled`（否则被反自动化检测清空页面），MCP/调试器不要连接该实例
- 51job：Chrome（端口 `job51.port`）

### 3. 投递
```powershell
python scripts\boss_apply.py --target 100      # BOSS 直聘投递 100 份
python scripts\job51_apply.py --target 100     # 前程无忧投递 100 份
```
筛选规则（硬性）：
- 薪资区间上限 < `target_salary_k` 千/月 不投；支持 `9千-1.4万`、`10-15K`、年薪制（X-Y K·Z薪）并换算月均 = Y×Z/12；未标注薪资跳过
- 职位名含 `skip_title_words` 一律不投（默认含销售/Sales/客服/业务员/电销/陌拜/地推/推销/客户代表/招商经理/招聘顾问/猎头/总监/VP/首席/负责人/CEO/CTO/总裁/合伙人/总经理/副总/业务拓展/BD/客户成功）
- 城市优先级：按 `cities_priority` 对页面卡片排序，未识别城市排在最后
- BOSS 深分页会返回重复 → 多关键词轮换 + 重叠率>60% 或连续 3 页 0 投自动切换；聊天监控 CDP 连接断开时自动重连
- 已投 jobId 持久化 `boss_applied_ids.json`，支持断点续投

### 4. 聊天监控自动回复（BOSS）
```powershell
python scripts\boss_chat_monitor.py        # 常驻，每 N 秒轮询
python scripts\boss_chat_monitor.py --once # 单轮调试
```
自动处理：
- HR 索要附件简历 → 点"同意"自动发送
- 交换微信 → 点"同意"
- 工作地点确认 → 点"可以接受"
- 技术/经验问题 → 自动回复技术背景介绍
- 其他 HR 消息 → 通用礼貌回复
- 防重复（按 msgId 记录 `boss_chat_replied.json`）；**会话名双重校验防发错**（打开会话后验证 `.chat-conversation .name-text` 与目标匹配才回复）

### 5. 每日报表
```powershell
python scripts\daily_report.py          # 今天
python scripts\daily_report.py 2026-08-16
```
生成 Markdown 日报到 `report_dir\<日期>.md`（BOSS 投递数/跳过统计/薪资、聊天互动、51job 投递数）。可配置 Windows 计划任务每晚 23:00 执行。

## 注意事项

- BOSS 直聘对自动化敏感：投递/聊天必须用独立 Chrome 实例（MCP 等调试器不要连接），页面加载后不要频繁刷新
- 风控被拦（403 页自动刷新循环）：立即关闭标签页，停止操作等待解封，不要反复打开
- 聊天自动同意动作必须返回点击成功才会写入去重状态，失败会保留待重试
- 聊天回复内容可在 `config.json` 的 `chat_reply_templates` 中自定义
- 所有操作写入日志文件（投递日志、聊天日志），供日报汇总
