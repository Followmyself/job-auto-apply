#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前程无忧(51job)批量投递（CDP 直连，配置驱动）

筛选规则（来自 config.json，与 BOSS 一致）：
- 薪资上限 >= target_salary_k 千元/月 才投；年薪制换算月均；未标注薪资跳过
- 职位名含 skip_title_words 不投
注意：51job 页面结构可能变化，卡片/投递按钮选择器可在 config.json 的 job51.selectors 中调整。
用法：python job51_apply.py --target 100
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

DEFAULT_SELECTORS = {
    "cards": ".joblist-item, .joblist-boxe .e, .job-primary, .joblist-box_li",
    "title": ".jname, .job-name, .joblist-box_li .job-title",
    "salary": ".sal, .salary, .joblist-box_li .sal",
    "apply_btn": ".btn.apply, .btn-apply, .joblist-box_li .apply-btn",
    "city": ".area, .location, .job-area, .job-location",
    "applied_text": "已申请",
    "pagination": ".pagination .el-pager li, .page-next",
}


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


def is_match(title, negative=None, match_words=None, industry=None):
    """精准匹配判断（与 BOSS 一致）：投递额度有限，只投最契合的岗位"""
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


def parse_salary_51(text: str):
    """解析 51job 薪资文本，返回 (min_k, max_k, 月均上限K) 或 None。
    支持：'1-1.5万/月'、'9千-1.4万'、'10-15K'、'面议'、'1.5-2万·15薪' 等
    """
    if not text:
        return None
    t = text.strip()
    if "面议" in t or "面谈" in t:
        return None
    m = re.search(r"([\d.]+)\s*(万|k|千)?\s*[-~至]\s*([\d.]+)\s*(万|k|千)", t, re.I)
    if not m:
        m2 = re.search(r"([\d.]+)\s*(万|k|千)", t, re.I)
        if not m2:
            return None
        unit = m2.group(2).lower()
        v = float(m2.group(1))
        k = v * 10 if unit == "万" else v
        return (k, k, k)
    lo, hi = float(m.group(1)), float(m.group(3))
    lo_unit = (m.group(2) or m.group(4)).lower()
    hi_unit = m.group(4).lower()
    lo_k = lo * 10 if lo_unit == "万" else lo
    hi_k = hi * 10 if hi_unit == "万" else hi
    mz = re.search(r"·?\s*(\d+)\s*薪", t)
    monthly_hi = hi_k
    if mz:
        monthly_hi = hi_k * float(mz.group(1)) / 12.0
    return (lo_k, hi_k, monthly_hi)


