# Reasoning Content Proxy

注意：本项目最后一次维护是5月，使用前请务必参考最新的官方api接口文档！！

> 用于补全deepseek、kimi等模型的工具调用思维回传的本地代理，适用于claudecode，防止工具调用报错。
>
> A local proxy that completes the tool-call reasoning feedback for models like DeepSeek and Kimi, compatible with Claude Code, to prevent tool-call errors.

以opencode go套餐为例，如需使用其他提供商、请自行更改proxy.py内的url。 适用于任何没有做思维链回传的ai应用程序，只用在应用内把提供商url改为[http://127.0.0.1:8787](http://127.0.0.1:8787/)

*下面的部分由kimi-k2.6编写*

---

## 目录

1. [我能解决什么问题？](#我能解决什么问题)
2. [数据流长什么样？](#数据流长什么样)
3. [安装依赖](#安装依赖)
4. [启动代理](#启动代理)
5. [配置 ccswitch](#配置-ccswitch)
6. [怎么看日志](#怎么看日志)
7. [环境变量](#环境变量)
8. [常见问题排查](#常见问题排查)
9. [Kimi 使用特别提示](#kimi-使用特别提示)
10. [隐私说明](#隐私说明)

---

## 我能解决什么问题？

如果你用 **Claude Code** 搭配 **DeepSeek** 或 **Kimi（Moonshot）** 的 API，可能会遇到这种报错：

```
API Error: 400
{
  "error": {
    "message": "thinking is enabled but reasoning_content is missing..."
  }
}
```

**原因**：Claude Code 发下一轮请求时，把模型上一轮的思考内容（`reasoning_content`）弄丢了。DeepSeek 和 Kimi 要求**每一轮都必须完整带回之前的思考内容**。

**本代理的作用**：在 Claude Code 和 API 之间加一层本地代理，自动记住并补回缺失的 `reasoning_content`。

---

## 数据流长什么样？

很多人搞不清 ccswitch、代理、官网三者之间的关系。一句话：**ccswitch 只认识代理，代理再去认识官网**。

```
Claude Code
    ↓
ccswitch（只做协议转换）
    Base URL = http://127.0.0.1:8787
    ↓
proxy.py（本地运行，做两件事）
    ① 补 reasoning_content
    ② 转发到真正的官网
    ↓
https://api.deepseek.com 或 https://api.moonshot.cn
```

**ccswitch 的 Base URL 只填代理地址**，不要填官网地址。代理自己会通过 `TARGET_API` 环境变量知道该往哪里转发。

---

## 安装依赖

确保你已经安装了 Python 3.10+，然后执行：

```bash
pip install flask requests
```

如果 `pip` 命令找不到，尝试：

```bash
python -m pip install flask requests
```

---

## 启动代理

进入 `proxy.py` 所在目录，直接启动：

```bash
python proxy.py
```

启动成功后，你会看到：

```
============================================================
 Reasoning Content Proxy
 解决 Claude Code + DeepSeek/Moonshot reasoning_content 回传报错
============================================================
 目标 API : https://api.deepseek.com
 本地端口 : 8787

 启动前请确保已安装依赖:
   pip install flask requests

 使用方法:
   1. python proxy.py
   2. 将 Claude Code / ccswitch 的 Base URL 设为 http://127.0.0.1:8787
   3. 正常使用 Claude Code 即可
============================================================
```

**窗口不要关**，代理需要一直运行。

### 如果要连 Kimi（Moonshot）

启动前设置环境变量：

**Windows：**
```cmd
set TARGET_API=https://api.moonshot.cn
python proxy.py
```

**Mac / Linux：**
```bash
export TARGET_API=https://api.moonshot.cn
python proxy.py
```

启动后你会看到 `目标 API : https://api.moonshot.cn`。

---

## 配置 ccswitch

找到 ccswitch 的配置文件（通常是 `.env` 或 `config.json`，也可能是配置界面里的 "Base URL" / "API Base" 栏），把 Base URL 改成：

```
http://127.0.0.1:8787
```

**只需要改这一处**。ccswitch 以前填官网地址，现在改填代理地址，**代理会代替 ccswitch 去请求官网**。

保存后**重启 ccswitch**，并确保代理窗口一直在运行。

---

## 怎么看日志

当你正常使用 Claude Code 对话时，观察运行 `proxy.py` 的终端窗口：

### 正常工作的样子

```
[REQ] POST /chat/completions -> https://api.deepseek.com/chat/completions
[REQ] messages=3, assistant=1, need_patch=1, stream=True
[PATCH] full_key=a3f7b2d1... 精确恢复 reasoning_content
[PATCH] 请求已修补 1 条，当前缓存数 full=2 tc=1
[PROXY] 转发请求 -> https://api.deepseek.com/chat/completions
[RESP] status=200, content-type=text/event-stream; charset=utf-8
[RESP] 响应模式: SSE 流式
[CACHE] full_key=8e2c4b5a... tc_key=N/A rc_len=142 content_len=56 preview='好的，我来帮你分析这个问题...'
[CACHE] SSE 流结束，共缓存 1 条 assistant 消息
```

- `[REQ]`：代理收到了 ccswitch 发过来的请求，能看到消息总数和需要修补的数量。
- `[PATCH]`：发现 Claude Code 漏了思考内容，已经帮你补上了。
- `[PROXY]`：代理正在把请求转发给真正的官网。
- `[RESP]`：官网返回响应，代理收到数据。
- `[CACHE]`：代理记住了模型返回的思考内容，供下一轮使用。

### 不正常的标志

如果代理窗口**没有任何输出**，说明请求根本没走到代理。请检查：

1. ccswitch 的 Base URL 是不是真的改成了 `http://127.0.0.1:8787`？
2. ccswitch 有没有重启？
3. Claude Code 有没有开新对话？（旧对话可能还指向老地址）

---

## 环境变量

启动前可以通过环境变量修改代理行为：

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `TARGET_API` | 真实 API 的服务器地址 | `https://api.deepseek.com` |
| `PROXY_PORT` | 本地代理监听的端口 | `8787` |

**Windows：**
```cmd
set TARGET_API=https://api.moonshot.cn
set PROXY_PORT=8788
python proxy.py
```

**Mac / Linux：**
```bash
export TARGET_API=https://api.moonshot.cn
export PROXY_PORT=8788
python proxy.py
```

如果改了端口，ccswitch 里的 Base URL 也要同步改成 `http://127.0.0.1:8788`。

---

## 常见问题排查

### Q1: 启动时提示 `ModuleNotFoundError: No module named 'flask'`

没有安装依赖，执行：
```bash
pip install flask requests
```

### Q2: 提示 `Address already in use`

端口 8787 被占用了，换个端口：
```cmd
set PROXY_PORT=8788
python proxy.py
```

### Q3: 代理窗口有 `[CACHE]`，但没有 `[PATCH]`，仍然报错 400

可能是多轮对话后缓存和实际消息对不上（比如 Claude Code 修改了历史消息的某些字段，导致签名不一致）。解决：开一个新对话，或者重启代理清空缓存后再开新对话。

### Q4: 能聊天，但模型看起来"忘了"之前说过的话

这是已知限制。代理虽然补上了 `reasoning_content`，但如果 `content` 或其他字段也被客户端修改了，可能导致缓存匹配失败。解决：**重启代理 + 开启新对话**。

### Q5: 代理窗口显示很多 `[PATCH] fallback 恢复`

精确匹配失败了，代理在用兜底策略（最近一条缓存补上）。如果只是偶尔出现，通常不影响；如果大量出现，请检查 ccswitch 是否在转发时修改了 `content` 或 `tool_calls` 的结构。

---

## Kimi 使用特别提示

根据 Kimi 官方文档，使用 `kimi-k2-thinking`、`kimi-k2.6` 或 `kimi-k2.5`（启用思考能力时），除了使用本代理外，请求本身还需要满足以下几点：

### 1. 务必开启流式输出

```json
{ "stream": true }
```

Kimi 官方强烈建议开启流式输出。本代理已完美支持 SSE 流式传输，无需额外配置。

### 2. 设置足够大的 `max_tokens`

```json
{ "max_tokens": 16000 }
```

Kimi 官方建议至少 `16000`，否则可能无法输出完整的 `reasoning_content` 和 `content`。

### 3. 固定 `temperature=1.0`

对于 `kimi-k2.6` 和 `kimi-k2.5` 模型，官方固定使用 `temperature=1.0`，不要修改。

### 4. 流式输出中识别思考结束

在流式（`stream=true`）场景下，Kimi 的 `reasoning_content` 字段**一定先于** `content` 字段出现。本代理会自动处理这个逻辑，普通用户无需关心。

### 5. 完整保留上下文

每一轮请求里，所有历史 `assistant` 消息都必须包含完整的 `reasoning_content`。

```python
# 官方推荐做法：直接把返回的 message 原封不动 append 到 messages
messages.append(response.choices[0].message)
```

**千万不要只保留 `content`，而把 `reasoning_content` 丢掉！**

---

## 隐私说明

- **纯本地运行**：代理只在你自己的电脑上运行，不会把数据上传到第三方服务器。
- **不存储日志**：`reasoning_content` 只保存在程序内存中，代理关闭后自动清空，不会写入硬盘。
- **API Key 不经过代理处理**：你的 API Key 由 Claude Code / ccswitch 直接放在请求头里发给真实 API，代理只是透传，不会读取或记录。

---

## 还搞不定？

请收集以下信息排查：

1. 运行 `proxy.py` 的终端窗口里，**是否有任何输出**？
2. 报错是 `400 reasoning_content missing` 还是其他（如 401、403、429）？
3. 你使用的是 DeepSeek 还是 Kimi？
4. ccswitch 的 Base URL 指向哪里？

