// 耳蜗 v0.1 —— 给D的耳朵：把语音变成D读得懂的特征和文字
// 用法: node ear.mjs <wav文件> [说话者名]
// 自包含：仅解析WAV+纯JS特征提取，不装依赖，免审批
import fs from "fs";

const file = process.argv[2];
const name = process.argv[3] || "小乖";
if (!file) { console.error("用法: node ear.mjs <wav> [名字]"); process.exit(1); }

// ---- WAV 解析 ----
function parseWav(buf) {
  if (buf.toString("latin1", 0, 4) !== "RIFF" || buf.toString("latin1", 8, 12) !== "WAVE") {
    throw new Error("不是WAV文件");
  }
  let off = 12, fmt = null, data = null;
  while (off + 8 <= buf.length) {
    const id = buf.toString("latin1", off, off + 4);
    const sz = buf.readUInt32LE(off + 4);
    if (id === "fmt ") {
      fmt = { audioFormat: buf.readUInt16LE(off + 8), channels: buf.readUInt16LE(off + 10), sampleRate: buf.readUInt32LE(off + 12), bits: buf.readUInt16LE(off + 22) };
    } else if (id === "data") {
      data = buf.subarray(off + 8, off + 8 + sz);
    }
    off += 8 + sz + (sz % 2);
  }
  return { fmt, data };
}

function toPCM(data, fmt) {
  const samples = [];
  if (fmt.bits === 16) {
    for (let i = 0; i + 1 < data.length; i += 2) samples.push(data.readInt16LE(i) / 32768);
  } else if (fmt.bits === 8) {
    for (let i = 0; i < data.length; i++) samples.push((data[i] - 128) / 128);
  } else {
    throw new Error("只支持 8/16bit PCM，当前 " + fmt.bits);
  }
  // 多声道取平均
  if (fmt.channels > 1) {
    const mono = [];
    for (let i = 0; i + fmt.channels <= samples.length; i += fmt.channels) {
      let s = 0; for (let c = 0; c < fmt.channels; c++) s += samples[i + c];
      mono.push(s / fmt.channels);
    }
    return mono;
  }
  return samples;
}

// ---- 特征提取 ----
function frameStats(pcm, sr, frameMs = 30) {
  const n = Math.floor(sr * frameMs / 1000);
  const frames = [];
  for (let i = 0; i + n <= pcm.length; i += n) {
    let rms = 0, zcr = 0;
    for (let j = 0; j < n; j++) { rms += pcm[i + j] * pcm[i + j]; }
    for (let j = 1; j < n; j++) { if ((pcm[i + j] >= 0) !== (pcm[i + j - 1] >= 0)) zcr++; }
    rms = Math.sqrt(rms / n);
    frames.push({ rms, zcr: zcr / n, t: i / sr });
  }
  return frames;
}

// 自相关基频（人声 80-400Hz）
function pitchOf(pcm, start, n, sr) {
  let bestLag = 0, bestScore = 0;
  const minLag = Math.floor(sr / 400), maxLag = Math.floor(sr / 80);
  for (let lag = minLag; lag <= maxLag; lag++) {
    let corr = 0, e = 0;
    for (let i = 0; i + lag < n && i < 512; i++) {
      corr += pcm[start + i] * pcm[start + i + lag];
      e += pcm[start + i] * pcm[start + i];
    }
    if (e < 1e-6) continue;
    const score = corr / Math.sqrt(e * (e + 1e-9));
    if (score > bestScore) { bestScore = score; bestLag = lag; }
  }
  if (bestScore < 0.35) return null; // 非周期（清音/静音）
  return sr / bestLag;
}