class Job51Applier:
    def __init__(self, cfg):
        self.cfg = cfg
        self.port = int(cfg["job51"].get("port", 9232))
        self.salary_k = float(cfg["user"].get("target_salary_k", 10))
        self.skip_words = cfg["user"].get("skip_title_words", [])
        self.cities = cfg["user"].get("cities_priority", [])
        self.negative_words = cfg["user"].get("negative_title_words", NEGATIVE_WORDS_DEFAULT)
        self.match_words = cfg["user"].get("match_title_words", MATCH_WORDS_DEFAULT)
        self.industry_words = cfg["user"].get("industry_words", INDUSTRY_WORDS_DEFAULT)
        self.queries = cfg["user"].get("queries", ["AI"])
        self.sel = {**DEFAULT_SELECTORS, **cfg.get("job51", {}).get("selectors", {})}
        logs_dir = Path(resolve(cfg.get("logs_dir", "logs")))
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = logs_dir / "51job投递日志.md"
        self.applied_db = Path(__file__).resolve().parent / "job51_applied_ids.json"
        self.applied = set()
        if self.applied_db.exists():
            try:
                self.applied = set(json.loads(self.applied_db.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as e:
                raise RuntimeError(f"51job 已投记录损坏，拒绝继续投递：{self.applied_db} ({e})") from e
        self.mid = 0
        self.ws = None

    def log(self, msg):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            print(f"日志写入失败: {e}")

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
        await asyncio.sleep(7)

    async def get_cards(self):
        sel = self.sel
        data = await self.ev(f"""(function(){{
          var out=[];
          var cards=document.querySelectorAll({json.dumps(sel['cards'])});
          for(var i=0;i<cards.length;i++){{
            var c=cards[i];
             var t=c.querySelector({json.dumps(sel['title'])});
             var s=c.querySelector({json.dumps(sel['salary'])});
             var b=c.querySelector({json.dumps(sel['apply_btn'])});
             var city=c.querySelector({json.dumps(sel.get('city', ''))});
             var link=c.querySelector('a[href]');
             var href=link?link.href:'';
             var key=c.getAttribute('data-id')||c.getAttribute('data-jobid')||href||'';
             out.push({{
               dom_index:i,
               title: t?(t.textContent||'').trim():'',
               salary: s?(s.textContent||'').trim():'',
               btnText: b?(b.textContent||'').trim():'',
               key: key.slice(0,200),
               city: city?(city.textContent||'').trim():''
             }});
          }}
          return out;
        }})()""")
        return data or []

    async def apply_card(self, idx):
        sel = self.sel
        r = await self.ev(f"""(function(){{
          var cards=document.querySelectorAll({json.dumps(sel['cards'])});
          var c=cards[{idx}];
          if(!c) return 'nocards';
          var b=c.querySelector({json.dumps(sel['apply_btn'])});
          if(!b) return 'nobtn';
          var t=(b.textContent||'').trim();
          if(t.indexOf({json.dumps(sel['applied_text'])})>-1) return 'applied';
          b.click();
          return 'clicked';
        }})()""")
        return r

    async def run(self, target):
        total = len(self.applied)
        self.log(f"断点续投：已投 {total} 份，目标 {target} 份")
        for query in self.queries:
            if total >= target:
                break
            page = 1
            zero_streak = 0
            while total < target and page <= 25:
                url = "https://we.51job.com/pc/search?" + urlencode({"keyword": query, "searchType": 2, "sortType": 0, "pageNum": page, "pageSize": 20})
                await self.nav(url)
                cards = await self.get_cards()
                if not cards:
                    self.log(f"关键词[{query}] 第 {page} 页无职位，切换")
                    break
                cards.sort(key=lambda c: city_rank(c.get("city", ""), self.cities))
                self.log(f"关键词[{query}] 第 {page} 页：{len(cards)} 个职位")
                done = 0
                for i, card in enumerate(cards):
                    if total >= target:
                        break
                    title = card["title"]
                    if not title:
                        continue
                    job_key = card.get("key", "")
                    if not job_key:
                        self.log(f"  [跳过] {title[:35]} | missing-job-id")
                        continue
                    if job_key in self.applied or title in self.applied:
                        continue
                    sal = parse_salary_51(card["salary"])
                    if not sal or sal[2] < self.salary_k:
                        self.log(f"  [跳过] {title[:35]} | 薪资不足: {card['salary'][:20]}")
                        continue
                    if any(w in title for w in self.skip_words):
                        self.log(f"  [跳过] {title[:35]} | skip-title")
                        continue
                    if not is_match(title, self.negative_words, self.match_words, self.industry_words):
                        self.log(f"  [跳过] {title[:35]} | 不匹配岗位")
                        continue
                    dom_index = card.get("dom_index", i)
                    r = await self.apply_card(dom_index)
                    if r == "clicked":
                        await asyncio.sleep(3)
                        await self.ev("""(function(){
                          var closed=false;
                          document.querySelectorAll('.el-dialog, .el-message-box, [class*="dialog"]').forEach(function(d){
                            if(d.offsetParent!==null){
                              var b=d.querySelector('.el-dialog__headerbtn, [class*="headerbtn"], [class*="close"], .el-message-box__headerbtn');
                              if(b){ b.click(); closed=true; }
                            }
                          });
                          return closed;
                        })()""")
                        chk = await self.ev(f"""(function(){{
                          var cards=document.querySelectorAll({json.dumps(self.sel['cards'])});
                           var c=cards[{dom_index}];
                          if(!c) return 'gone';
                          var b=c.querySelector({json.dumps(self.sel['apply_btn'])});
                          return b?(b.textContent||'').trim():'';
                        }})()""")
                        if chk and self.sel["applied_text"] in chk:
                            total += 1
                            done += 1
                            self.applied.add(job_key)
                            self.applied_db.write_text(json.dumps(sorted(self.applied), ensure_ascii=False), encoding="utf-8")
                            self.log(f"  [投递成功 {total}/{target}] {title[:40]} | {card['salary'][:20]}")
                        else:
                            self.log(f"  [未确认] {title[:35]} | 按钮状态: {chk}")
                    else:
                        self.log(f"  [跳过] {title[:35]} | apply-btn: {r}")
                    await asyncio.sleep(1.5)
                self.log(f"关键词[{query}] 第 {page} 页完成，本页投 {done}，累计 {total}")
                page += 1
                zero_streak = zero_streak + 1 if done == 0 else 0
                if zero_streak >= 3:
                    self.log(f"关键词[{query}] 连续 3 页 0 投，切换关键词")
                    break
            self.log(f"关键词[{query}] 结束")
        self.log(f"===== 51job 投递结束：累计 {total} 份 =====")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100)
    args = ap.parse_args()
    cfg = load_config()
    a = Job51Applier(cfg)
    await a.connect("51job.com")
    a.log(f"===== 51job 自动投递开始，目标 {args.target} 份 =====")
    await a.run(args.target)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)
