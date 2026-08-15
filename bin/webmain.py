# -*- coding: utf-8 -*-
import os
import json
import threading
import time
import ipaddress

from flask import Flask, request, jsonify, send_from_directory

from bin import logger, mjbconfig, mjbstatus, worker, message

_webui_app = None
_start_time = time.time()


def _is_valid_ip(ip_str):
    """校验字符串是否为合法的 IPv4/IPv6 地址"""
    try:
        ipaddress.ip_address(ip_str)
        return True
    except ValueError:
        return False


def _is_trusted_proxy(ip_str):
    """判断直连来源是否为可信代理（本机/内网/链路本地地址）

    仅当请求确实来自可信代理时，才允许信任 X-Forwarded-For 等代理头，
    否则客户端可伪造请求头伪装真实 IP。
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _get_real_client_ip():
    """获取真实客户端 IP

    策略：
    1. 直连来源不是可信代理（即用户直接访问 WebUI）时，不使用任何代理头，
       以 socket 连接地址为准，防止伪造 X-Forwarded-For 欺骗。
    2. 直连来源是可信代理（反向代理场景）时，解析 X-Forwarded-For，
       取最左侧（客户端最初来源）第一个合法 IP；无则回退 X-Real-IP；
       都无效则回退连接地址。
    """
    remote_addr = request.remote_addr or ""

    if remote_addr and _is_trusted_proxy(remote_addr):
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        if x_forwarded_for:
            # 格式如 "client, proxy1, proxy2"，最左侧为客户端真实来源
            for part in x_forwarded_for.split(","):
                part = part.strip()
                if _is_valid_ip(part):
                    return part
        x_real_ip = request.headers.get("X-Real-IP")
        if x_real_ip and _is_valid_ip(x_real_ip.strip()):
            return x_real_ip.strip()

    return remote_addr or "unknown"


def _create_app():
    """创建 Flask WebUI 应用"""
    global _webui_app
    webui_dir = mjbconfig.get_webui_dir()
    if webui_dir is None or not os.path.exists(webui_dir):
        logger.warning("webui_dir 未设置或不存在，WebUI 无法启动")
        return None

    app = Flask(__name__, static_folder=webui_dir, template_folder=webui_dir)

    @app.before_request
    def before_request():
        request.client_real_ip = _get_real_client_ip()
        logger.debug(f"请求路径: {request.path}, 客户端IP: {request.client_real_ip}")

    @app.route("/css/<path:filename>")
    def serve_css(filename):
        return send_from_directory(os.path.join(webui_dir, "css"), filename)

    @app.route("/js/<path:filename>")
    def serve_js(filename):
        return send_from_directory(os.path.join(webui_dir, "js"), filename)

    @app.route("/<path:filename>")
    def serve_static(filename):
        static_path = os.path.join(webui_dir, filename)
        if os.path.exists(static_path):
            return send_from_directory(webui_dir, filename)
        if not filename.startswith("api/") and os.path.exists(os.path.join(webui_dir, "index.html")):
            return send_from_directory(webui_dir, "index.html")
        return "文件不存在", 404

    @app.route("/")
    def index():
        index_path = os.path.join(webui_dir, "index.html")
        if os.path.exists(index_path):
            return send_from_directory(webui_dir, "index.html")
        return "index.html文件不存在", 404

    @app.route("/status")
    def status():
        index_path = os.path.join(webui_dir, "index.html")
        if os.path.exists(index_path):
            return send_from_directory(webui_dir, "index.html")
        return "<h3>状态页面 - 正在开发中</h3>"

    @app.route("/help")
    def help_page():
        help_path = os.path.join(webui_dir, "helplist.html")
        if os.path.exists(help_path):
            return send_from_directory(webui_dir, "helplist.html")
        return "<h3>帮助页面 - 正在开发中</h3>"

    @app.route("/api/commands", methods=["GET"])
    def get_commands():
        try:
            group_data = mjbconfig.get_config()
            result = {
                "commands": group_data.get("commands", {}),
                "commandsinfo": group_data.get("commandsinfo", {}),
                "commandscategory": group_data.get("commandscategory", {}),
                "bot_admin_commands": group_data.get("bot_admin_commands", []),
                "group_admin_commands": group_data.get("group_admin_commands", []),
                "commandshidden": group_data.get("commandshidden", []),
                "version": mjbconfig.get_version(),
            }
            return jsonify(result)
        except Exception as e:
            logger.error(f"获取命令列表失败: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/status", methods=["GET"])
    def get_status():
        try:
            # 获取线程信息
            thread_info = [{
                "name": t.name, "ident": t.ident,
                "is_alive": t.is_alive(), "daemon": t.daemon,
            } for t in threading.enumerate()]

            # 计算运行时长
            uptime_seconds = int(time.time() - _start_time)
            days = uptime_seconds // 86400
            hours = (uptime_seconds % 86400) // 3600
            minutes = (uptime_seconds % 3600) // 60
            seconds = uptime_seconds % 60
            if days > 0:
                uptime_str = f"{days}天{hours}小时{minutes}分钟{seconds}秒"
            elif hours > 0:
                uptime_str = f"{hours}小时{minutes}分钟{seconds}秒"
            elif minutes > 0:
                uptime_str = f"{minutes}分钟{seconds}秒"
            else:
                uptime_str = f"{seconds}秒"

            # 获取 CPU 和内存占用
            try:
                import psutil
                system_cpu_percent = psutil.cpu_percent(interval=1)
                memory_info = psutil.virtual_memory()
                system_memory_percent = memory_info.percent
                current_process = psutil.Process(os.getpid())
                current_process.cpu_percent(interval=0.1)
                process_cpu_percent = current_process.cpu_percent(interval=1) / psutil.cpu_count()
                process_memory_percent = current_process.memory_percent()
                cpu_usage = f"{process_cpu_percent:.1f}%/{system_cpu_percent:.1f}%"
                memory_usage = f"{process_memory_percent:.1f}%/{system_memory_percent:.1f}%"
            except Exception as e:
                logger.debug(f"获取CPU/内存信息失败: {e}")
                cpu_usage = "无法获取"
                memory_usage = "无法获取"

            client_ip = getattr(request, "client_real_ip", request.remote_addr)

            # 获取心跳信息（带 5 秒超时检测）
            heartbeat_info = message.get_heartbeat_info()

            status_data = {
                "bot_status": "running",
                "thread_count": len(thread_info),
                "threads": thread_info,
                "uptime": uptime_str,
                "cpu_usage": cpu_usage,
                "memory_usage": memory_usage,
                "bot_name": mjbconfig.get_botname(),
                "version": mjbconfig.get_version(),
                "client_ip": client_ip,
                "heartbeat": heartbeat_info,
                "background_tasks": worker.get_status(),
            }
            return jsonify({"success": True, "data": status_data})
        except Exception as e:
            logger.error(f"获取状态失败: {e}")
            return jsonify({"success": False, "message": str(e)})

    _webui_app = app
    return app


def start_webui():
    """启动 Flask WebUI 服务（阻塞）"""
    app = _create_app()
    if app is None:
        return
    logger.info("Flask WebUI服务启动在 http://127.0.0.1:34343")
    try:
        app.run(host="0.0.0.0", port=34343, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"启动Flask WebUI服务失败: {e}")


def start_webui_thread():
    """在 daemon 线程中启动 Flask WebUI"""
    worker.start_background("WebUI-Thread", start_webui, daemon=True)
