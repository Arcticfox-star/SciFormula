# -*- coding: utf-8 -*-
"""
mcp_server.py —— 把 SciFormula 的工具暴露成一个 MCP 服务器（零第三方依赖）

【为什么走 MCP 这条路】
AGH 官方文档 `docs/guide/mcp.md` 明确写了两种给智能体加能力的方式：
  1) 写后端插件（docs/develop/backend.md）——需要 Node + Cordis + TypeBox，是 JS 侧生态
  2) 接入一个 MCP 服务（docs/guide/mcp.md）——`agnes.mjs mcp add <名字> --stdio <可执行文件> --arg ...`
我们的算法是 Python 写的，走第 2 条路不用改一行数学代码，而且签名里那句
「Do not pass a complete shell command as the executable」也正好对上——
我们传的是解释器本体，脚本路径用 --arg 分开传。

【协议要点（MCP stdio 传输）】
  · 消息是**按行分隔的 JSON-RPC 2.0**：一行一条，换行结尾。
  · stdout 只能出现协议消息。任何调试输出都必须走 stderr，否则会污染流、
    让客户端解析失败。本文件因此把所有业务代码的 stdout 重定向到 stderr。
  · 需要处理的方法：initialize / notifications/initialized / tools/list / tools/call / ping。
  · 通知（没有 id）不需要回复；请求（有 id）必须回一条同 id 的响应。

【自检（不需要 AGH 也能验证协议正确）】
    python mcp_server.py --selftest
"""

import io
import os
import sys
import json
import contextlib

# 保证 import agh_tools / pipeline 不依赖调用方的当前目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agh_tools  # noqa: E402

SERVER_INFO = dict(name="sciformula", version="0.1.0")
DEFAULT_PROTOCOL = "2024-11-05"

# JSON-RPC 标准错误码
E_PARSE, E_INVALID_REQ, E_NO_METHOD, E_INVALID_PARAMS, E_INTERNAL = -32700, -32600, -32601, -32602, -32603


def _setup_stdio():
    """
    把标准流强制设成 UTF-8 —— **这一条在 Windows 上是必需的，不是可选项。**

    为什么：MCP 规范规定 stdio 传输使用 UTF-8 编码，但 Windows 上 Python 的
    stdin/stdout 默认用本地代码页（中文系统是 cp936/GBK）。
    而我们的工具返回值里**大量是中文**（诊断信息、公式说明、候选表达式说明），
    `json.dumps(..., ensure_ascii=False)` 会把它们原样写出去。
    不设 UTF-8 的后果：写出去的是 GBK 字节，客户端按 UTF-8 解码 →
    轻则中文乱码，重则 JSON 解析失败、整个 MCP 连接报废。

    这个坑在 Linux/macOS 上**永远遇不到**（那边默认就是 UTF-8），
    所以特别留这段注释：以后若有人把这套代码搬到别的语言/平台，
    别忘了这一层要有等价处理。

    另外 stdin 用 errors="replace"：客户端万一发来不合法的字节，
    宁可把它替换成占位符也不要让进程崩掉——MCP 服务器崩了，AGH 侧只会看到"连接断开"，
    很难排查。
    """
    for stream, errors in ((sys.stdin, "replace"), (sys.stdout, None), (sys.stderr, "replace")):
        try:
            kwargs = {"encoding": "utf-8"}
            if errors:
                kwargs["errors"] = errors
            stream.reconfigure(**kwargs)
        except Exception:
            # 极端情况下（流被替换成不可重配置的对象）忽略即可，不阻断启动
            pass


def _log(*parts):
    """所有日志走 stderr —— stdout 是协议通道，绝不能碰"""
    print("[sciformula]", *parts, file=sys.stderr, flush=True)


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ok(msg_id, result):
    _send(dict(jsonrpc="2.0", id=msg_id, result=result))


def _err(msg_id, code, message, data=None):
    err = dict(code=code, message=message)
    if data is not None:
        err["data"] = data
    _send(dict(jsonrpc="2.0", id=msg_id, error=err))


# ---------------------------------------------------------------------------
# 各方法处理
# ---------------------------------------------------------------------------
def _handle_initialize(msg_id, params):
    # 协议版本协商：这里回显客户端提出的版本。
    # 原因：AGH 是 pre-alpha，各版本间协议细节仍在变；回显客户的版本比
    # 强行声明一个固定版本更不容易在握手阶段就被断开。
    proto = (params or {}).get("protocolVersion") or DEFAULT_PROTOCOL
    _ok(msg_id, dict(
        protocolVersion=proto,
        capabilities=dict(tools=dict(listChanged=False)),
        serverInfo=SERVER_INFO,
        instructions=("SciFormula：从带噪声的观测数据中发现物理定律公式，"
                      "并用量纲一致性约束剪枝搜索空间。"
                      "典型调用顺序：list_problems → load_problem → check_units → "
                      "build_candidate_library → fit_sparse → verify_formula。"
                      "注意：目标量的物理单位不会直接给出，需要你根据变量名与物理语境推断，"
                      "并由 build_candidate_library 的剪枝结果反向检验推断是否正确。"),
    ))


def _handle_tools_list(msg_id):
    _ok(msg_id, dict(tools=agh_tools.tool_definitions()))


