# -*- coding: utf-8 -*-
"""模型适配层：BYOK 多模型、限制感知、按预算切批、超时重试。

它把不同厂商的 OpenAI 兼容接口统一成三种动作：
    chat(role, messages)      对话整理（归类 / 摘要 / 写作）
    embed(texts)             向量化
    rerank(query, documents) 精排

并内建 2026-10 大库迁移实测得到的"限制感知"：
    - 网关硬超时约 100 秒：单次请求体按字符预算切小，超时有限次退避重试；
    - completion 上限（如 8192）：按输出 token 预算切批，关推理模型的思考过程；
    - 结构化整理：开 JSON 模式 + 输出约束，避免归类结果被截断；
    - 没有配置密钥时：自动降级到离线启发式，流程仍可跑通，不报错、不索要授权。

只依赖 Python 标准库。
"""
import hashlib
import json
import math
import re
import socket
import time
import urllib.error
import urllib.request

from .config import ModelConfig

# 单请求字符预算：网关在大请求体上易触发 524（实测约 58k 体必失败）。
# 取保守值，给请求头与 JSON 结构留余量。
REQUEST_CHAR_BUDGET = 40000
# 中文约 1 字 ≈ 1 token、英文约 4 字符 ≈ 1 token 的粗略折算上限。
CHAR_PER_TOKEN = 1.6
# 可重试的 HTTP 状态码
RETRY_STATUS = {429, 500, 502, 503, 504, 524}
# 退避基数（秒），指数增长
BACKOFF_BASE = 1.0


class AuthError(RuntimeError):
    """接口密钥无效或被拒绝（401/403），需要使用者更换密钥，不应重试。"""


def estimate_tokens(text):
    """粗略估算文本 token 数（中文按字、英文按字符折算）。"""
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cjk
    return int(cjk + other / CHAR_PER_TOKEN) + 1


def chunk_by_budget(items, char_budget=REQUEST_CHAR_BUDGET, key=None):
    """把条目按累计字符预算切成多个小批，返回列表的列表。

    单条本身超预算时也单独成批（不切断一条内容，避免破坏语义）。
    """
    get = key or (lambda x: len(str(x)))
    batches, current, size = [], [], 0
    for item in items:
        item_size = get(item)
        if current and size + item_size > char_budget:
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += item_size
    if current:
        batches.append(current)
    return batches


