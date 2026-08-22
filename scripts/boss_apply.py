#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOSS 直聘批量投递（CDP 直连独立 Chrome，配置驱动）

筛选规则（来自 config.json）：
- 薪资门槛：月均收入上限 >= target_salary_k 千元 才投（区间上限 K*1000；年薪制 X-Y K·Z薪 换算月均 = Y*Z/12）
- 未标注薪资 / 面议：跳过
- 职位名含 skip_title_words：跳过（销售/客服/高管等）
- 城市优先级：cities_priority
用法：python boss_apply.py --target 100
"""
import argparse
import asyncio
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import websockets

from common import city_rank, load_config, resolve

PUA_MAP = {
    0xE031: "1", 0xE032: "2", 0xE033: "3", 0xE034: "4", 0xE035: "5",
    0xE036: "6", 0xE037: "7", 0xE038: "8", 0xE039: "9", 0xE03A: "0",
}
CITIES = ["长沙", "武汉", "广州", "北京", "上海", "深圳", "成都", "杭州", "南京", "西安", "重庆", "郑州", "天津", "苏州", "东莞", "佛山"]
CHAT_QUOTA_RE = re.compile(r"您今天已与\s*(\d+)\s*位BOSS沟通，还剩\s*(\d+)\s*次沟通机会")

# 精准匹配（硬性）：投递额度有限，只投最契合的岗位
NEGATIVE_WORDS_DEFAULT = ["销售", "Sales", "客服", "电销", "陌拜", "地推", "业务员", "推销", "客户代表", "招商经理",
                          "招聘顾问", "猎头", "业务拓展", "BD", "客户成功", "运营", "市场", "推广", "品牌", "主播", "直播",
                          "标注", "数据标注", "AI训练师", "审核", "评测", "算法工程师", "算法", "研究员", "研究",
                          "讲师", "教师", "老师", "培训", "实习生", "实习",
                          "总监", "VP", "首席", "负责人", "CEO", "CTO", "总裁", "合伙人", "总经理", "副总", "校长", "专家岗"]
MATCH_WORDS_DEFAULT = ["AI应用", "AI应用开发", "AI应用工程师", "AI开发", "AI开发工程师", "AI工程", "AI工程化",
                       "Agent", "智能体", "RAG", "大模型应用", "MCP", "提示词", "Prompt", "AI产品", "AI产品经理",
                       "AI工具", "AI落地", "AI场景", "人工智能应用", "AI办公", "AI效率", "AI应用产品",
                       "AI全栈", "全栈", "Python开发", "Python工程师", "Python",
                       "大模型开发", "大模型工程师", "LLM", "AIGC", "AI软件", "AI应用软件"]
INDUSTRY_WORDS_DEFAULT = ["水利", "工程", "建筑", "制造", "能源", "电力", "环保", "医疗", "金融", "教育", "交通",
                          "政务", "工业", "农业", "物流", "供应链", "法律", "人力", "电商", "零售", "旅游", "汽车",
                          "机械", "化工", "地质", "测绘", "GIS", "设计", "水务", "管网", "勘察", "新能源", "智能建造",
                          "数字化", "智慧", "行业", "产业", "场景", "业务"]


def is_match(title: str, negative=None, match_words=None, industry=None):
    """精准匹配判断：投递额度有限，只投最契合的岗位"""
    negative = negative or NEGATIVE_WORDS_DEFAULT
    match_words = match_words or MATCH_WORDS_DEFAULT
    industry = industry or INDUSTRY_WORDS_DEFAULT
    if any(w in title for w in negative):
        return False
    if any(w in title for w in match_words):
        return True
    if ("AI" in title or "智能" in title) and any(w in title for w in industry):
        return True
    return False


def decode_salary(text: str):
    """解码 BOSS PUA 混淆薪资，返回 (min_k, max_k, 月均上限K) 或 None"""
    if not text:
        return None
    t = "".join(PUA_MAP.get(ord(c), c) for c in text).strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*K", t)
    if not m:
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*K", t)
        if m2:
            v = float(m2.group(1))
            return (v, v, v)
        return None
    lo, hi = float(m.group(1)), float(m.group(2))
    mz = re.search(r"·\s*(\d+(?:\.\d+)?)\s*薪", t)
    monthly_hi = hi
    if mz:
        monthly_hi = hi * float(mz.group(1)) / 12.0
    return (lo, hi, monthly_hi)


class BossApplier:
    def __init__(self, cfg):
        self.cfg = cfg
        self.port = int(cfg["boss"].get("port", 9233))
        self.pdf = resolve(cfg["user"].get("pdf_resume", ""))
        self.queries = cfg["user"].get("queries", ["AI应用"])
        self.skip_words = cfg["user"].get("skip_title_words", [])
        self.negative_words = cfg["user"].get("negative_title_words", NEGATIVE_WORDS_DEFAULT)
        self.match_words = cfg["user"].get("match_title_words", MATCH_WORDS_DEFAULT)
        self.industry_words = cfg["user"].get("industry_words", INDUSTRY_WORDS_DEFAULT)
        self.salary_k = float(cfg["user"].get("target_salary_k", 10))
        self.cities = cfg["user"].get("cities_priority", []) or CITIES
        logs_dir = Path(resolve(cfg.get("logs_dir", "logs")))
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = logs_dir / "boss投递日志.md"
        self.applied_db = Path(__file__).resolve().parent / "boss_applied_ids.json"
        self.applied_ids = set()
        if self.applied_db.exists():
            try:
                self.applied_ids = set(json.loads(self.applied_db.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as e:
                raise RuntimeError(f"已投记录损坏，拒绝继续投递：{self.applied_db} ({e})") from e
        self.mid = 0
        self.ws = None
        self.page_url = ""

    def log(self, msg):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            print(f"日志写入失败: {e}")

    def save_applied(self):
        try:
            self.applied_db.write_text(json.dumps(sorted(self.applied_ids), ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"已投记录保存失败，已停止继续投递：{self.applied_db} ({e})") from e

    async def connect(self, expected_host=""):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=5) as r:
            pages = json.loads(r.read().decode("utf-8"))
        pages = [p for p in pages if p.get("type") == "page"]
        if expected_host:
            pages = [p for p in pages if expected_host in p.get("url", "")]
        if not pages:
            raise RuntimeError(f"端口 {self.port} 没有匹配 {expected_host or '目标'} 的页面（Chrome 未启动或页面不正确）")
        self.ws = await websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024)

    async def call(self, method, params=None, timeout=60):
        self.mid += 1
        req = {"id": self.mid, "method": method}
        if params:
            req["params"] = params
        await self.ws.send(json.dumps(req))
        while True:
            resp = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if resp.get("id") == self.mid:
                if "error" in resp:
                    raise RuntimeError(resp["error"])
                return resp.get("result", {})

    async def ev(self, expr, timeout=60):
        res = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True}, timeout)
        return res.get("result", {}).get("value")

    async def nav(self, url):
        await self.call("Page.navigate", {"url": url})
        await asyncio.sleep(8)

    async def upload_pdf(self):
        if not self.pdf or not Path(self.pdf).exists():
            self.log(f"错误：附件简历不存在，拒绝投递：{self.pdf or '(未配置)'}")
            return False
        dom = await self.call("DOM.getDocument")
        root = dom.get("root", {}).get("nodeId")
        found = await self.call("DOM.querySelectorAll", {"nodeId": root, "selector": "input[type=file]"})
        nids = found.get("nodeIds", [])
        if not nids:
            return False
        await self.call("DOM.setFileInputFiles", {"nodeId": nids[0], "files": [self.pdf]})
        await asyncio.sleep(5)
        return True

    async def dismiss_chat_quota_warning(self):
        """处理 BOSS 每日沟通额度提示；额度耗尽或弹窗无法关闭时返回原因。"""
        body = await self.ev("document.body ? document.body.innerText : ''") or ""
        match = CHAT_QUOTA_RE.search(body)
        if not match:
            return None
        used, remaining = (int(match.group(1)), int(match.group(2)))
        if remaining <= 0:
            return {"used": used, "remaining": remaining, "clicked": False,
                    "reason": "daily-chat-quota-exhausted"}
        clicked = await self.ev("""(function(){
          var buttons = Array.from(document.querySelectorAll('a.default-btn.sure-btn,button'));
          var button = buttons.find(function(e){ return (e.innerText || '').trim() === '好'; });
          if (!button) return 'missing';
          button.click();
          return 'clicked';
        })()""")
        return {"used": used, "remaining": remaining, "clicked": clicked == "clicked",
                "reason": "" if clicked == "clicked" else "quota-warning-not-dismissed"}

    async def get_cards(self):
        data = await self.ev("""(function(){
          var out=[];
          var cards=document.querySelectorAll('.job-info');
          for(var i=0;i<cards.length;i++){
            var c=cards[i];
            var a=c.querySelector('.job-name');
            var s=c.querySelector('.job-salary');
            var href=a?a.href:'';
            var idm=href.match(/job_detail\\/([^.]+)/);
            var city=c.querySelector('.job-area,.job-location,.job-place');
            out.push({id:idm?idm[1]:'', title:a?a.textContent.trim():'', salary:s?s.textContent.trim():'', city:city?city.textContent.trim():''});
          }
          return out;
        })()""")
        return data or []

    async def apply_one(self, card):
        cid = card["id"]
        if not cid:
            return False, "missing-job-id"
        if cid in self.applied_ids:
            return False, "dup"
        title = card["title"]
        sal = decode_salary(card["salary"])
        if not sal or sal[2] < self.salary_k:
            sal_desc = f"{sal[0]:.0f}-{sal[1]:.0f}K" if sal else "unknown"
            return False, f"salary<{self.salary_k:.0f}K: {sal_desc}"
        if any(w in title for w in self.skip_words):
            return False, "skip-title"
        if not is_match(title, self.negative_words, self.match_words, self.industry_words):
            return False, "not-match"

        clicked = await self.ev(f"""(function(){{
          var cards=document.querySelectorAll('.job-info');
          for(var i=0;i<cards.length;i++){{
            var a=cards[i].querySelector('.job-name');
            if(a&&a.href.indexOf({json.dumps(cid)})>-1){{ a.click(); return 'ok'; }}
          }}
          return 'notfound';
        }})()""")
        if clicked != "ok":
            return False, "card-click-fail"
        await asyncio.sleep(3)

        city = await self.ev("""(function(){
          var h=document.querySelector('.job-detail-header');
          var t=h?h.innerText:'';
          var m=t.match(/(长沙|武汉|广州|北京|上海|深圳|成都|杭州|南京|西安|重庆|郑州|天津|苏州|东莞|佛山)/);
          return m?m[1]:'';
        })()""") or ""

        r = await self.ev("""(function(){
          var btn=document.querySelector('.job-detail-op .op-btn-chat');
          if(!btn) return 'nobtn';
          if(btn.className.indexOf('is-disabled')>-1) return 'disabled';
          btn.click();
          return 'ok';
        })()""")
        if r != "ok":
            return False, f"chat-btn:{r}"
        await asyncio.sleep(3)

        quota = await self.dismiss_chat_quota_warning()
        if quota:
            if quota["reason"]:
                return False, f"{quota['reason']}: used={quota['used']} remaining={quota['remaining']}"
            await asyncio.sleep(1)

        if not await self.upload_pdf():
            return False, "no-file-input"

        st = await self.ev("""(function(){
          var body=document.body.innerText;
          return /已向BOSS发送消息/.test(body)?'sent':'nosent';
        })()""")
        if st != "sent":
            return False, "not-sent"

        await self.ev("""(function(){
          var b=document.querySelector('a.default-btn.sure-btn');
          if(b){ b.click(); return 'ok'; }
          return 'nosure';
        })()""")
        await asyncio.sleep(4)

        await self.nav(self.page_url)
        self.applied_ids.add(cid)
        self.save_applied()
        sal_desc = f"{sal[0]:.0f}-{sal[1]:.0f}K" if sal[1] != sal[0] else f"{sal[0]:.0f}K"
        return True, f"OK city={city} sal={sal_desc}"

    async def run(self, target):
        total = len(self.applied_ids)
        self.log(f"断点续投：已投 {total} 份，目标 {target} 份")
        for query in self.queries:
            if total >= target:
                break
            page = 1
            last_ids = []
            zero_streak = 0
            while total < target and page <= 22:
                self.page_url = "https://www.zhipin.com/web/geek/jobs?" + urlencode({"query": query, "page": page})
                await self.nav(self.page_url)
                cards = await self.get_cards()
                if not cards:
                    self.log(f"关键词[{query}] 第 {page} 页无职位，切换")
                    break
                cards.sort(key=lambda c: city_rank(c.get("city", ""), self.cities))
                cur_ids = [c["id"] for c in cards if c.get("id")]
                overlap = 0.0
                if last_ids:
                    common = len(set(cur_ids) & set(last_ids))
                    overlap = common / max(len(cur_ids), 1)
                if overlap > 0.6:
                    self.log(f"关键词[{query}] 第 {page} 页与上页重叠 {overlap:.0%}，切换关键词")
                    break
                last_ids = cur_ids
                self.log(f"关键词[{query}] 第 {page} 页：{len(cards)} 个职位")
                done = 0
                for card in cards:
                    if total >= target:
                        break
                    ok, info = await self.apply_one(card)
                    if not ok and info.startswith(("daily-chat-quota-exhausted", "quota-warning-not-dismissed")):
                        self.log(f"停止：{info}")
                        self.log(f"===== BOSS 投递暂停：累计 {total} 份 =====")
                        return
                    if ok:
                        total += 1
                        done += 1
                        self.log(f"  [投递成功 {total}/{target}] {card['title'][:40]} | {info}")
                    else:
                        self.log(f"  [跳过] {card['title'][:35]} | {info}")
                    await asyncio.sleep(2)
                self.log(f"关键词[{query}] 第 {page} 页完成，本页投 {done}，累计 {total}")
                page += 1
                zero_streak = zero_streak + 1 if done == 0 else 0
                if zero_streak >= 3:
                    self.log(f"关键词[{query}] 连续 3 页 0 投，切换关键词")
                    break
            self.log(f"关键词[{query}] 结束")
        self.log(f"===== BOSS 投递结束：累计 {total} 份 =====")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100)
    args = ap.parse_args()
    cfg = load_config()
    applier = BossApplier(cfg)
    await applier.connect("zhipin.com")
    applier.log(f"===== BOSS 直聘自动投递开始，目标 {args.target} 份 =====")
    await applier.run(args.target)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)