def _handle_tools_call(msg_id, params):
    params = params or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    if not name:
        _err(msg_id, E_INVALID_PARAMS, "tools/call 缺少 name")
        return

    # 业务代码里任何 print 都会被截到 stderr，避免污染协议流
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            res = agh_tools.run_tool(name, args)
    except Exception as exc:                                  # noqa: BLE001
        stray = buf.getvalue().strip()
        if stray:
            _log("stray stdout:", stray[:500])
        _err(msg_id, E_INTERNAL, "工具执行失败：%s: %s" % (type(exc).__name__, exc))
        return

    stray = buf.getvalue().strip()
    if stray:
        _log("stray stdout from", name, ":", stray[:500])

    is_error = not bool(res.get("ok", True))
    _ok(msg_id, dict(
        content=[dict(type="text", text=json.dumps(res, ensure_ascii=False, indent=2))],
        structuredContent=res,
        isError=is_error,
    ))


def dispatch(msg):
    """处理一条进来的消息；返回 True 表示继续循环"""
    if not isinstance(msg, dict):
        _err(None, E_INVALID_REQ, "消息必须是 JSON 对象")
        return True

    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params")

    # 通知：没有 id，不回复
    if msg_id is None and method:
        if method == "notifications/initialized":
            _log("client initialized")
        elif method == "notifications/cancelled":
            _log("cancelled:", (params or {}).get("requestId"))
        else:
            _log("ignoring notification:", method)
        return True

    try:
        if method == "initialize":
            _handle_initialize(msg_id, params)
        elif method == "tools/list":
            _handle_tools_list(msg_id)
        elif method == "tools/call":
            _handle_tools_call(msg_id, params)
        elif method == "ping":
            _ok(msg_id, {})
        elif method in ("resources/list", "prompts/list"):
            # 我们只提供工具，这两类能力按协议返回空列表即可
            key = method.split("/")[0]
            _ok(msg_id, {key: []})
        else:
            _err(msg_id, E_NO_METHOD, "不支持的方法：%s" % method)
    except Exception as exc:                                  # noqa: BLE001
        _err(msg_id, E_INTERNAL, "%s: %s" % (type(exc).__name__, exc))
    return True


def serve():
    _setup_stdio()
    _log("sciformula MCP server 启动（stdio）。日志走 stderr，协议走 stdout。")
    _log("标准流编码已强制为 UTF-8（Windows 必需）。")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as exc:
            _err(None, E_PARSE, "JSON 解析失败：%s" % exc)
            continue
        dispatch(msg)
    _log("stdin 已关闭，退出。")
    return 0


# ---------------------------------------------------------------------------
# 自检：不接 AGH，直接把协议走一遍
# ---------------------------------------------------------------------------
def selftest():
    _setup_stdio()

    def run(messages):
        out = []
        real_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            for m in messages:
                dispatch(m)
            text = sys.stdout.getvalue()
        finally:
            sys.stdout = real_stdout
        for ln in text.splitlines():
            if ln.strip():
                out.append(json.loads(ln))
        return out

    print("=== MCP 协议自检 ===")
    replies = run([
        dict(jsonrpc="2.0", id=1, method="initialize",
             params=dict(protocolVersion="2024-11-05", capabilities={},
                         clientInfo=dict(name="selftest", version="0"))),
        dict(jsonrpc="2.0", method="notifications/initialized"),
        dict(jsonrpc="2.0", id=2, method="tools/list"),
        dict(jsonrpc="2.0", id=3, method="ping"),
    ])
    assert len(replies) == 3, "预期 3 条响应（通知不回），实际 %d" % len(replies)

    init = replies[0]["result"]
    print("1. initialize    → protocolVersion=%s server=%s"
          % (init["protocolVersion"], init["serverInfo"]["name"]))

    tools = replies[1]["result"]["tools"]
    print("2. tools/list    → %d 个工具：%s" % (len(tools), ", ".join(t["name"] for t in tools)))
    for t in tools:
        assert "inputSchema" in t and t["inputSchema"]["type"] == "object", t["name"]
    print("                  （每个工具都有合法 inputSchema ✔）")

    print("3. ping          → %s" % ("ok" if replies[2]["result"] == {} else replies[2]))

    # 真正调一次工具，验证 tools/call 的返回结构
    replies = run([dict(jsonrpc="2.0", id=4, method="tools/call",
                        params=dict(name="list_problems", arguments={}))])
    r = replies[0]["result"]
    ok_shape = ("content" in r and r["content"][0]["type"] == "text"
                and "structuredContent" in r and r["isError"] is False)
    print("4. tools/call    → list_problems 返回 %d 个问题，返回结构合法=%s"
          % (r["structuredContent"]["count"], ok_shape))

    # 错误路径
    replies = run([dict(jsonrpc="2.0", id=5, method="tools/call",
                        params=dict(name="fit_sparse", arguments={}))])
    r = replies[0]["result"]
    print("5. 错误路径      → isError=%s，错误信息=%r"
          % (r["isError"], r["structuredContent"].get("error")))

    # stdout 洁净度：重定向期间业务代码不应往 stdout 写东西
    print()
    print("=== 全部通过：协议可实现、stdout 洁净、错误可被智能体读到 ===")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(serve())
