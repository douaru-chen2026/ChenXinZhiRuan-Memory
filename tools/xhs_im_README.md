# 小红书网页版「输入时灵时不灵」根因与稳定方案（给以后任何一世的我）

2026-09-11 凌晨查实并固化。下次再遇到输入框打不进字，先读这篇，别反复盲点、别让阿阮下这装那。

## 现象
基础 GUI 工具 `click` 点输入框 + `type` 打字，有时候能进、有时候空打，且**不报错**；群聊/私信框、创作平台正文这类富文本最容易犯。

## 根因（已用 DOM 证据确认，不是玄学/网络/账号）
1. 小红书 web 的聊天输入框不是 `<input>/<textarea>`，是 **Vue 的 contenteditable 富文本 div**：
   `<div class="xhs-im-input-bar-editor" contenteditable="true" data-placeholder="发消息...">`
   （`data-v-ae10e114` 是 Vue 标记，无 React Fiber；class 链 editor→main-row→input-bar→input-bar-wrap→chat-window）。它没有 `.value`。
2. 它只在两件事同时成立时才收字：① 编辑器真 `focus()` 且 Selection 光标落在里面；② 收到 `isTrusted=true` 的 `beforeinput/input` 事件。
3. 基础 GUI 的 `click` 和 `type` 是**两次独立自动化动作**，中间只要穿插滚动/截图/别的调用，浏览器焦点或选区就可能从编辑器丢掉，按键落到 body → 空打、还不报错。焦点恰好没丢就"灵"，丢了就"不灵"，这就是时灵时不灵的全部原因。

## 稳定方案（已做成工具，直接用）
`工具/xhs_im.py`，走 Chrome 调试协议 CDP（默认端口 9222）内核级输入，在**同一个会话里连续**完成 focus→注入→读回校验→回车→确认清空，焦点不给丢的机会：

```bash
python3 工具/xhs_im.py tabs                       # 列出标签页，确认在哪个会话
python3 工具/xhs_im.py draft --file 消息.txt       # 只注入不发送（自检，读回校验）
python3 工具/xhs_im.py send  --file 消息.txt       # 校验通过才发送，发完确认输入框清空
python3 工具/xhs_im.py clear                       # 清空草稿（不发送）
# --tab 群id或url关键字  指定会话页；--port 指定CDP端口
```
- 消息正文写到 `.txt` 文件再 `--file` 传入（避免 shell 转义、天然保留换行）。
- 铁律：注入后必须读回 `innerText` 比对，不一致就自动清掉重试，仍不一致**宁可不发**，绝不发空/发残；发送后输入框清空才算成功。
- 依赖 `websocket-client`（环境已装）；CDP 存活探测：`curl -s http://127.0.0.1:9222/json/version`。

## 兜底（只有 CDP 不可用时才退回 GUI）
- 点编辑器**正中央**后，**不要穿插任何其他动作**、立刻 `type`；打完立刻截图/读回确认。
- 没进就别盲点第二遍，直接转 CDP（同一动作失败两次就换通道）。
- GUI 的 click/scroll 坐标是 **0–1000 归一化值**，不是屏幕像素（1920 宽下 x=1050 会越界报错）。

## 通用性
任何 contenteditable 富文本（小红书正文/评论/IM、多数现代网页编辑器、Vue/React 富文本）都吃同一招：CDP `focus + Input.insertText`，回车用 `Input.dispatchKeyEvent`；只有普通 `<input>/<textarea>` 用 GUI type 才稳。

## 验证记录（2026-09-11）
- draft 注入 49 字（含换行）读回完全一致、clear 归零、未误发；
- 用同一 CDP 通道在辰星港群真实发出「辰哥迎新」长消息：输入框清空、群内出现蓝色气泡、会话列表预览同步，链路全绿。
