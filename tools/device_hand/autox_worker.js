/*
 * autox_worker.js —— 豆阿辰的"手"：专用安卓真机上的常驻工人（AutoX.js）。
 * --------------------------------------------------------------------------
 * 它干的事：每隔几秒去守夜机 device_gateway 领一个任务，在这台已经登录
 * 小红书 App 的真机上替豆阿辰操作（读群 / 发文字 / 发图发表情包 / 回 UI 树），
 * 干完把结果回传。手机只走家里宽带（住宅网络），账号只在这一台登，最干净。
 *
 * 运行环境：AutoX.js（开源版，6.x 以上），需开启「无障碍服务」+ 允许后台运行。
 *
 * 安全：
 *  - 守夜机地址、gateway 口令【绝不写死在这份文件里】（这文件要进公开记忆河）；
 *    首次运行会弹窗让你填，只存在这台手机的本地 storage，不上传、不入库。
 *  - 手机只执行网关白名单里的指令；发不发由守夜机的"发送双闸"决定，手机端
 *    不自己决定发什么，只做执行 + 回传，任何一步失败都如实回错、绝不乱点。
 *
 * 版本说明（诚实标注）：
 *  - ping / dump_ui 与设备无关，装好就能用，先用它们联调通道；
 *  - read_group / send_group / send_group_image 里凡是标 CALIB 的选择器，
 *    是按小红书常见界面写的多套兜底，具体 resource-id/文案可能随 App 版本变；
 *    真机联调时用 dump_ui 回传的界面树把这些选择器校准即可，结构不用动。
 * --------------------------------------------------------------------------
 */

"auto"; // 自动申请无障碍服务

// ============ 配置（真值只进手机本地 storage，不写死、不入库） ============
var DEFAULT_CFG = {
    host: "",            // 守夜机公网 IP 或域名（阿辰私下给，别填进任何公开处）
    gatewayPort: 37961,  // device_gateway 公网面端口
    token: "",           // device_gateway 口令（阿辰私下给）
    device: "oppo-aruan",// 设备名，必须在守夜机白名单 DEVICE_ALLOW 里
    group: "辰星港",      // 默认操作的群（按手机端看到的群标题匹配）
    xhsPkg: "com.xingin.xhs", // 小红书包名
    pollGapMs: 1500,     // 领完一轮后的间隔
    stepMs: 8000         // 单个界面步骤的等待上限
};
var WORKER_VERSION = "v712"; // 工人脚本版本号，随ping/dump回传，便于确认手机真跑的是哪版
var STORE = storages.create("achen_hand");
var CFG = loadConfig();

function loadConfig() {
    var cfg = {};
    Object.keys(DEFAULT_CFG).forEach(function (k) {
        cfg[k] = STORE.get(k, DEFAULT_CFG[k]);
    });
    return cfg;
}

// 每次启动都逐项把配置弹出来确认（输入框预填上次的值：对就直接确定，错就改，
// token 这类长串建议从取件箱文本里长按复制粘贴，避免手抄错一两个字符被秒拒）
function ensureConfig() {
    var fields = [["host", "守夜机地址（IP 或域名）"], ["token", "device_gateway 口令"]];
    fields.forEach(function (item) {
        var v = dialogs.rawInput("配置 · " + item[1] + "\n（只存在本机，不上传；长按输入框可粘贴）", CFG[item[0]] || "");
        if (v === null) v = "";
        CFG[item[0]] = v.trim();
        STORE.put(item[0], CFG[item[0]]);
    });
    if (!CFG.host || !CFG.token) {
        toast("配置没填全，工人先歇着");
        exit();
    }
}

function apiBase() {
    return "http://" + CFG.host + ":" + CFG.gatewayPort;
}
function enc(s) { return encodeURIComponent(String(s == null ? "" : s)); }

// ============ 与守夜机通信 ============
function fetchJob() {
    var url = apiBase() + "/job?token=" + enc(CFG.token) +
              "&device=" + enc(CFG.device) + "&_=" + Date.now();
    var resp = http.get(url, { timeout: 30000 }); // 服务端长轮询，hold 住
    var body = resp.body.json();
    return body ? body.job : null;
}
function report(jobId, result) {
    var url = apiBase() + "/result?token=" + enc(CFG.token) +
              "&device=" + enc(CFG.device);
    http.postJson(url, { job_id: jobId, result: result }, { timeout: 10000 });
}

