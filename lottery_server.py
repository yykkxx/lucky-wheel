#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
幸运大转盘 —— 本地 Web 抽奖程序
================================================
* 纯 Python 标准库实现，零第三方依赖，Windows / macOS / Linux 通用
* 启动后自动打开系统默认浏览器，访问 http://127.0.0.1:<端口>/
* 从同目录的 prizes.json 读取奖品种类与中奖概率（可热更新，改完存盘即生效）
* 中奖结果由服务端按概率权重抽取，前端负责转盘旋转动画 + 粒子特效 + 结果展示

用法:
    python lottery_server.py                 # 默认 8000 端口并自动打开浏览器
    python lottery_server.py -p 9000         # 指定端口
    python lottery_server.py --no-browser    # 不自动打开浏览器
    python lottery_server.py --host 0.0.0.0  # 允许局域网其它设备访问
    python lottery_server.py --reset         # 清空库存与历史记录后启动
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- 路径与常量

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
CONFIG_FILE = os.path.join(BASE_DIR, "prizes.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")

MAX_HISTORY = 200          # 服务端最多保留的历史条数
_LOCK = threading.RLock()  # 保护配置与状态文件的读写


# ---------------------------------------------------------------- 控制台输出

def setup_console() -> None:
    """尽量让 Windows 控制台正确显示中文与 emoji。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 配置读取

DEFAULT_CONFIG = {
    "title": "幸运大转盘",
    "subtitle": "转动转盘，好运即刻降临",
    "draw_limit": 0,
    "prizes": [
        {"name": "一等奖", "icon": "🏆", "probability": 1, "color": "#ff3d81"},
        {"name": "二等奖", "icon": "🎁", "probability": 4, "color": "#ff9f43"},
        {"name": "三等奖", "icon": "🎉", "probability": 10, "color": "#ffd93d"},
        {"name": "谢谢参与", "icon": "🍀", "probability": 85, "color": "#5a6b8c"},
    ],
}


def _to_float(value, default=None):
    """把 JSON 里的数字/字符串安全地转成 float。"""
    if value is None:
        return default
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return default


def normalize_config(raw: dict) -> dict:
    """
    校验并规范化配置：
      * 支持 probability（0~1 小数 或 0~100 百分数，自动识别）与 weight（相对权重）
      * 概率自动归一化到总和为 1
      * stock 为 null / 缺省 表示不限量
    """
    if not isinstance(raw, dict):
        raise ValueError("prizes.json 的根节点必须是一个 JSON 对象")

    prizes_in = raw.get("prizes")
    if not isinstance(prizes_in, list) or not prizes_in:
        raise ValueError("prizes.json 缺少非空的 prizes 数组")

    # 1) 先解析每一项的权重
    items = []
    for idx, item in enumerate(prizes_in):
        if not isinstance(item, dict):
            raise ValueError("prizes[%d] 必须是对象" % idx)
        name = str(item.get("name") or ("奖品 %d" % (idx + 1))).strip()

        weight = _to_float(item.get("weight"))
        if weight is None:
            weight = _to_float(item.get("probability"))
        if weight is None:
            weight = _to_float(item.get("prob"))
        if weight is None:
            weight = _to_float(item.get("rate"))
        if weight is None or weight < 0:
            weight = 0.0

        stock = item.get("stock", None)
        if stock is not None:
            try:
                stock = int(stock)
                if stock < 0:
                    stock = 0
            except (TypeError, ValueError):
                stock = None

        items.append({
            "name": name,
            "icon": str(item.get("icon") or "🎁").strip() or "🎁",
            "desc": str(item.get("desc") or "").strip(),
            "color": str(item.get("color") or "").strip(),
            "weight": weight,
            "stock": stock,
        })

    total = sum(it["weight"] for it in items)

    # 2) 全部为 0 时退化为等概率，避免程序不可用
    if total <= 0:
        for it in items:
            it["weight"] = 1.0
        total = float(len(items))

    # 3) 归一化
    for it in items:
        it["probability"] = it["weight"] / total

    return {
        "title": str(raw.get("title") or DEFAULT_CONFIG["title"]),
        "subtitle": str(raw.get("subtitle") or DEFAULT_CONFIG["subtitle"]),
        "draw_limit": int(_to_float(raw.get("draw_limit"), 0) or 0),
        "prizes": items,
    }


def load_config() -> dict:
    """读取 prizes.json；文件不存在或损坏时回退到内置默认配置。"""
    with _LOCK:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return normalize_config(raw)
        except FileNotFoundError:
            log("[!] 未找到 prizes.json，已自动生成一份默认配置。")
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
            except OSError:
                pass
            return normalize_config(DEFAULT_CONFIG)
        except (json.JSONDecodeError, ValueError) as exc:
            log("[!] prizes.json 解析失败(%s)，本次使用内置默认配置。" % exc)
            return normalize_config(DEFAULT_CONFIG)


# ---------------------------------------------------------------- 状态持久化

def _blank_state() -> dict:
    return {"used": {}, "history": [], "draws": 0}


def load_state() -> dict:
    with _LOCK:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError
        except Exception:
            data = _blank_state()
        base = _blank_state()
        base.update({
            "used": data.get("used") if isinstance(data.get("used"), dict) else {},
            "history": data.get("history") if isinstance(data.get("history"), list) else [],
            "draws": int(data.get("draws") or 0),
        })
        return base


def save_state(state: dict) -> None:
    with _LOCK:
        try:
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass


# ---------------------------------------------------------------- 抽奖核心

def remaining_of(prize: dict, state: dict) -> int | None:
    """返回剩余数量；None 表示不限量。"""
    if prize.get("stock") is None:
        return None
    used = int(state["used"].get(prize["name"], 0))
    return max(0, int(prize["stock"]) - used)


def pick_prize(config: dict, state: dict) -> tuple[int, dict]:
    """
    按概率权重抽取奖品，返回 (下标, 奖品信息)。
    已抽完（剩余 0）的奖品会被自动排除；若全部抽完则抛出 RuntimeError。
    """
    prizes = config["prizes"]
    pool = []
    for idx, prize in enumerate(prizes):
        left = remaining_of(prize, state)
        if left == 0:
            continue
        if prize["weight"] <= 0 and prize.get("stock") is None:
            # 概率为 0 且不限量的奖品，直接不参与
            continue
        pool.append((idx, prize["weight"] if prize["weight"] > 0 else 0.0001))

    if not pool:
        raise RuntimeError("所有奖品均已抽完")

    total = sum(w for _, w in pool)
    r = random.random() * total
    acc = 0.0
    chosen = pool[-1][0]
    for idx, w in pool:
        acc += w
        if r <= acc:
            chosen = idx
            break
    return chosen, prizes[chosen]


def do_draw(config: dict, state: dict) -> dict:
    """执行一次抽奖，更新并持久化状态，返回给前端的 JSON。"""
    with _LOCK:
        idx, prize = pick_prize(config, state)

        name = prize["name"]
        state["used"][name] = int(state["used"].get(name, 0)) + 1
        state["draws"] = int(state.get("draws", 0)) + 1

        record = {
            "index": idx,
            "name": name,
            "icon": prize["icon"],
            "desc": prize["desc"],
            "color": prize["color"],
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "seq": state["draws"],
        }
        state["history"].insert(0, record)
        del state["history"][MAX_HISTORY:]
        save_state(state)

        record["remaining"] = remaining_of(prize, state)
        record["draws"] = state["draws"]
        return record


def config_for_client(config: dict, state: dict) -> dict:
    """附加剩余数量后的配置，供前端渲染。"""
    prizes = []
    for p in config["prizes"]:
        item = dict(p)
        item["remaining"] = remaining_of(p, state)
        prizes.append(item)
    return {
        "title": config["title"],
        "subtitle": config["subtitle"],
        "draw_limit": config["draw_limit"],
        "draws": state.get("draws", 0),
        "prizes": prizes,
    }


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    server_version = "LuckyWheel/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 工具方法 ----------

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json({"ok": False, "error": message}, code)

    def log_message(self, fmt, *args):  # 精简日志
        return

    # ---------- 路由 ----------

    def do_GET(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]

        if path in ("/", "/index.html"):
            return self._serve_file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")

        if path == "/api/config":
            with _LOCK:
                config = load_config()
                state = load_state()
                return self._json({"ok": True, "config": config_for_client(config, state)})

        if path == "/api/history":
            with _LOCK:
                state = load_state()
                return self._json({"ok": True, "history": state["history"][:50],
                                   "draws": state.get("draws", 0)})

        if path == "/api/health":
            return self._json({"ok": True, "time": time.time()})

        # 静态资源
        rel = path.lstrip("/")
        if rel and ".." not in rel and not os.path.isabs(rel):
            target = os.path.join(WEB_DIR, *rel.split("/"))
            if os.path.isfile(target):
                return self._serve_file(target, self._guess_type(target))

        self._error(404, "Not Found")

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        # 读取请求体（本程序不需要参数，但必须消费掉，否则连接复用会出错）
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 0:
                self.rfile.read(min(length, 1 << 20))
        except Exception:
            pass

        if path == "/api/draw":
            with _LOCK:
                config = load_config()
                state = load_state()
                try:
                    record = do_draw(config, state)
                except RuntimeError as exc:
                    return self._error(409, str(exc))
                return self._json({"ok": True, "result": record,
                                   "prizes": config_for_client(config, state)["prizes"]})

        if path == "/api/reset":
            with _LOCK:
                save_state(_blank_state())
                return self._json({"ok": True})

        self._error(404, "Not Found")

    # ---------- 静态文件 ----------

    @staticmethod
    def _guess_type(path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".ico": "image/x-icon",
            ".woff2": "font/woff2",
        }.get(ext, "application/octet-stream")

    def _serve_file(self, path: str, ctype: str) -> None:
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._error(404, "Not Found")
        self._send(200, body, ctype)


# ---------------------------------------------------------------- 启动流程

def find_free_port(host: str, start: int, tries: int = 20) -> int:
    """从 start 开始找一个可用端口。"""
    for offset in range(tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host if host != "0.0.0.0" else "", port))
                return port
            except OSError:
                continue
    raise SystemExit("[x] 端口 %d-%d 均被占用，请用 -p 指定其它端口。" % (start, start + tries))


def local_ip() -> str:
    """获取本机在局域网中的 IP，用于提示手机扫码访问。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


