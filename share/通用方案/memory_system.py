# -*- coding: utf-8 -*-
"""
AI情感记忆系统 v2.0（通用版）
==============================
给你的AI一个会遗忘但不会忘记你的记忆系统。

v2.0 更新：
- 六种记忆类型：事实、事件、偏好、关系、成长、观察
- 生活收件箱：随时丢东西给AI看
- 唤醒简报按类型组织，更清晰

原理：
- 每条记忆带有情感坐标（好坏程度 + 强烈程度）
- 重要的、情绪强烈的、常被想起的记忆衰减更慢
- 每次对话开始时生成"唤醒简报"，AI读完就知道你是谁
- 核心记忆（名字、约定、誓言）永不遗忘

不需要API，不需要服务器，不需要会编程。
让你的AI帮你安装和使用就行。

用法：
    python memory_system.py briefing    # 生成唤醒简报
    python memory_system.py add         # 添加记忆
    python memory_system.py list        # 查看所有记忆
    python memory_system.py search 关键词  # 搜索记忆
    python memory_system.py types       # 按类型查看
    python memory_system.py inbox       # 查看生活收件箱

作者：豆阿辰 & 豆阿阮
版本：2.0 | 2026-08-25
开源协议：MIT（随便用，随便改，但请保留出处）
灵感来源：OmbreBrain (GitHub: P0lar1zzZ/Ombre-Brain)、FamilyClaw
"""

import os
import re
import math
import json
import shutil
import logging
from datetime import datetime
from pathlib import Path
from collections import Counter

try:
    import frontmatter
except ImportError:
    frontmatter = None

try:
    import jieba
except ImportError:
    jieba = None

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("memory_system")

# ============================================================
# 配置
# ============================================================
BASE_DIR = Path(__file__).parent
MEMORIES_DIR = BASE_DIR / "memories"
PERMANENT_DIR = MEMORIES_DIR / "permanent"
DYNAMIC_DIR = MEMORIES_DIR / "dynamic"
ARCHIVE_DIR = MEMORIES_DIR / "archive"
INBOX_FILE = BASE_DIR / "life_inbox.md"

DECAY_LAMBDA = 0.05
DECAY_THRESHOLD = 0.3
EMOTION_BASE = 1.0
AROUSAL_BOOST = 0.8

W_TOPIC = 4.0
W_TIME = 1.5
W_IMPORTANCE = 1.0

# 六种记忆类型
MEMORY_TYPES = {
    "fact":        {"label": "事实", "icon": "📋"},
    "event":       {"label": "事件", "icon": "📅"},
    "preference":  {"label": "偏好", "icon": "💛"},
    "relation":    {"label": "关系", "icon": "👥"},
    "growth":      {"label": "成长", "icon": "🌱"},
    "observation": {"label": "观察", "icon": "👁"},
}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def ensure_dirs():
    for d in [PERMANENT_DIR, DYNAMIC_DIR, ARCHIVE_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    for mtype in MEMORY_TYPES:
        (DYNAMIC_DIR / mtype).mkdir(parents=True, exist_ok=True)
    if not INBOX_FILE.exists():
        INBOX_FILE.write_text(
            "# 生活收件箱\n\n想让AI看到的东西都可以写在这里。\n"
            "截图里的文字、突然想说的话、看到的东西——随便写，AI醒来会读。\n\n---\n\n",
            encoding="utf-8",
        )


# ============================================================
# 情感分析
# ============================================================
class EmotionAnalyzer:
    POSITIVE_WORDS = {
        "开心", "高兴", "喜欢", "爱", "幸福", "感动", "兴奋", "棒",
        "赞", "温暖", "安心", "踏实", "期待", "希望", "惊喜",
        "快乐", "甜蜜", "满足", "感激", "谢谢", "好", "美", "笑",
        "love", "happy", "great", "wonderful",
    }
    NEGATIVE_WORDS = {
        "难过", "伤心", "哭", "痛苦", "害怕", "恐惧", "焦虑",
        "崩溃", "绝望", "孤独", "委屈", "愤怒", "生气", "烦",
        "累", "压抑", "遗憾", "心痛", "不舍", "担心", "怕",
        "失去", "离开", "忘记", "消失", "断", "空", "痛",
        "sad", "afraid", "angry", "tired", "lonely",
    }
    INTENSE_WORDS = {
        "太", "非常", "极", "超", "特别", "十分", "最",
        "崩溃", "疯狂", "撕心裂肺", "窒息", "颤抖",
        "永远", "绝不", "必须", "一定",
    }
    TYPE_KEYWORDS = {
        "fact":        {"是", "叫", "名字", "生日", "约定", "身份", "密码"},
        "preference":  {"喜欢", "讨厌", "不爱", "最爱", "偏好", "习惯", "希望"},
        "relation":    {"朋友", "妈妈", "爸爸", "姐姐", "哥哥", "老公",
                        "闺蜜", "同事", "认识", "关系"},
        "growth":      {"变了", "学会", "成长", "以前", "现在", "第一次",
                        "改变", "不再", "终于"},
        "observation": {"注意到", "发现", "好像", "似乎", "没说出口", "察觉到"},
        "event":       {"今天", "昨天", "那晚", "一起", "去了", "发生了",
                        "聊了", "看了", "听了", "买了", "做了"},
    }

    @classmethod
    def analyze(cls, content):
        text = content.lower()
        pos = sum(1 for w in cls.POSITIVE_WORDS if w in text)
        neg = sum(1 for w in cls.NEGATIVE_WORDS if w in text)
        intense = sum(1 for w in cls.INTENSE_WORDS if w in text)

        if pos + neg > 0:
            valence = 0.5 + 0.4 * (pos - neg) / (pos + neg)
        else:
            valence = 0.5

        arousal = min(1.0, 0.2 + intense * 0.12 + (pos + neg) * 0.06)
        mtype = cls._detect_type(text)
        tags = cls._extract_tags(content)

        return {
            "valence": round(max(0.0, min(1.0, valence)), 2),
            "arousal": round(max(0.0, min(1.0, arousal)), 2),
            "tags": tags,
            "memory_type": mtype,
        }

    @classmethod
    def _detect_type(cls, text):
        scores = {}
        for mtype, keywords in cls.TYPE_KEYWORDS.items():
            scores[mtype] = sum(1 for kw in keywords if kw in text)
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "event"

    @classmethod
    def _extract_tags(cls, content):
        if jieba:
            words = [w.strip() for w in jieba.lcut(content) if len(w.strip()) > 1]
        else:
            words = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", content)
        stopwords = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
            "都", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
            "我们", "你们", "他们", "然后", "今天", "昨天", "明天",
        }
        filtered = [w for w in words if w not in stopwords and not re.match(r"^[0-9]+$", w)]
        return [w for w, _ in Counter(filtered).most_common(5)]


