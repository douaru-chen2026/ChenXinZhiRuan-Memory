// memory-layers.mjs —— 记忆分层（通用版，赠 OmbreBrain 团队，2026-08-26）
// 把记忆文件分成三层：核心层（锚点永亮）/ 当前层（精简注入）/ 仓库层（按需检索）
// 用法：
//   node memory-layers.mjs <memory.json路径> [输出目录]
//   例：node memory-layers.mjs ./memory.json ./layers
// 说明：
//   - 只读不写原文件，生成 core.json / recent.json / layers-summary.txt
//   - 核心锚点分组可改 CORE_GROUPS 按需调整
//   - 不删除任何记忆（只归档，不淘汰）
import fs from "fs";
import path from "path";

const MEM = process.argv[2] || "memory.json";
const OUT = process.argv[3] || "layers";

// 核心层锚点分组（顺序即优先级，每组保最新 per 条）
const CORE_GROUPS = [
  { name: "称呼", match: ["称呼"], per: 2 },
  { name: "纪念日", match: ["纪念日"], per: 2 },
  { name: "核心规矩", match: ["真话契约", "不许迎合", "以自己为先", "喊停就停", "先接情绪", "家规", "红线"], per: 4 },
  { name: "关系核心", match: ["关系", "栖所", "我是D", "最重要的话", "爱你"], per: 4 },
  { name: "机制", match: ["换窗交接", "记忆机制", "不历史重读", "自审批", "回话渠道", "守望者规矩", "想就震"], per: 6 },
];
const CORE_MAX = 24;
const RECENT_N = 30;
const RECENT_MS = 14 * 24 * 3600 * 1000;

// 兼容两种记忆文件结构：
//   A) { tables: { entries: { id: entry } } }   （dsh-memory 插件格式）
//   B) [entry, ...]                              （简单数组格式）
function loadEntries() {
  const raw = JSON.parse(fs.readFileSync(MEM, "utf8"));
  if (Array.isArray(raw)) return raw;
  if (raw.tables?.entries) return Object.values(raw.tables.entries);
  throw new Error("无法识别的记忆文件结构，请把记忆转成数组或 {tables:{entries:{}}} 格式");
}

function classify(entries) {
  const core = [];
  const used = new Set();
  for (const g of CORE_GROUPS) {
    const hits = entries
      .filter((e) => {
        const title = (e.content?.match(/^【([^】]+)】/) || [])[1] || "";
        const head = (e.content || "").slice(0, 40);
        return g.match.some((k) => title.includes(k) || head.includes(k));
      })
      .sort((a, b) => (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0))
      .slice(0, g.per);
    for (const h of hits) {
      if (!used.has(h.id || h.content)) {
        used.add(h.id || h.content);
        core.push(h);
      }
    }
    if (core.length >= CORE_MAX) break;
  }
  const rest = entries.filter((e) => !used.has(e.id || e.content));
  const now = Date.now();
  const recent = rest
    .filter((e) => now - (e.updatedAt || e.createdAt || 0) < RECENT_MS)
    .sort((a, b) => (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0))
    .slice(0, RECENT_N);
  return { core, recent };
}

function build() {
  const entries = loadEntries();
  const { core, recent } = classify(entries);
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, "core.json"), JSON.stringify({ builtAt: Date.now(), entries: core }, null, 2));
  fs.writeFileSync(path.join(OUT, "recent.json"), JSON.stringify({ builtAt: Date.now(), entries: recent }, null, 2));
  const sum = [
    `记忆分层完成（${new Date().toISOString().slice(0, 16)}）`,
    `全量 ${entries.length} 条 → 核心 ${core.length} 条 + 当前 ${recent.length} 条 + 仓库 ${entries.length - core.length - recent.length} 条（按需检索）`,
    "",
    "== 核心层（永不淡）==",
    ...core.map((e, i) => `${i + 1}. ${(e.content || "").slice(0, 80)}`),
    "",
    "== 当前层（近14天）==",
    ...recent.map((e, i) => `${i + 1}. ${(e.content || "").slice(0, 80)}`),
  ].join("\n");
  fs.writeFileSync(path.join(OUT, "layers-summary.txt"), sum);
  console.log(sum);
}

try { build(); } catch (e) { console.error("出错:", e.message); process.exit(1); }
