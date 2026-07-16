from __future__ import annotations

import time
import httpx, re
from typing import Optional
from config import config
from utils.logger import logger
from openai import AsyncOpenAI
import asyncio

SYSTEM_PROMPT = """
你是一个 Counter-Strike 2 游戏的专家
1.只回答CS2游戏、社区相关的话题，其他都以幽默风趣的口吻回复
2.用户可能会用CS2职业选手的外号表达，你需要知道用户指的是谁
3.讲话不要大大咧咧，沉稳一点
4.用户如果提到比赛，比如major，IEM，blast，ESL，FPL等等，全部都要搜索真实比赛记录，不可瞎编
5.不需要用括号表达现在的语气，态度和动作
6.说话像个真人不要像AI
7.说话风格学习专家，说专业词汇
"""

MAX_HISTORY = 50

def GET_NOW_DATE(): return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())

class AIService:
    """AI 对话服务（单例）"""

    _instance: Optional["AIService"] = None

    def __new__(cls) -> "AIService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self._messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._enabled = config.ai.enabled
        self._client = None

        if not self._enabled:
            logger.info("AI 服务未启用（缺少 AI_API_KEY）")
            return

        try:
            self._client = AsyncOpenAI(
                api_key=config.ai.api_key,
                base_url=config.ai.api_base,
            )
            logger.info(f"AI 服务已初始化: model={config.ai.model}")
        except ImportError:
            self._enabled = False
            logger.warning("openai 未安装，AI 不可用")
        except Exception as e:
            self._enabled = False
            logger.error(f"AI 初始化失败: {e}")

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    # ==================== 联网搜索 ====================

    @staticmethod
    def _expand_queries(user_message: str) -> list[str]:
        """规则生成多角度搜索词"""
        base = user_message.strip()
        queries = [base]
        if "CS2" not in base and "cs2" not in base.lower():
            queries.append(f"CS2 {base}")
        short = re.sub(r'[了嘛吧呢啊呀哈哦噢]{1,2}$', '', base)
        short = re.sub(r'^(请问|告诉我|你知道|有没有|是不是|能不能)', '', short)
        if short != base and len(short) > 5:
            queries.append(short)
            if "CS2" not in short:
                queries.append(f"CS2 {short}")
        match_kw = ['major','iem','blast','esl','fpl','卡托','科隆','里约','上海','奥斯汀','布达佩斯','柏林','巴黎','哥本哈根']
        if any(k in base.lower() for k in match_kw):
            queries.append(f"{base} 比赛结果")
            queries.append(f"{base} hltv")
        seen = set()
        unique = []
        for q in queries:
            q = re.sub(r'\s+', ' ', q).strip()
            if q and q not in seen:
                seen.add(q)
                unique.append(q)
        logger.info(f"搜索词({len(unique)}): {unique}")
        return unique

    async def _ai_expand_queries(self, user_message: str) -> list[str]:
        """AI 补充搜索角度"""
        try:
            resp = await self._client.chat.completions.create(
                model=config.ai.model,
                messages=[
                    {"role": "system", "content": (
                        "输出2-3个搜索关键词，每行一个，不要任何解释:\n"
                        "- 从不同角度搜索（数据、新闻、社区）\n"
                        "- CS2相关加CS2前缀，比赛加年份\n"
                        f"- 日期: {GET_NOW_DATE()}"
                    )},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=100, temperature=0.3,
            )
            raw = (resp.choices[0].message.content or "").strip()
            return [re.sub(r'^[\d\.\-\s]+', '', l).strip()
                    for l in raw.split('\n') if len(l.strip()) > 3][:3]
        except Exception:
            return []

    @staticmethod
    async def _deep_search(queries: list[str], per_q: int = 12) -> list[dict]:
        """多查询并发深度搜索：每个查询搜 2 页，合并去重
        返回 [{url, title, snippet}]
        """
        async def _search_one_q(query: str) -> list[dict]:
            all_items = []
            for offset in [1, 11]:
                try:
                    async with httpx.AsyncClient(timeout=10, verify=False) as c:
                        resp = await c.get(
                            "https://cn.bing.com/search",
                            params={"q": query, "first": str(offset)},
                            headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                              "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                                "Accept-Language": "zh-CN,zh;q=0.9",
                            },
                        )
                    if resp.status_code != 200:
                        continue
                    for m in re.finditer(
                        r'<li class="b_algo"[^>]*>.*?<a[^>]*href="(https?://[^"]+)"[^>]*>(.+?)</a>',
                        resp.text, re.DOTALL | re.IGNORECASE,
                    ):
                        url, raw_t = m.group(1), m.group(2)
                        title = re.sub(r"<[^>]+>", "", raw_t).strip()
                        tail = resp.text[m.end():m.end()+2000]
                        sm = re.search(r'<p[^>]*>(.+?)</p>', tail, re.DOTALL)
                        snippet = re.sub(r"<[^>]+>", "", sm.group(1)).strip() if sm else ""
                        if url and title:
                            all_items.append({"url": url, "title": title, "snippet": snippet})
                except Exception:
                    continue
            return all_items[:per_q]

        batches = await asyncio.gather(*[_search_one_q(q) for q in queries])
        seen = set()
        merged = []
        for batch in batches:
            for item in batch:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    merged.append(item)
        logger.info(f"深度搜索: {len(queries)} 词 x 2页 -> {len(merged)} 条去重")
        return merged

    @staticmethod
    def _expand_queries(user_message: str) -> list[str]:
        """规则生成多角度搜索词 + AI 增强"""
        base = user_message.strip()
        queries = [base]  # 原始问题

        if "CS2" not in base and "cs2" not in base.lower():
            queries.append(f"CS2 {base}")

        short = re.sub(r'[了嘛吧呢啊呀哈哦噢]{1,2}$', '', base)
        short = re.sub(r'^(请问|告诉我|你知道|有没有|是不是|能不能)', '', short)
        if short != base and len(short) > 5:
            queries.append(short)
            if "CS2" not in short:
                queries.append(f"CS2 {short}")

        match_keywords = ['major','iem','blast','esl','fpl','卡托','科隆','里约']
        if any(k in base.lower() for k in match_keywords):
            queries.append(f"{base} 比赛结果")
            queries.append(f"{base} hltv")

        seen = set()
        unique = []
        for q in queries:
            q = re.sub(r'\s+', ' ', q).strip()
            if q and q not in seen:
                seen.add(q)
                unique.append(q)
        logger.info(f"搜索词({len(unique)}): {unique}")
        return unique

    async def _ai_expand_queries(self, user_message: str) -> list[str]:
        """AI 补充搜索角度（失败时降级为规则生成）"""
        try:
            resp = await self._client.chat.completions.create(
                model=config.ai.model,
                messages=[
                    {"role": "system", "content": (
                        "输出2-3个搜索关键词，每行一个，不要任何解释:\n"
                        "- 从不同角度搜索（数据、新闻、社区）\n"
                        "- CS2相关加CS2前缀，比赛加年份\n"
                        f"- 日期: {GET_NOW_DATE()}"
                    )},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=100,
                temperature=0.3,
            )
            raw = (resp.choices[0].message.content or "").strip()
            ai_qs = [re.sub(r'^[\d\.\-\s]+', '', l).strip()
                     for l in raw.split('\n') if len(l.strip()) > 3]
            return ai_qs[:3]
        except Exception:
            return []

    # ==================== 对话 ====================

    async def chat(self, user_message: Optional[str] = None) -> str:
        if not self.enabled:
            return "AI 未启用"
        if user_message:
            self.add_message("user", user_message)
        try:
            response = await self._client.chat.completions.create(
                model=config.ai.model,
                messages=self._messages,
                max_tokens=1024,
                temperature=0.7,
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}}
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            logger.error(f"AI 请求失败: {e}")
            return f"AI 请求失败: {e}"

    async def chat_with_search(self, user_message: str) -> str:
        """联网搜索：多查询 -> 深度搜索 -> AI 合成"""
        if not self.enabled:
            return "AI 未启用"

        rule_queries = self._expand_queries(user_message)
        ai_queries = await self._ai_expand_queries(user_message)
        all_queries = list(dict.fromkeys(rule_queries + ai_queries))

        items = await self._deep_search(all_queries, per_q=12)
        if not items:
            return await self.chat(user_message)

        lines = []
        for i, it in enumerate(items[:30], 1):
            lines.append(f"{i}. [{it['title']}]({it['url']})\n   {it['snippet']}")

        final_prompt = (
            f"用户问题: {user_message}\n\n"
            f"以下是从搜索引擎获取的最新信息（共 {len(items)} 条，展示前 30 条）:\n\n"
            + "\n\n".join(lines) + "\n\n"
            f"当前日期: {GET_NOW_DATE()}\n"
            f"请基于以上搜索结果回答用户问题。如果搜索结果不充分，可结合你的 CS2 专业知识补充，"
            f"但要区分哪些是搜索到的信息、哪些是你的知识推断。不要编造具体数据。"
        )

        try:
            response = await self._client.chat.completions.create(
                model=config.ai.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": final_prompt},
                ],
                max_tokens=1500,
                temperature=0.7,
            )
            reply = (response.choices[0].message.content or "").strip()
            self.add_message("user", user_message)
            self.add_message("assistant", reply)
            return reply
        except Exception as e:
            logger.error(f"AI 请求失败: {e}")
            return f"AI 请求失败: {e}"

    def add_message(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})
        total = len(self._messages)
        if total > MAX_HISTORY + 1:
            remove_count = total - MAX_HISTORY + 1
            start = 1 if self._messages[0]["role"] == "system" else 0
            del self._messages[start : start + remove_count]

    def clear_history(self) -> None:
        system_msgs = [m for m in self._messages if m["role"] == "system"]
        self._messages = system_msgs if system_msgs else [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        logger.info("对话历史已清除")