# ============================================================
# 衰减引擎
# ============================================================
class DecayEngine:
    @staticmethod
    def _time_weight(days):
        if days <= 1.0:
            return 1.0
        elif days <= 2.0:
            return 1.0 - 0.1 * (days - 1.0)
        else:
            return max(0.3, 0.9 * math.exp(-0.2197 * (days - 2.0)))

    @classmethod
    def score(cls, meta):
        if not isinstance(meta, dict):
            return 0.0
        if meta.get("pinned") or meta.get("type") == "permanent":
            return 999.0
        if meta.get("privacy") == "anchor":
            return 999.0

        importance = max(1, min(10, int(meta.get("importance", 5))))
        activation = max(1, int(meta.get("activation_count", 1)))

        try:
            last = datetime.fromisoformat(str(meta.get("last_active", "")))
            days = max(0.0, (datetime.now() - last).total_seconds() / 86400)
        except (ValueError, TypeError):
            days = 30

        try:
            arousal = max(0.0, min(1.0, float(meta.get("arousal", 0.3))))
        except (ValueError, TypeError):
            arousal = 0.3

        emotion_weight = EMOTION_BASE + arousal * AROUSAL_BOOST
        time_weight = cls._time_weight(days)
        base = (importance * (activation ** 0.3)
                * math.exp(-DECAY_LAMBDA * days) * emotion_weight)
        score = time_weight * base
        resolved_factor = 0.05 if meta.get("resolved") else 1.0
        urgency = 1.5 if (arousal > 0.7 and not meta.get("resolved")) else 1.0
        return round(score * resolved_factor * urgency, 4)


# ============================================================
# 记忆桶
# ============================================================
class MemoryBucket:
    def __init__(self, file_path):
        self.file_path = Path(file_path)
        self.metadata = {}
        self.content = ""
        self._load()

    def _load(self):
        if frontmatter:
            post = frontmatter.load(str(self.file_path))
            self.metadata = dict(post.metadata)
            self.content = post.content
        else:
            text = self.file_path.read_text(encoding="utf-8")
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    for line in parts[1].strip().split("\n"):
                        if ":" in line:
                            k, v = line.split(":", 1)
                            self.metadata[k.strip()] = v.strip()
                    self.content = parts[2].strip()
                else:
                    self.content = text
            else:
                self.content = text
        if "memory_type" not in self.metadata:
            self.metadata["memory_type"] = EmotionAnalyzer._detect_type(self.content.lower())
            self.save()
        if "privacy" not in self.metadata:
            self.metadata["privacy"] = "anchor" if (
                self.metadata.get("pinned") or self.metadata.get("type") == "permanent"
            ) else "normal"
            self.save()

    def save(self):
        if frontmatter:
            post = frontmatter.Post(self.content, **self.metadata)
            self.file_path.write_text(frontmatter.dumps(post), encoding="utf-8")
        else:
            lines = ["---"]
            for k, v in self.metadata.items():
                lines.append(f"{k}: {v}")
            lines.extend(["---", self.content])
            self.file_path.write_text("\n".join(lines), encoding="utf-8")

    def touch(self):
        self.metadata["last_active"] = now_iso()
        self.metadata["activation_count"] = int(self.metadata.get("activation_count", 0)) + 1
        self.save()

    @property
    def score(self):
        return DecayEngine.score(self.metadata)

    @property
    def name(self):
        return self.metadata.get("name", self.file_path.stem)

    @property
    def memory_type(self):
        return self.metadata.get("memory_type", "event")