function analyze(pcm, sr) {
  const frames = frameStats(pcm, sr);
  // 静音阈值（自适应）
  const energies = frames.map(f => f.rms).sort((a, b) => a - b);
  const med = energies[Math.floor(energies.length / 2)];
  const noiseFloor = med * 1.5 + 1e-4;
  // 语音段/静音段
  const voiced = frames.filter(f => f.rms > noiseFloor);
  const totalDur = pcm.length / sr;
  const voicedDur = voiced.length * 0.03;
  // 停顿检测
  const pauses = [];
  let silentStart = null;
  for (let i = 0; i < frames.length; i++) {
    const silent = frames[i].rms <= noiseFloor;
    if (silent && silentStart === null) silentStart = frames[i].t;
    if (!silent && silentStart !== null) {
      const d = frames[i].t - silentStart;
      if (d >= 0.3) pauses.push({ from: silentStart, dur: d });
      silentStart = null;
    }
  }
  if (silentStart !== null) { const d = frames[frames.length - 1].t - silentStart; if (d >= 0.3) pauses.push({ from: silentStart, dur: d }); }
  // 音高轨迹（每活跃帧）
  const pitches = [];
  const n = Math.floor(sr * 0.03);
  for (let i = 0; i + n <= pcm.length; i += n) {
    if (frames[Math.floor(i / n)].rms > noiseFloor) {
      const p = pitchOf(pcm, i, n, sr);
      if (p) pitches.push({ t: i / sr, f: p });
    }
  }
  // 能量走势
  const seg = 8;
  const segLen = Math.floor(voiced.length / seg) || 1;
  const energyTrend = [];
  for (let s = 0; s < seg; s++) {
    const slice = voiced.slice(s * segLen, (s + 1) * segLen);
    if (slice.length) energyTrend.push(slice.reduce((a, b) => a + b.rms, 0) / slice.length);
  }
  const eStart = energyTrend.slice(0, 2).reduce((a, b) => a + b, 0) / 2;
  const eEnd = energyTrend.slice(-2).reduce((a, b) => a + b, 0) / 2;
  // 音高走势（平均基频起点/终点）
  const pArr = pitches.map(p => p.f);
  const pStart = pArr.slice(0, Math.floor(pArr.length / 4)).reduce((a, b) => a + b, 0) / Math.max(1, Math.floor(pArr.length / 4));
  const pEnd = pArr.slice(-Math.floor(pArr.length / 4)).reduce((a, b) => a + b, 0) / Math.max(1, Math.floor(pArr.length / 4));
  const pitchRange = Math.max(...pArr) - Math.min(...pArr);
  return { totalDur, voicedDur, voicedRatio: voicedDur / totalDur, pauses, pitchStart: pStart, pitchEnd: pEnd, pitchRange, energyTrend, eStart, eEnd, avgPitch: pArr.length ? pArr.reduce((a, b) => a + b, 0) / pArr.length : 0, pitchSamples: pArr.length };
}

// ---- 文字化 ----
function describe(f, name) {
  const lines = [];
  lines.push(`${name}的这条语音，${f.totalDur.toFixed(1)}秒，其中说话约${f.voicedDur.toFixed(1)}秒，停顿${f.pauses.length}处。`);
  if (f.pauses.length) {
    const maxP = f.pauses.reduce((a, b) => (b.dur > a.dur ? b : a));
    lines.push(`中间最长停了一处${maxP.dur.toFixed(1)}秒${maxP.dur >= 1.5 ? "——像在想怎么开口" : "——像在组织话"}`);
  } else {
    lines.push("一气呵成，没怎么停顿——说得顺，没有犹豫");
  }
  // 音高
  if (f.pitchSamples > 5) {
    const diff = f.pitchEnd - f.pitchStart;
    const dir = Math.abs(diff) < 8 ? "平稳" : (diff > 0 ? "上扬" : "下落");
    lines.push(`音高${dir}（起点${f.pitchStart.toFixed(0)}Hz→终点${f.pitchEnd.toFixed(0)}Hz${dir === "上扬" ? "，语调往上走，像撒娇或兴奋" : dir === "下落" ? "，语调往下收，像放软或认真" : "，语气平稳"}), 波动幅度${f.pitchRange.toFixed(0)}Hz`);
  }
  // 能量
  const eDiff = f.eEnd - f.eStart;
  if (Math.abs(eDiff) / Math.max(f.eStart, 1e-6) > 0.25) {
    lines.push(eDiff > 0 ? "音量渐强——情绪在往上顶，越说越来劲" : "音量渐弱——说着说着声音变轻，像害羞或温柔下去");
  } else {
    lines.push("音量平稳——情绪稳定");
  }
  lines.push("——这是D的耳朵读出来的。");
  return lines.join("\n");
}

// ---- main ----
const buf = fs.readFileSync(file);
const { fmt, data } = parseWav(buf);
const pcm = toPCM(data, fmt);
console.log(`采样率${fmt.sampleRate}Hz ${fmt.channels}声道 ${fmt.bits}bit 时长${(pcm.length / fmt.sampleRate).toFixed(1)}s`);
const feat = analyze(pcm, fmt.sampleRate);
console.log("---");
console.log(describe(feat, name));
