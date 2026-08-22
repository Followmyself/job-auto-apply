#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOSS 直聘聊天监控自动回复（CDP 直连，配置驱动）

检测 HR 新消息自动处理：
- 简历请求 → 点"同意"自动发送附件简历
- 交换微信 → 点"同意"
- 工作地点确认 → 点"可以接受"
- 技术/经验问题 → 按模板自动回复
- 其他 → 通用礼貌回复
防重复（msgId 记录）+ 会话名双重校验防发错。
用法：python boss_chat_monitor.py [--once]
"""
import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

import websockets

from common import load_config, resolve


class ChatMonitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.port = int(cfg["boss"].get("port", 9233))
        templates = cfg["boss"].get("chat_reply_templates", {})
        bg = cfg["user"].get("background", "AI应用开发方向")
        stack = cfg["user"].get("tech_stack", "Python、Agent、RAG")
        projects = cfg["user"].get("projects", "多个0到1交付项目")
        def fill(t):
            return t.replace("{user_bg}", bg).replace("{user_stack}", stack).replace("{user_projects}", projects)
        self.replies = {
            "intro": fill(templates.get("intro", "您好！感谢您的回复。我的背景是{user_bg}，有多个0到1交付的项目经验。请问方便介绍一下岗位的具体情况和团队吗？期待进一步沟通！")),
            "sales": fill(templates.get("sales_question", "您好！我过往没有销售相关的工作经验。我的背景是{user_bg}，如果贵司有技术方向的岗位需求，我可以详细介绍一下项目经验。")),
            "experience": fill(templates.get("experience_question", "您好！我是{user_bg}方向，主攻{user_stack}，有{user_projects}等完整项目经验，可以把技术落地到实际业务场景。期待进一步沟通！")),
        }
        logs_dir = Path(resolve(cfg.get("logs_dir", "logs")))
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = logs_dir / "boss聊天日志.md"
        self.state_file = Path(__file__).resolve().parent / "boss_chat_replied.json"
        self.poll_interval = int(cfg["boss"].get("chat_poll_interval", 90))
        self.my_id = 0
        self.state = {}
        if self.state_file.exists():
            try:
                self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise RuntimeError(f"聊天去重记录损坏，拒绝启动：{self.state_file} ({e})") from e
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

    def save_state(self):
        try:
            self.state_file.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"聊天去重记录保存失败：{self.state_file} ({e})") from e

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

    async def api_get_friends(self):
        expr = """(async function(){
          try {
            var r = await fetch('/wapi/zprelation/friend/getGeekFriendList.json?page=1', {credentials:'include'});
            if(!r.ok) throw new Error('HTTP '+r.status);
            var j = await r.json();
            var res = (j.zpData && j.zpData.result) || [];
            return {ok:true, friends:res.map(function(f){
              var li = f.lastMessageInfo || {};
              return {name: f.name||'', lastMsg: f.lastMsg||'', msgId: li.msgId||0, fromId: li.fromId||0};
            })};
          } catch(e) { return {ok:false, error:String(e)}; }
        })()"""
        res = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        value = res.get("result", {}).get("value") or {}
        if not value.get("ok"):
            raise RuntimeError(f"会话列表接口失败：{value.get('error', 'unknown error')}")
        return value.get("friends", [])

    async def get_conversation_name(self):
        name = await self.ev("""(function(){
          var n=document.querySelector('.chat-conversation .name-text');
          return n?(n.innerText||'').trim():'';
        })()""")
        return name or ""

    async def get_messages(self):
        return await self.ev("""(function(){
          var box=document.querySelector('.chat-dialog-message-list,.chat-message-list,[class*=message-list]');
          return box?(box.innerText||'').trim():(document.body.innerText.slice(-1200));
        })()""")

    async def click_by_text(self, text):
        return await self.ev(f"""(function(){{
          var btns=[].slice.call(document.querySelectorAll('span,a,button,div'));
          for(var i=0;i<btns.length;i++){{
            if((btns[i].innerText||'').trim()==={json.dumps(text)}&&btns[i].children.length===0){{
              btns[i].click(); return 'clicked';
            }}
          }}
          return 'notfound';
        }})()""")

    async def open_session_by_name(self, name):
        for attempt in range(2):
            pos = await self.ev(f"""(function(){{
              var wanted={json.dumps(name.strip())};
              var items=document.querySelectorAll('.friend-content-warp');
              for(var i=0;i<items.length;i++){{
                var nt=items[i].querySelector('.name-text');
                if(nt&&(nt.innerText||'').trim()===wanted){{
                  var r=items[i].getBoundingClientRect();
                  return {{x:r.x+r.width/2, y:r.y+r.height/2}};
                }}
              }}
              return null;
            }})()""")
            if not pos:
                return False
            await self.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
            await self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
            cur = ""
            for _ in range(5):
                await asyncio.sleep(2)
                cur = await self.get_conversation_name()
                if cur:
                    break
            if cur.strip() == name.strip():
                return True
            self.log(f"  点击 {name} 后当前会话为 {cur or '(未打开)'}，重试")
        return False

    async def send_reply(self, text, expect_name=""):
        if expect_name:
            cur = await self.get_conversation_name()
            if cur.strip() != expect_name.strip():
                self.log(f"  发送前校验失败：期望会话 {expect_name}，当前 {cur}，取消发送！")
                return False
        r = await self.ev("(function(){var t=document.querySelector('#chat-input');if(!t)return 'NO INPUT';t.focus();return 'focused';})()")
        if r != "focused":
            return False
        await asyncio.sleep(0.5)
        await self.call("Input.insertText", {"text": text})
        await asyncio.sleep(0.5)
        await self.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "text": "\r"})
        await self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})
        await asyncio.sleep(2)
        return True

    def pick_reply(self, msgs):
        if "交换微信" in msgs and "是否同意" in msgs:
            return "AGREE_WECHAT"
        if "想要一份您的附件简历" in msgs or ("附件简历" in msgs and "是否同意" in msgs):
            return "AGREE_RESUME"
        if "是否接受此工作地点" in msgs or "接受此工作地点" in msgs:
            return "ACCEPT_LOCATION"
        if "销售" in msgs and ("经验" in msgs or "做过" in msgs):
            return self.replies["sales"]
        if any(k in msgs for k in ("经验", "技能", "技术", "介绍", "工作内容")):
            return self.replies["experience"]
        if any(k in msgs for k in ("合适", "不匹配", "抱歉")):
            return ""
        return self.replies["intro"]

    async def process_session(self, name, friend):
        key = name
        msg_id = friend.get("msgId", 0)
        if key in self.state and self.state[key].get("msgId") == msg_id:
            return False
        cur = await self.get_conversation_name()
        if not cur or (name[:2] not in cur and cur[:2] not in name):
            self.log(f"[{name}] 会话名不匹配（{cur}），跳过")
            return False
        msgs = await self.get_messages()
        if not msgs:
            return False
        lines = [l.strip() for l in msgs.split("\n") if l.strip()]
        if not lines:
            return False
        last = lines[-1]
        if last in ("已读", "送达", "发送中"):
            return False
        if last.startswith(("您好！", "你好！")) and "方便沟通" in last:
            return False
        reply = self.pick_reply(msgs)
        if reply == "AGREE_WECHAT":
            if "已发送给您" in msgs or "微信号" in msgs:
                self.log(f"[{name}] 微信已交换过，跳过")
                self.state[key] = {"msgId": msg_id, "t": time.time()}
                self.save_state()
                return False
            r = await self.click_by_text("同意")
            self.log(f"[{name}] 交换微信请求 → 点击同意（{r}）")
            if r != "clicked":
                return False
            await asyncio.sleep(2)
            self.state[key] = {"msgId": msg_id, "t": time.time()}
            self.save_state()
            return True
        if reply == "AGREE_RESUME":
            if "已发送给Boss" in msgs or "已发送给 Boss" in msgs:
                self.log(f"[{name}] 附件简历已发送过，跳过")
                self.state[key] = {"msgId": msg_id, "t": time.time()}
                self.save_state()
                return False
            r = await self.click_by_text("同意")
            self.log(f"[{name}] 简历请求 → 点击同意（{r}），附件简历自动发送")
            if r != "clicked":
                return False
            self.state[key] = {"msgId": msg_id, "t": time.time()}
            self.save_state()
            return True
        if reply == "ACCEPT_LOCATION":
            r = await self.click_by_text("可以接受")
            self.log(f"[{name}] 工作地点确认 → 点击可以接受（{r}）")
            if r != "clicked":
                return False
            self.state[key] = {"msgId": msg_id, "t": time.time()}
            self.save_state()
            return True
        if reply:
            ok = await self.send_reply(reply, expect_name=cur)
            if ok:
                self.log(f"[{name}] 自动回复: {reply[:40]}...")
                self.state[key] = {"msgId": msg_id, "t": time.time()}
                self.save_state()
                return True
            self.log(f"[{name}] 发送被拦截（会话校验失败）")
            return False
        self.log(f"[{name}] 无匹配回复策略，跳过: {last[:50]}")
        return False

    async def run_once(self):
        friends = await self.api_get_friends()
        if not friends:
            self.log("API 会话列表为空/异常")
            return 0
        candidates = []
        for f in friends:
            if f.get("fromId") == self.my_id or not f.get("msgId"):
                continue
            if f["name"] in self.state and self.state[f["name"]].get("msgId") == f["msgId"]:
                continue
            candidates.append(f)
        self.log(f"发现 {len(candidates)} 个需要处理的会话（HR 新消息）")
        handled = 0
        for f in candidates[:3]:
            if not await self.open_session_by_name(f["name"]):
                continue
            if await self.process_session(f["name"], f):
                handled += 1
        self.log(f"本轮处理 {handled} 个会话")
        return handled

    async def run(self):
        self.log("===== BOSS 聊天监控启动 =====")
        await self.call("Page.navigate", {"url": "https://www.zhipin.com/web/geek/chat"})
        self.log("等待页面稳定（90 秒）...")
        await asyncio.sleep(90)
        while True:
            try:
                await self.run_once()
            except Exception as e:
                self.log(f"轮询异常: {e}")
                try:
                    if self.ws:
                        await self.ws.close()
                except Exception as close_error:
                    self.log(f"关闭旧 CDP 连接失败: {close_error}")
                try:
                    await self.connect("zhipin.com")
                    self.log("CDP 连接已重建")
                except Exception as reconnect_error:
                    self.log(f"CDP 重连失败: {reconnect_error}")
            await asyncio.sleep(self.poll_interval)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    m = ChatMonitor(cfg)
    await m.connect("zhipin.com")
    if args.once:
        await m.run_once()
    else:
        await m.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)