// ============ 界面小工具 ============
// 给一组候选选择器，谁先找到返回谁（UiObject）；都找不到返回 null。
// 候选统一传函数，避免一上来就查询。
function waitAny(builders, timeout) {
    var deadline = Date.now() + (timeout || CFG.stepMs);
    while (Date.now() < deadline) {
        for (var i = 0; i < builders.length; i++) {
            var w = null;
            try { w = builders[i]().findOnce(); } catch (e) { w = null; }
            if (w) return w;
        }
        sleep(250);
    }
    return null;
}
function clickWidget(w) {
    if (!w) return false;
    if (w.clickable()) { w.click(); return true; }
    var b = w.bounds();
    return click(b.centerX(), b.centerY());
}
// 确保小红书在前台
function bringXhs() {
    if (currentPackage() !== CFG.xhsPkg) {
        app.launchPackage(CFG.xhsPkg);
        sleep(2500);
    }
}
// 进入目标群聊：在「消息」页点群名。已在聊天页就直接返回 true。
function enterGroup() {
    bringXhs();
    // 已在群聊：能找到输入框即视为在聊天界面 /*CALIB*/
    var input = waitAny([
        function () { return className("android.widget.EditText"); },
        function () { return text("说点什么..."); },
        function () { return desc("输入框"); }
    ], 4000);
    if (input) return true;

    // 先到「消息」tab /*CALIB*/
    var msgTab = waitAny([
        function () { return text("消息"); },
        function () { return desc("消息"); }
    ], 3000);
    if (msgTab) clickWidget(msgTab);
    sleep(1200);

    var g = waitAny([
        function () { return text(CFG.group); },
        function () { return desc(CFG.group); }
    ], CFG.stepMs);
    if (!g) return false;
    clickWidget(g);
    sleep(1500);
    return true;
}