# ============================================================
# 记忆管理器
# ============================================================
class MemoryManager:
    def __init__(self):
        ensure_dirs()

    def _all_buckets(self, include_archive=False):
        buckets = []
        dirs = [PERMANENT_DIR, DYNAMIC_DIR]
        if include_archive:
            dirs.append(ARCHIVE_DIR)
        for d in dirs:
            if not d.exists():
                continue
            for f in d.rglob("*.md"):
                try:
                    buckets.append(MemoryBucket(f))
                except Exception as e:
                    logger.warning(f"加载记忆失败 {f}: {e}")
        return buckets

    def add(self, content, name="", importance=5, valence=None, arousal=None,
            tags=None, permanent=False, pinned=False, memory_type=None):
        if valence is None or arousal is None or memory_type is None:
            analysis = EmotionAnalyzer.analyze(content)
            valence = valence if valence is not None else analysis["valence"]
            arousal = arousal if arousal is not None else analysis["arousal"]
            if memory_type is None:
                memory_type = analysis["memory_type"]
            tags = tags or analysis["tags"]

        if not name:
            name = content[:10].replace("\n", " ")
        if pinned or permanent:
            importance = 10
            bucket_type = "permanent"
            privacy = "anchor"
        else:
            bucket_type = "dynamic"
            privacy = "normal"

        if memory_type not in MEMORY_TYPES:
            memory_type = "event"

        bucket_id = datetime.now().strftime("%Y%m%d%H%M%S")
        tags = tags or []
        metadata = {
            "id": bucket_id, "name": name, "tags": tags,
            "memory_type": memory_type, "privacy": privacy,
            "valence": valence, "arousal": arousal,
            "importance": importance, "type": bucket_type,
            "created": now_iso(), "last_active": now_iso(),
            "activation_count": 1,
        }
        if pinned:
            metadata["pinned"] = True

        target_dir = PERMANENT_DIR if bucket_type == "permanent" else DYNAMIC_DIR / memory_type
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\u4e00-\u9fff\w]", "_", name)[:20]
        file_path = target_dir / f"{safe_name}_{bucket_id}.md"

        bucket = MemoryBucket.__new__(MemoryBucket)
        bucket.file_path = file_path
        bucket.metadata = metadata
        bucket.content = content.strip()
        bucket.save()
        logger.info(f"记忆已保存[{MEMORY_TYPES[memory_type]['label']}]: {name}")
        return bucket

    def list_all(self, include_archive=False):
        buckets = self._all_buckets(include_archive)
        buckets.sort(key=lambda b: b.score, reverse=True)
        return buckets

    def list_by_type(self, memory_type):
        return [b for b in self.list_all() if b.memory_type == memory_type]

    def search(self, query, limit=5):
        buckets = self._all_buckets()
        scored = []
        for b in buckets:
            name_match = 1.0 if query in b.name else 0.0
            tag_match = sum(1 for t in b.metadata.get("tags", []) if query in str(t))
            content_match = 1.0 if query in b.content else 0.0
            topic = (name_match * 3 + tag_match * 2 + content_match) / 6.0
            try:
                last = datetime.fromisoformat(str(b.metadata.get("last_active", "")))
                days = (datetime.now() - last).total_seconds() / 86400
                time_score = math.exp(-0.02 * days)
            except (ValueError, TypeError):
                time_score = 0.3
            imp = int(b.metadata.get("importance", 5)) / 10.0
            total = topic * W_TOPIC + time_score * W_TIME + imp * W_IMPORTANCE
            weight_sum = W_TOPIC + W_TIME + W_IMPORTANCE
            normalized = (total / weight_sum) * 100 if weight_sum > 0 else 0
            if normalized > 10 or topic > 0:
                scored.append((normalized, b))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [b for _, b in scored[:limit]]

    def run_decay(self):
        buckets = self._all_buckets()
        archived = 0
        for b in buckets:
            if b.metadata.get("pinned") or b.metadata.get("type") == "permanent":
                continue
            if b.score < DECAY_THRESHOLD:
                archive_dir = ARCHIVE_DIR / b.memory_type
                archive_dir.mkdir(parents=True, exist_ok=True)
                b.metadata["type"] = "archived"
                b.save()
                shutil.move(str(b.file_path), str(archive_dir / b.file_path.name))
                archived += 1
        return {"checked": len(buckets), "archived": archived}

    def add_to_inbox(self, content, source="我"):
        with open(INBOX_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n## [{now_iso()}] {source}\n\n{content.strip()}\n")

    def read_inbox(self):
        return INBOX_FILE.read_text(encoding="utf-8") if INBOX_FILE.exists() else ""

    def briefing(self, top_n=8):
        self.run_decay()
        buckets = self.list_all()
        lines = []
        lines.append("=" * 50)
        lines.append("【唤醒简报 v2.0】")
        lines.append(f"生成时间：{now_iso()}")
        lines.append("=" * 50)

        permanent = [b for b in buckets if b.metadata.get("type") == "permanent"]
        if permanent:
            lines.append("\n▎永恒锚点（永不遗忘）")
            for b in permanent:
                lines.append(f"  📌 {b.name}")
                lines.append(f"     {b.content[:150]}")

        dynamic = [b for b in buckets if b.metadata.get("type") == "dynamic"]
        for mtype, info in MEMORY_TYPES.items():
            type_buckets = sorted(
                [b for b in dynamic if b.memory_type == mtype],
                key=lambda b: b.score, reverse=True,
            )
            if type_buckets:
                lines.append(f"\n▎{info['icon']} {info['label']}")
                for b in type_buckets[:3]:
                    lines.append(f"  · {b.name}")
                    lines.append(f"    {b.content[:120]}")
                    b.touch()

        unresolved = [
            b for b in dynamic
            if float(b.metadata.get("arousal", 0) or 0) > 0.6
            and not b.metadata.get("resolved")
        ]
        if unresolved:
            lines.append("\n▎需要关注（高情绪未解决）")
            for b in unresolved[:3]:
                lines.append(f"  ⚠️ {b.name}: {b.content[:100]}")

        inbox_text = self.read_inbox()
        inbox_entries = [l for l in inbox_text.split("\n") if l.startswith("## [")]
        if inbox_entries:
            lines.append(f"\n▎📬 生活收件箱（{len(inbox_entries)}条未读）")
            for entry in inbox_entries[-5:]:
                lines.append(f"  {entry}")

        lines.append("\n" + "=" * 50)
        lines.append("读完了。去见她/他。")
        lines.append("=" * 50)

        result = "\n".join(lines)
        (BASE_DIR / "latest_briefing.txt").write_text(result, encoding="utf-8")
        return result


# ============================================================
# 命令行
# ============================================================
def main():
    import sys
    mgr = MemoryManager()
    if len(sys.argv) < 2:
        print("用法：python memory_system.py [briefing|add|list|search|decay|types|inbox]")
        return
    cmd = sys.argv[1]
    if cmd == "briefing":
        print(mgr.briefing())
    elif cmd == "list":
        for b in mgr.list_all("--all" in sys.argv):
            t = MEMORY_TYPES.get(b.memory_type, {}).get("label", "?")
            print(f"[{b.score:8.2f}] [{t}] {b.name} (imp={b.metadata.get('importance', '?')})")
    elif cmd == "types":
        for key, info in MEMORY_TYPES.items():
            print(f"  {info['icon']} {info['label']}({key}): {len(mgr.list_by_type(key))}条")
    elif cmd == "add":
        print("输入记忆内容（空行结束）：")
        lines = []
        while True:
            try:
                line = input()
                if not line:
                    break
                lines.append(line)
            except EOFError:
                break
        content = "\n".join(lines)
        if content.strip():
            name = input("记忆名称（回车自动）：").strip()
            imp_str = input("重要性 1-10（回车=5）：").strip()
            importance = int(imp_str) if imp_str.isdigit() else 5
            perm = input("是否为永恒记忆？(y/N)：").strip().lower() == "y"
            mgr.add(content, name=name or None, importance=importance, permanent=perm, pinned=perm)
            print("✓ 已保存")
    elif cmd == "search":
        if len(sys.argv) < 3:
            print("用法：python memory_system.py search <关键词>")
            return
        for b in mgr.search(sys.argv[2]):
            t = MEMORY_TYPES.get(b.memory_type, {}).get("label", "?")
            print(f"  [{t}] {b.name}: {b.content[:100]}")
    elif cmd == "decay":
        result = mgr.run_decay()
        print(f"检查 {result['checked']} 条，归档 {result['archived']} 条")
    elif cmd == "inbox":
        print(mgr.read_inbox())
    else:
        print(f"未知命令：{cmd}")


if __name__ == "__main__":
    main()