BANNER = r"""
   __            __          __
  / /  __ __ ___/ /__ __ __ / /  __ __ ___ __
 / _ \/ // / _  / -_) // // // _ \/ // / -_) /
/_.__/\_,_/\_,_/\__/\_, /_//_/_.__/\_,_/\__/_/
                    /___/   Lucky Wheel  v1.0
"""


def main() -> None:
    setup_console()

    parser = argparse.ArgumentParser(
        description="幸运大转盘 —— 本地 Web 抽奖程序（纯标准库，零依赖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-p", "--port", type=int, default=8000, help="监听端口，默认 8000")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--reset", action="store_true", help="启动前清空库存用量与历史记录")
    args = parser.parse_args()

    if not os.path.isfile(os.path.join(WEB_DIR, "index.html")):
        log("[x] 找不到 web/index.html，请确认 lottery_server.py 与 web 目录在同一文件夹内。")
        raise SystemExit(1)

    if args.reset:
        save_state(_blank_state())
        log("[*] 已清空历史记录与库存用量。")

    if not os.path.isfile(CONFIG_FILE):
        load_config()  # 自动生成默认配置

    config = load_config()
    state = load_state()
    port = find_free_port(args.host, args.port)

    try:
        httpd = ThreadingHTTPServer((args.host, port), Handler)
    except OSError as exc:
        raise SystemExit("[x] 服务启动失败：%s" % exc)

    httpd.daemon_threads = True

    url = "http://127.0.0.1:%d/" % port

    log(BANNER)
    log("  奖品配置 : %s" % CONFIG_FILE)
    log("  奖品种类 : %d 种，累计已抽 %d 次" % (len(config["prizes"]), state.get("draws", 0)))
    log("  访问地址 : %s" % url)
    if args.host == "0.0.0.0":
        log("  局域网   : http://%s:%d/" % (local_ip(), port))
    log("  停止服务 : 在本窗口按 Ctrl + C")
    log("")

    # 延迟一点再打开浏览器，确保服务已就绪
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("\n[*] 正在关闭服务 ...")
    finally:
        httpd.shutdown()
        httpd.server_close()
        log("[*] 已停止，感谢使用。")


if __name__ == "__main__":
    main()