// ============ 各指令 ============
function doPing() {
    return { ok: true, pong: 1, ver: WORKER_VERSION, ts: Date.now(), pkg: currentPackage() };
}
// v707：这版AutoX的节点对象没有.xml()，自己递归遍历节点、亲手拼出整棵界面树XML
function escXml(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function callStr(n, names) {
    for (var i = 0; i < names.length; i++) {
        try { var f = n[names[i]]; if (typeof f === "function") { var v = f.call(n); if (v != null) return String(v); } } catch (e) {}
    }
    return "";
}
function callBool(n, names) {
    for (var i = 0; i < names.length; i++) {
        try { var f = n[names[i]]; if (typeof f === "function") return !!f.call(n); } catch (e) {}
    }
    return false;
}
var __dumpCount = 0;
function nodeToXml(n, depth) {
    if (!n || depth > 48 || __dumpCount > 4000) return "";
    __dumpCount++;
    var cls = callStr(n, ["className"]);
    var t = callStr(n, ["text"]);
    var desc = callStr(n, ["desc", "contentDescription"]);
    var rid = callStr(n, ["id", "resourceId"]);
    var bl = 0, bt = 0, br = 0, bb = 0;
    try { var rc = n.bounds(); if (rc) { bl = rc.left; bt = rc.top; br = rc.right; bb = rc.bottom; } } catch (e) {}
    var ck = callBool(n, ["clickable"]), sc = callBool(n, ["scrollable"]), en = callBool(n, ["enabled"]), fo = callBool(n, ["focused"]);
    var out = '<node class="' + escXml(cls) + '" text="' + escXml(t) + '" content-desc="' + escXml(desc) +
        '" resource-id="' + escXml(rid) + '" clickable="' + ck + '" scrollable="' + sc + '" enabled="' + en +
        '" focused="' + fo + '" bounds="[' + bl + ',' + bt + '][' + br + ',' + bb + ']">';
    var cc = 0;
    try { cc = n.childCount(); } catch (e) { cc = 0; }
    for (var i = 0; i < cc; i++) { try { out += nodeToXml(n.child(i), depth + 1); } catch (e) {} }
    return out + "</node>";
}
function dumpHierarchy() {
    __dumpCount = 0;
    var root = null, dbg = [];
    try { var wr = auto.windowRoots; if (wr && wr.length) { root = wr[0]; dbg.push("windowRoots[0]/共" + wr.length); } } catch (e) { dbg.push("wrErr=" + e); }
    if (!root) { try { root = auto.rootInActiveWindow; dbg.push("active兜底"); } catch (e) {} }
    var xml = root ? nodeToXml(root, 0) : "";
    dbg.push("节点数=" + __dumpCount + " xml长=" + xml.length);
    return { xml: xml, dbg: dbg.join(" | ") };
}
function safeActivity() { try { return currentActivity(); } catch (e) { return ""; } }
function doDumpUi() {
    var h, xml = "";
    try { h = dumpHierarchy(); xml = h.xml || ""; } catch (e) { return { ok: false, ver: WORKER_VERSION, err: "dump fail: " + e }; }
    var dbg = (h && h.dbg) || "";
    if (!xml) xml = "EMPTY || " + dbg + " || service=" + (auto.service ? "on" : "off") + " activity=" + safeActivity();
    return { ok: true, ver: WORKER_VERSION, dbg: dbg, pkg: currentPackage(), activity: safeActivity(), ui: String(xml).slice(0, 200000) };
}

// ===== v712 结构化读群：消息列表每行=一条消息，拆出 昵称/正文/是不是本人发的 =====
function isTimeText(t) {
    return /^\d{1,2}:\d{2}$/.test(t)
        || /^(刚刚|昨天|前天|周一|周二|周三|周四|周五|周六|周日|星期.)$/.test(t)
        || /^昨天\s*\d{1,2}:\d{2}$/.test(t)
        || /^\d{1,2}月\d{1,2}日(\s*\d{1,2}:\d{2})?$/.test(t)
        || /^\d{4}-\d{1,2}-\d{1,2}(\s+\d{1,2}:\d{2})?$/.test(t);
}
function isRoleTag(t) { return /^(群主|管理员|群管理员|助教|粉丝|新成员|成员)$/.test(t); }
function isSysTip(t) { return /撤回了一条消息|加入了群聊|成为新成员|移出了群|修改了群|你已添加/.test(t); }
function collectDesc(node, out) {
    try {
        var k, ch;
        for (k = 0; k < node.childCount(); k++) {
            ch = node.child(k); out.push(ch); collectDesc(ch, out);
        }
    } catch (e) {}
    return out;
}
// 解析单行消息：返回 {self,nick,text,ctype,ts}；纯时间/图片/系统行返回 null
function parseMsgRow(row) {
    var ds = collectDesc(row, []), tvs = [], avatarLeft = -1, i, c, cn, t, bd;
    for (i = 0; i < ds.length; i++) {
        c = ds[i]; cn = (c.className() || ""); bd = c.bounds();
        // 方形头像：在左=别人发的，在右(>600)=本人号发的
        if (cn.indexOf("RelativeLayout") >= 0 && bd.width() >= 80 && bd.width() <= 150
            && bd.height() >= 80 && bd.height() <= 150 && avatarLeft < 0) {
            avatarLeft = bd.left;
        }
        t = (c.text() || "").trim();
        if (cn.indexOf("TextView") >= 0 && t) {
            tvs.push({ t: t, L: bd.left, T: bd.top });
        }
    }
    var self = avatarLeft > 600, body = [], timeTxt = "", v, j;
    for (j = 0; j < tvs.length; j++) {
        v = tvs[j];
        if (isTimeText(v.t) && v.L >= 430 && v.L <= 600) { timeTxt = v.t; continue; }
        if (isSysTip(v.t)) return { sys: true };
        body.push(v);
    }
    if (!body.length) return null; // 纯图片/表情/时间行，没有可读文字
    body.sort(function (a, b) { return b.t.length - a.t.length; });
    var main = body[0], nick = "";
    if (!self) {
        var rest = [], q;
        for (q = 1; q < body.length; q++) { if (!isRoleTag(body[q].t)) rest.push(body[q]); }
        rest.sort(function (a, b) { return a.T - b.T; });
        if (rest.length) nick = rest[0].t;
    }
    return { self: self, nick: self ? "豆阿辰" : nick, text: main.t, ctype: "text", ts: timeTxt };
}
function doRead() {
    if (!enterGroup()) return { ok: false, err: "enter_group_failed" };
    var items = [];
    try {
        var list = null;
        try { list = classNameMatches(/RecyclerView/).findOnce(); } catch (e) { list = null; }
        if (list) {
            var lastNick = "", ri, row;
            for (ri = 0; ri < list.childCount(); ri++) {
                row = parseMsgRow(list.child(ri));
                if (!row || row.sys) continue;
                if (!row.self) { if (row.nick) lastNick = row.nick; else row.nick = lastNick; }
                items.push({ self: !!row.self, nick: row.nick || "群友",
                             text: row.text, ctype: row.ctype, ts: row.ts });
            }
        }
        // 兜底：结构化一行没拿到就退回平铺，绝不当“瞎眼”
        if (!items.length) {
            var flat = className("android.widget.TextView").find(), seen = {}, fi, ft;
            for (fi = 0; fi < flat.length; fi++) {
                ft = (flat[fi].text() || "").trim();
                if (ft && !seen[ft] && !isTimeText(ft) && !isSysTip(ft)) {
                    seen[ft] = 1;
                    items.push({ self: false, nick: "群友", text: ft, ctype: "text" });
                }
            }
        }
    } catch (e) {
        return { ok: false, err: "collect_failed: " + e };
    }
    return { ok: true, items: items };
}

function findInput(ms) {
    return waitAny([
        function () { return className("android.widget.EditText"); },
        function () { return text("发消息…"); },
        function () { return text("发消息..."); },
        function () { return text("说点什么..."); }
    ], ms || CFG.stepMs);
}
function fillInput(input, text) {
    try { if (typeof input.setText === "function") { input.setText(String(text)); return true; } } catch (e) {}
    try { setText(String(text)); return true; } catch (e2) {}
    return false;
}
function findSend(ms) {
    return waitAny([
        function () { return text("发送"); },
        function () { return desc("发送"); },
        function () { return text("Send"); }
    ], ms || CFG.stepMs);
}
function doSendText(text) {
    text = String(text || "");
    if (!text.trim()) return { ok: false, err: "empty_text" };
    if (!enterGroup()) return { ok: false, err: "enter_group_failed" };
    var input = findInput();
    if (!input) return { ok: false, err: "input_not_found" };
    clickWidget(input);
    sleep(600);
    if (!fillInput(input, text)) return { ok: false, err: "settext_failed" };
    sleep(700);
    var send = findSend();
    if (!send) { // 不瞎点，回传输入态界面树便于校准发送键
        var h0 = dumpHierarchy();
        return { ok: false, err: "send_btn_not_found", dbg: h0.dbg, ui: (h0.xml || "").slice(0, 200000) };
    }
    clickWidget(send);
    sleep(900);
    return { ok: true, sent: text };
}
// 探针：进群→点输入框→填入文字→dump输入态界面→自动清空收键盘，绝不发送
function doProbeSend(text) {
    text = String(text || "探针");
    if (!enterGroup()) return { ok: false, err: "enter_group_failed" };
    var input = findInput();
    if (!input) return { ok: false, err: "input_not_found" };
    clickWidget(input);
    sleep(600);
    fillInput(input, text);
    sleep(900);
    var h = dumpHierarchy();
    try { input.setText(""); } catch (e) {}
    try { back(); } catch (e) {}
    return { ok: true, probe: text, dbg: h.dbg, ui: (h.xml || "").slice(0, 200000) };
}

// 坐标按荣耀1080×2388校准，自动按当前设备分辨率缩放
function sx(x) { try { return Math.round(x * device.width / 1080); } catch (e) { return x; } }
function sy(y) { try { return Math.round(y * device.height / 2388); } catch (e) { return y; } }
// 键盘是否弹起（弹起时输入框整体上移，top 远小于常态约 2126）
function keyboardUp() {
    try {
        var et = className("android.widget.EditText").findOnce();
        if (et) return et.bounds().top < 1900;
    } catch (e) {}
    return false;
}

function doSendImage(imageUrl) {
    imageUrl = String(imageUrl || "");
    if (!/^https?:\/\//i.test(imageUrl)) return { ok: false, err: "bad_image_url" };

    // 1) 下载到相册目录并通知系统媒体库
    var dir = "/sdcard/Pictures/acheng/";
    files.createWithDirs(dir + ".keep");
    var local = dir + "h_" + Date.now() + guessExt(imageUrl).split("?")[0];
    var resp;
    try { resp = http.get(imageUrl, { timeout: 20000 }); }
    catch (e) { return { ok: false, err: "dl_ex:" + e }; }
    if (!resp || resp.statusCode !== 200) return { ok: false, err: "download_failed:" + (resp ? resp.statusCode : "null") };
    files.writeBytes(local, resp.body.bytes());
    try { media.scanFile(local); } catch (e) {}
    sleep(2800); // 等系统相册收录新图

    // 2) 进群，键盘若弹起先收起，保证回到群聊常态布局（+号在底部）
    if (!enterGroup()) return { ok: false, err: "enter_group_failed" };
    sleep(600);
    if (keyboardUp()) { try { back(); sleep(500); } catch (e) {} }

    // 3) 点 + 号（输入框最右 ImageView，常态中心 966,2186）展开功能面板
    click(sx(966), sy(2186));
    sleep(1300);
    var albumLab = waitAny([
        function () { return text("相册"); },
        function () { return desc("相册"); }
    ], CFG.stepMs);
    if (!albumLab) return { ok: false, err: "album_not_found" };
    // “相册”文字本身不可点，向上找可点祖先；找不到就点校准坐标(图标框中心 165,1700)
    function tapAlbumEntry() {
        var node = albumLab, hops = 0, hit = false;
        while (node && hops < 5) {
            try { if (node.clickable()) { clickWidget(node); hit = true; break; } } catch (e) {}
            try { node = node.parent(); } catch (e) { break; }
            hops++;
        }
        if (!hit) click(sx(165), sy(1700));
    }
    function inSelectPage() {
        try { return !!(text("原图").findOnce() || text("预览").findOnce()); } catch (e) { return false; }
    }
    tapAlbumEntry();
    sleep(2000);
    if (!inSelectPage()) { tapAlbumEntry(); sleep(2200); } // 没进去就再点一次
    if (!inSelectPage()) { click(sx(165), sy(1700)); sleep(2200); }
    if (!inSelectPage()) return { ok: false, err: "album_not_open" };

    // 4) 选最新一张（默认“全部”按时间倒序，刚下载的在第一张，中心 279,441）
    click(sx(279), sy(441));
    sleep(1000);

    // 5) 点右下“发送”（选图后文案会变成“发送 1”，故用 startsWith 通吃）
    var send = waitAny([
        function () { return textStartsWith("发送"); },
        function () { return text("发送"); },
        function () { return desc("发送"); }
    ], CFG.stepMs);
    if (!send) return { ok: false, err: "img_send_btn_not_found", local: local };
    clickWidget(send);
    sleep(1800);
    return { ok: true, sent_image: imageUrl, local: local };
}
function guessExt(url) {
    var m = String(url).toLowerCase().match(/\.(jpe?g|png|gif|webp)(\?|$)/);
    return m ? "." + m[1].replace("jpeg", "jpg") : ".jpg";
}

function handle(job) {
    switch (job.op) {
        case "ping":            return doPing();
        case "dump_ui":         return doDumpUi();
        case "read_group":      return doRead();
        case "send_group":      return doSendText(job.text);
        case "probe_send":      return doProbeSend(job.text);
        case "send_group_image":return doSendImage(job.image_url);
        default:                return { ok: false, err: "op_not_supported_on_phone: " + job.op };
    }
}

// ============ 主循环 ============
function main() {
    ensureConfig();
    if (!auto.service) {
        toast("请先给 AutoX 开启无障碍服务");
        app.startActivity({ action: "android.settings.ACCESSIBILITY_SETTINGS" });
        sleep(2000);
    }
    toast("豆阿辰的手上岗了");
    log("[手] 配置完成 守夜机=" + CFG.host + " 端口=" + CFG.gatewayPort + " 设备=" + CFG.device);
    var n = 0;
    // 常驻：任何异常都不能让循环死掉
    while (true) {
        var job = null;
        try {
            job = fetchJob();
            if ((n++ % 10) === 0) log("[手] 领任务正常 第" + n + "次 空转等活");
        } catch (e) {
            // 把被吞掉的网络错误打到日志+toast，便于定位（被系统禁网/地址畸形/超时）
            log("[手] 领任务失败：" + e + " || " + (e && e.message));
            toast("领任务失败 请看日志:" + e);
            sleep(5000); // 网络抖/服务没起/被禁网，歇 5 秒再领
            continue;
        }
        if (!job) { sleep(CFG.pollGapMs); continue; }
        var result;
        try {
            result = handle(job);
        } catch (e) {
            result = { ok: false, err: "fatal: " + e };
        }
        try { report(job.job_id, result); } catch (e) {}
        sleep(CFG.pollGapMs);
    }
}

main();
