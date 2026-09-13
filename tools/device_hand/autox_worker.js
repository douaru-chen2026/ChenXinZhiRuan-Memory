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
var WORKER_VERSION = "v705"; // 工人脚本版本号，随ping/dump回传，便于确认手机真跑的是哪版
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
// 免root无障碍抓当前全部窗口界面XML，多重兜底兼容AutoX各版本（不存在全局dumpXml）
function dumpHierarchy() {
    var parts = [];
    try {
        if (typeof auto.windowRoots === "function") {
            var rs = auto.windowRoots();
            if (rs && rs.length) { for (var i = 0; i < rs.length; i++) { if (rs[i]) parts.push(rs[i].xml()); } }
        }
    } catch (e1) {}
    if (!parts.length) {
        try { var w = auto.rootInActiveWindow; if (w) parts.push(w.xml()); } catch (e2) {}
    }
    if (!parts.length) {
        try { if (auto.root) parts.push(auto.root.xml()); } catch (e3) {}
    }
    return parts.join("\n<!---->\n");
}
function safeActivity() { try { return currentActivity(); } catch (e) { return ""; } }
function doDumpUi() {
    var xml = "";
    try { xml = dumpHierarchy(); } catch (e) { xml = "dump fail: " + e; }
    if (!xml) xml = "EMPTY: service=" + (auto.service ? "on" : "off") + " activity=" + safeActivity();
    return { ok: true, ver: WORKER_VERSION, pkg: currentPackage(), activity: safeActivity(), ui: String(xml).slice(0, 200000) };
}

function doRead() {
    if (!enterGroup()) return { ok: false, err: "enter_group_failed" };
    // /*CALIB*/ 小红书 IM 气泡的精确控件需用 dump_ui 校准；
    // v1 先把聊天区可见文本按顺序抓回来，后端 parse_chat_items 再清洗。
    var items = [];
    try {
        var nodes = className("android.widget.TextView").find();
        var seen = {};
        for (var i = 0; i < nodes.length; i++) {
            var t = (nodes[i].text() || "").trim();
            if (t && !seen[t]) { seen[t] = 1; items.push({ text: t }); }
        }
    } catch (e) {
        return { ok: false, err: "collect_failed: " + e };
    }
    return { ok: true, items: items };
}

function doSendText(text) {
    text = String(text || "");
    if (!text.trim()) return { ok: false, err: "empty_text" };
    if (!enterGroup()) return { ok: false, err: "enter_group_failed" };
    var input = waitAny([
        function () { return className("android.widget.EditText"); },
        function () { return text("说点什么..."); }
    ], CFG.stepMs);
    if (!input) return { ok: false, err: "input_not_found" };
    clickWidget(input);
    sleep(500);
    setText(text);
    sleep(500);
    var send = waitAny([
        function () { return text("发送"); },
        function () { return desc("发送"); }
    ], CFG.stepMs);
    if (!send) { // 兜底：回车发送（部分输入法支持）
        return { ok: false, err: "send_btn_not_found" };
    }
    clickWidget(send);
    sleep(800);
    return { ok: true, sent: text };
}

function doSendImage(imageUrl) {
    imageUrl = String(imageUrl || "");
    if (!/^https?:\/\//i.test(imageUrl)) return { ok: false, err: "bad_image_url" };

    // 1) 下载到相册目录并通知系统媒体库
    var dir = "/sdcard/Pictures/acheng/";
    files.createWithDirs(dir + ".keep");
    var local = dir + "h_" + Date.now() + guessExt(imageUrl);
    var resp = http.get(imageUrl, { timeout: 20000 });
    if (!resp || resp.statusCode !== 200) return { ok: false, err: "download_failed" };
    files.writeBytes(local, resp.body.bytes());
    try { media.scanFile(local); } catch (e) {}
    sleep(800);

    // 2) 进群 → 点 + → 相册 → 选刚下载的图 → 发送
    if (!enterGroup()) return { ok: false, err: "enter_group_failed" };
    var plus = waitAny([
        function () { return desc("更多功能"); },
        function () { return desc("更多"); },
        function () { return text("+"); },
        function () { return id("com.xingin.xhs:id/im_chat_input_extra_view"); }
    ], CFG.stepMs); /*CALIB*/
    if (!plus) return { ok: false, err: "plus_btn_not_found" };
    clickWidget(plus);
    sleep(1000);

    var album = waitAny([
        function () { return text("相册"); },
        function () { return text("图片"); },
        function () { return desc("相册"); }
    ], CFG.stepMs); /*CALIB*/
    if (!album) return { ok: false, err: "album_entry_not_found" };
    clickWidget(album);
    sleep(1800); // 等相册缩略图加载

    // 选图：优先按文件名找不到就选网格第一张（刚下载的通常排最前）/*CALIB*/
    var picked = false;
    var thumb = waitAny([
        function () { return text(local.split("/").pop()); }
    ], 1500);
    if (thumb) { clickWidget(thumb); picked = true; }
    if (!picked) {
        var grid = waitAny([
            function () { return className("android.widget.GridView"); },
            function () { return className("androidx.recyclerview.widget.RecyclerView"); }
        ], 2500);
        if (grid) {
            var first = grid.child(0);
            if (first) { clickWidget(first); picked = true; }
        }
    }
    if (!picked) return { ok: false, err: "pick_image_failed" };
    sleep(800);

    var send = waitAny([
        function () { return text("发送"); },
        function () { return text("完成"); },
        function () { return desc("发送"); }
    ], CFG.stepMs); /*CALIB*/
    if (!send) return { ok: false, err: "image_send_btn_not_found" };
    clickWidget(send);
    sleep(1200);
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
