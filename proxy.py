#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reasoning Content Proxy
=======================
在 Claude Code 与 DeepSeek / Moonshot / 其他要求回传 reasoning_content 的 API 之间
加一层本地代理，自动缓存并回填缺失的 reasoning_content。

官方参考逻辑:
    messages.append(response.choices[0].message)
    # 必须包含 content + reasoning_content + tool_calls
    # 且多轮对话后仍需保留历史 reasoning_content

用法:
    pip install flask requests
    python proxy.py

然后让 Claude Code（或 ccswitch）的 Base URL 指向:
    http://127.0.0.1:8787

环境变量:
    TARGET_API  - 真实 API 地址 (默认: https://opencode.ai/zen/go)
    PROXY_PORT  - 本地监听端口 (默认: 8787)
"""

import os
import json
import hashlib
import requests
from flask import Flask, request, Response

app = Flask(__name__)

# ==================== 配置 ====================
TARGET_BASE = os.getenv("TARGET_API", "https://opencode.ai/zen/go").rstrip('/')
PROXY_PORT = int(os.getenv("PROXY_PORT", "8787"))

# 缓存结构:
#   _reasoning_cache[full_key] = reasoning_content
#   _toolcall_cache[toolcall_key] = reasoning_content   (专门给 content 为空但带 tool_calls 的消息)
_reasoning_cache = {}
_toolcall_cache = {}

# ==================== 工具函数 ====================

def _make_full_key(msg: dict) -> str:
    """完整 key: content + tool_calls"""
    content = msg.get("content") or ""
    tool_calls = msg.get("tool_calls")
    tc_str = json.dumps(tool_calls, sort_keys=True, ensure_ascii=False) if tool_calls else ""
    return hashlib.md5(f"content={content}\ntool_calls={tc_str}".encode('utf-8')).hexdigest()


def _make_toolcall_key(msg: dict) -> str:
    """ToolCall 专用 key: 只看 tool_calls，忽略 content。
    因为 thinking 模式下，模型经常 content='' 或固定前缀，只有 tool_calls 能唯一标识。"""
    tool_calls = msg.get("tool_calls")
    if not tool_calls:
        return ""
    tc_str = json.dumps(tool_calls, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(f"tool_calls={tc_str}".encode('utf-8')).hexdigest()


def _restore_reasoning(msg: dict) -> bool:
    """尝试为单条 assistant 消息恢复 reasoning_content。成功返回 True。"""
    if msg.get("reasoning_content"):
        return False  # 本来就有，不需要恢复

    full_key = _make_full_key(msg)
    tc_key = _make_toolcall_key(msg)

    # 1. 精确匹配 (content + tool_calls)
    if full_key in _reasoning_cache:
        msg["reasoning_content"] = _reasoning_cache[full_key]
        print(f"[PATCH] full_key={full_key[:8]}... 精确恢复 reasoning_content")
        return True

    # 2. ToolCall 专用匹配 (只看 tool_calls，适用于 content='' 的情况)
    if tc_key and tc_key in _toolcall_cache:
        msg["reasoning_content"] = _toolcall_cache[tc_key]
        print(f"[PATCH] tc_key={tc_key[:8]}... toolcall 恢复 reasoning_content")
        return True

    # 3. 兜底：消息带 tool_calls 但缓存未命中，用最近一条缓存
    if msg.get("tool_calls") and _reasoning_cache:
        fallback = list(_reasoning_cache.values())[-1]
        msg["reasoning_content"] = fallback
        print(f"[PATCH] fallback 恢复 (最近缓存) reasoning_content")
        return True

    print(f"[PATCH] 未命中缓存，无法恢复 reasoning_content (full_key={full_key[:8]}... tc_key={(tc_key[:8] + '...') if tc_key else 'N/A'})")
    return False


def _patch_messages(data: dict) -> dict:
    """遍历请求中的 messages，为缺失 reasoning_content 的 assistant 补回"""
    messages = data.get("messages")
    if not messages:
        return data

    patched = False
    for msg in messages:
        if msg.get("role") != "assistant":
            continue

        rc = msg.get("reasoning_content")
        full_key = _make_full_key(msg)
        tc_key = _make_toolcall_key(msg)

        if rc:
            # 请求里自带了，刷新缓存
            _reasoning_cache[full_key] = rc
            if tc_key:
                _toolcall_cache[tc_key] = rc
        else:
            if _restore_reasoning(msg):
                patched = True

    if patched:
        print(f"[PATCH] 请求已修补 {patched} 条，当前缓存数 full={len(_reasoning_cache)} tc={len(_toolcall_cache)}")
    else:
        print(f"[PATCH] 无需修补，当前缓存数 full={len(_reasoning_cache)} tc={len(_toolcall_cache)}")
    return data


def _cache_msg(msg: dict):
    """把 assistant 消息缓存到内存"""
    rc = msg.get("reasoning_content")
    if not rc:
        return
    full_key = _make_full_key(msg)
    tc_key = _make_toolcall_key(msg)
    _reasoning_cache[full_key] = rc
    if tc_key:
        _toolcall_cache[tc_key] = rc
    content_preview = (msg.get("content") or "")[:40].replace("\n", " ")
    print(f"[CACHE] full_key={full_key[:8]}... tc_key={(tc_key[:8] + '...') if tc_key else 'N/A'} rc_len={len(rc)} content_len={len(msg.get('content') or '')} preview='{content_preview}...'")


def _cache_nonstream(resp_json: dict):
    """非流式响应：直接提取 message 并缓存"""
    cached_count = 0
    for choice in resp_json.get("choices", []):
        msg = choice.get("message")
        if msg and msg.get("role") == "assistant":
            _cache_msg(msg)
            cached_count += 1
    if cached_count:
        print(f"[CACHE] 非流式响应，共缓存 {cached_count} 条 assistant 消息")


def _merge_tool_calls(acc_list: list, delta_list: list):
    """SSE 流中增量合并 tool_calls（OpenAI 格式）"""
    if not delta_list:
        return
    for tc in delta_list:
        idx = tc.get("index", 0)
        while len(acc_list) <= idx:
            acc_list.append({})
        entry = acc_list[idx]
        if "id" in tc:
            entry["id"] = tc["id"]
        if "type" in tc:
            entry["type"] = tc["type"]
        if "function" in tc:
            entry.setdefault("function", {})
            fn = tc["function"]
            if "name" in fn:
                entry["function"]["name"] = fn["name"]
            if "arguments" in fn:
                entry["function"]["arguments"] = entry["function"].get("arguments", "") + fn["arguments"]


def _generate_and_cache_stream(resp):
    """
    透传 SSE 流，同时在后台累积每个 choice 的 content / reasoning_content / tool_calls。
    收到 [DONE] 后把完整 reasoning_content 写入缓存。
    """
    # choice_index -> 累积状态
    acc = {}

    for raw_line in resp.iter_lines():
        if raw_line is None:
            continue

        # 先透传，保证客户端不阻塞
        yield raw_line + b'\n'

        line = raw_line.decode('utf-8', errors='replace')
        if not line.startswith('data: '):
            continue

        payload = line[6:]
        if payload == '[DONE]':
            # 流结束，刷缓存
            cached_count = 0
            for idx, state in acc.items():
                rc = state.get("reasoning_content", "")
                if not rc:
                    continue
                msg = {
                    "role": "assistant",
                    "content": state.get("content", ""),
                    "reasoning_content": rc,
                }
                if state.get("tool_calls"):
                    msg["tool_calls"] = state["tool_calls"]
                _cache_msg(msg)
                cached_count += 1
            if cached_count:
                print(f"[CACHE] SSE 流结束，共缓存 {cached_count} 条 assistant 消息")
            else:
                print(f"[CACHE] SSE 流结束，无 reasoning_content 可缓存")
            continue

        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue

        for choice in chunk.get("choices", []):
            idx = choice.get("index", 0)
            if idx not in acc:
                acc[idx] = {"content": "", "reasoning_content": "", "tool_calls": []}

            delta = choice.get("delta", {})
            st = acc[idx]

            if "content" in delta and delta["content"]:
                st["content"] += delta["content"]
            if "reasoning_content" in delta and delta["reasoning_content"]:
                st["reasoning_content"] += delta["reasoning_content"]
            if "tool_calls" in delta:
                _merge_tool_calls(st["tool_calls"], delta["tool_calls"])


# ==================== 路由 ====================

@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'])
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'])
def proxy(path):
    target_url = f"{TARGET_BASE}/{path}"
    print(f"\n[REQ] {request.method} /{path} -> {target_url}")

    # 转发 header（去掉影响代理的）
    headers = {}
    for k, v in request.headers:
        kl = k.lower()
        if kl in ('host', 'content-length', 'transfer-encoding'):
            continue
        headers[k] = v

    # 读取并修补 body
    body = request.get_data()
    is_json = False
    parsed_json = None
    ct = request.content_type or ""
    if 'application/json' in ct:
        try:
            parsed_json = json.loads(body)
            msg_count = len(parsed_json.get("messages", []))
            assistant_msgs = [m for m in parsed_json.get("messages", []) if m.get("role") == "assistant"]
            need_patch = sum(1 for m in assistant_msgs if not m.get("reasoning_content"))
            print(f"[REQ] messages={msg_count}, assistant={len(assistant_msgs)}, need_patch={need_patch}, stream={parsed_json.get('stream')}")
            parsed_json = _patch_messages(parsed_json)
            body = json.dumps(parsed_json, ensure_ascii=False).encode('utf-8')
            is_json = True
        except Exception as e:
            print(f"[WARN] 请求 JSON 解析/修补失败: {e}")
    else:
        print(f"[REQ] 非 JSON 请求，content-type={ct}")

    print(f"[PROXY] 转发请求 -> {target_url}")

    # 发往真实 API
    upstream = requests.request(
        method=request.method,
        url=target_url,
        headers=headers,
        data=body,
        params=request.args,
        stream=True,
        timeout=(10, 300)
    )

    content_type = upstream.headers.get('Content-Type', '')
    print(f"[RESP] status={upstream.status_code}, content-type={content_type}")

    # 判定是否为 SSE：Content-Type 显式声明，或请求里带了 stream=true
    is_sse = 'text/event-stream' in content_type or (is_json and parsed_json.get("stream"))
    print(f"[RESP] 响应模式: {'SSE 流式' if is_sse else '非流式 / 透传'}")

    if is_sse:
        return Response(
            _generate_and_cache_stream(upstream),
            status=upstream.status_code,
            headers={k: v for k, v in upstream.headers.items()
                     if k.lower() not in ('content-length', 'transfer-encoding')}
        )
    else:
        # 非流式：尝试 JSON 缓存后直接返回
        try:
            data = upstream.json()
            _cache_nonstream(data)
            return Response(
                json.dumps(data, ensure_ascii=False),
                status=upstream.status_code,
                content_type='application/json'
            )
        except Exception:
            # 不是 JSON，直接透传
            print(f"[RESP] 非 JSON 响应，直接透传")
            return Response(
                upstream.iter_content(chunk_size=8192),
                status=upstream.status_code,
                headers={k: v for k, v in upstream.headers.items()
                         if k.lower() not in ('content-length', 'transfer-encoding')}
            )


# ==================== 入口 ====================

if __name__ == '__main__':
    print("=" * 60)
    print(" Reasoning Content Proxy")
    print(" 解决 Claude Code + DeepSeek/Moonshot reasoning_content 回传报错")
    print("=" * 60)
    print(f" 目标 API : {TARGET_BASE}")
    print(f" 本地端口 : {PROXY_PORT}")
    print("")
    print(" 启动前请确保已安装依赖:")
    print("   pip install flask requests")
    print("")
    print(" 使用方法:")
    print("   1. python proxy.py")
    print(f"   2. 将 Claude Code / ccswitch 的 Base URL 设为 http://127.0.0.1:{PROXY_PORT}")
    print("   3. 正常使用 Claude Code 即可")
    print("=" * 60)

    # 压低 Flask 默认日志，避免刷屏
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=PROXY_PORT, threaded=True)