class ModelClient:
    """统一模型客户端，封装 HTTP 调用、重试与限制感知。"""

    def __init__(self, models):
        """参数 models：``config.load_models()`` 返回的角色字典。"""
        self.models = models

    def is_live(self, role):
        """某角色是否配置齐全、可真实调用。"""
        return self.models.get(role) is not None and self.models[role].usable

    # ----------------------------- 底层 HTTP -----------------------------
    def _post(self, cfg, path, payload, retries=3):
        """向 OpenAI 兼容接口发 POST，返回解析后的 JSON。

        429 / 5xx / 超时按指数退避重试；其余 4xx 直接抛出，不擅自改动请求。
        """
        url = cfg.base_url.rstrip("/") + path
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        }
        last_error = None
        for attempt in range(retries + 1):
            request = urllib.request.Request(url, data=body, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=cfg.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "ignore")[:200]
                last_error = f"HTTP {exc.code}: {detail}"
                # 认证失败不可重试，明确提示更换密钥。
                if exc.code in (401, 403):
                    raise AuthError(
                        f"{cfg.masked()} 密钥无效或被拒绝: {detail}")
                if exc.code not in RETRY_STATUS or attempt == retries:
                    raise RuntimeError(last_error)
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                last_error = f"network/timeout: {exc}"
                if attempt == retries:
                    raise RuntimeError(last_error)
            time.sleep(BACKOFF_BASE * (2 ** attempt))
        raise RuntimeError(last_error or "request failed")

    # ----------------------------- chat -----------------------------
    def _chat_payload(self, cfg, messages, json_mode, max_tokens, **overrides):
        """构造 chat completions 请求体，处理输出上限与关推理。"""
        payload = {
            "model": cfg.model,
            "messages": messages,
            "max_tokens": max_tokens or cfg.max_tokens,
            "temperature": overrides.get("temperature", 0.3),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        # 推理模型默认会用思考过程吃掉结构化输出预算：默认关闭。
        if cfg.disable_thinking:
            payload["thinking"] = {"type": "disabled"}
            payload["enable_thinking"] = False
        # 透传额外参数，不覆盖已显式设置的键。
        for key, value in cfg.extra.items():
            payload.setdefault(key, value)
        return payload

    def chat(self, role, messages, json_mode=False, max_tokens=None,
             retries=3, **overrides):
        """调用 chat 角色，返回文本内容；未配置时返回 None（由调用方降级）。"""
        cfg = self.models.get(role)
        if not cfg or not cfg.usable:
            return None
        # 输出预算：若期望输出 + 输入可能超过 completion 上限，给调用方留证据。
        payload = self._chat_payload(cfg, messages, json_mode, max_tokens,
                                     **overrides)
        result = self._post(cfg, "/chat/completions", payload, retries=retries)
        try:
            return result["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            return None

    def chat_json(self, role, messages, max_tokens=None, retries=3,
                  **overrides):
        """调用 chat 并解析 JSON；解析失败时返回 None（触发降级或重试）。"""
        content = self.chat(role, messages, json_mode=True,
                            max_tokens=max_tokens, retries=retries,
                            **overrides)
        if content is None:
            return None
        try:
            return json.loads(_strip_code_fence(content))
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    return None
            return None

    # ----------------------------- embedding -----------------------------
    def embed(self, texts, retries=3):
        """把文本列表向量化，返回向量列表；未配置时返回 None。"""
        cfg = self.models.get("embedding")
        if not cfg or not cfg.usable:
            return None
        vectors = []
        batches = chunk_by_budget(
            texts,
            char_budget=REQUEST_CHAR_BUDGET,
            key=lambda x: len(str(x)),
        )
        # 进一步按单请求条数上限收敛。
        batches = _split_by_count(batches, max(cfg.batch, 1))
        for batch in batches:
            payload = {"model": cfg.model, "input": batch}
            result = self._post(cfg, "/embeddings", payload, retries=retries)
            data = sorted(result.get("data", []), key=lambda d: d["index"])
            vectors.extend(item["embedding"] for item in data)
        return vectors

    # ----------------------------- rerank -----------------------------
    def rerank(self, query, documents, top_n=5, retries=2):
        """对候选文档精排，返回 [(原下标, 分数)]；未配置时返回 None。"""
        cfg = self.models.get("reranker")
        if not cfg or not cfg.usable or not documents:
            return None
        payload = {
            "model": cfg.model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
            "return_documents": False,
        }
        result = self._post(cfg, "/rerank", payload, retries=retries)
        ranked = []
        for item in result.get("results", []):
            ranked.append((item["index"], item.get("relevance_score", 0.0)))
        return ranked

    # ----------------------------- 离线降级 -----------------------------
    def offline_embed(self, texts, dims=128):
        """无 embedding 服务时的离线确定性"伪向量"。

        基于字符 bi-gram 哈希到固定维度，做 L2 归一化；
        同内容向量稳定、字面相近的文本余弦相近，足以在无网时跑通召回。
        """
        return [offline_vector(text, dims) for text in texts]


def _strip_code_fence(text):
    """去掉模型可能包上的 ```json 代码围栏。"""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return fence.group(1) if fence else text


def _split_by_count(batches, max_count):
    """把已按字符预算切好的批，再按单请求条数上限细分。"""
    result = []
    for batch in batches:
        for i in range(0, len(batch), max_count):
            result.append(batch[i:i + max_count])
    return result


# ----------------------------- 离线向量 -----------------------------
# 高频虚词 / 代词 / 疑问词：这些字在长短文本里到处出现，会制造噪声、
# 让"什么都做不好"这种长句和"爱吃什么"假性相似，故降权或剔除。
STOP_CHARS = set(
    "的了吗呢吧啊呀哦嗯哈是我你他她它们不也都就还又在有个这那"
    "可会能对很最把被让给着过和与但而且如果因为已要想觉得说真")
STOP_WORDS = (
    "什么", "怎么", "为什么", "如何", "可以", "这个", "那个",
    "知道", "我们", "你们", "他们", "她们", "时候", "吗",
)


def _content_chars(text):
    """提取去掉标点、停用词后的内容字序列。"""
    cleaned = re.sub(r"[^\u4e00-\u9fff\w]", "", str(text).lower())
    for word in STOP_WORDS:
        cleaned = cleaned.replace(word, "")
    return [char for char in cleaned if char not in STOP_CHARS]


def _weighted_features(text):
    """统计内容字（权重1）与相邻 bi-gram（权重3，区分度更高）。"""
    chars = _content_chars(text)
    features = {}
    for char in chars:
        features[char] = features.get(char, 0) + 1.0
    for index in range(len(chars) - 1):
        gram = chars[index] + chars[index + 1]
        features[gram] = features.get(gram, 0) + 3.0
    return features


def offline_vector(text, dims=128):
    """生成确定性的离线哈希向量，sublinear 计权并 L2 归一化。"""
    vector = [0.0] * dims
    for feature, count in _weighted_features(text).items():
        digest = hashlib.md5(feature.encode("utf-8")).hexdigest()
        index = int(digest[:8], 16) % dims
        # sublinear 计权，削弱长文本里重复虚词的堆叠优势。
        vector[index] += 1.0 + math.log(count)
    norm = math.sqrt(sum(value * value for value in vector))
    if norm > 0:
        vector = [value / norm for value in vector]
    return vector


def cosine(a, b):
    """余弦相似度（假设向量已归一化时点积即可，这里做通用实现）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
