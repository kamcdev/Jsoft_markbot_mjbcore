# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import queue
import random
import string
import threading
import ipaddress

from flask import Flask, request, jsonify, send_from_directory, make_response

from bin import logger, mjbconfig, mjbstatus, worker, message

_webui_app = None
_start_time = time.time()

# 验证令牌持久化文件（与 modules/cloudlogin.py 共用，经 mjbconfig 模块配置接口读写）
HOPEXAUTH_FILE = "hopexauth.json"


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


def _load_hopexauth():
    """读取验证令牌文件 hopexauth.json（WebUI 线程无账号上下文，落到默认账号）"""
    data = mjbconfig.load_module_config(HOPEXAUTH_FILE)
    return data if isinstance(data, dict) else {}


def _save_hopexauth(data):
    """写入验证令牌文件 hopexauth.json（默认账号）"""
    mjbconfig.save_module_config(HOPEXAUTH_FILE, data)


def _get_cloudlogin():
    """运行时获取 cloudlogin 模块（验证令牌共享状态的持有者）

    vcode 命令（modules/cloudlogin.py）与 vstatus 接口需共享内存中的
    auth_tokens/auth_callbacks/auth_lock，这里通过 sys.modules 查找而非顶层
    import，既避免 bin -> modules 的循环导入，又保证 mjb.reload 重新加载
    模块后取到新实例。返回 None 表示模块不可用。
    """
    mod = sys.modules.get("modules.cloudlogin")
    if mod is None:
        try:
            import importlib
            mod = importlib.import_module("modules.cloudlogin")
        except Exception as e:
            logger.error(f"加载 cloudlogin 模块失败: {e}")
            return None
    return mod


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
            result = {
                "commands": mjbconfig.get_commands_map(),
                "commandsinfo": mjbconfig.get_commandsinfo(),
                "commandscategory": mjbconfig.get_commandscategory(),
                "bot_admin_commands": mjbconfig.get_bot_admin_commands(),
                "group_admin_commands": mjbconfig.get_group_admin_commands(),
                "commandshidden": mjbconfig.get_commandshidden(),
                "version": mjbconfig.get_mjbcver_raw(),
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

            # 获取多账号心跳信息（带 5 秒超时检测）
            all_heartbeats = message.get_all_heartbeat_info()
            # 聚合：正常数 / 异常数（None 键视为无账号旧场景，单独计数）
            accounts_status = {}
            online_count = 0
            offline_count = 0
            for bid, info in all_heartbeats.items():
                key = bid if bid is not None else "default"
                is_online = bool(info.get("online", False))
                accounts_status[key] = {
                    "online": is_online,
                    "interval": info.get("interval", 0),
                    "timestamp": info.get("timestamp", 0),
                }
                if is_online:
                    online_count += 1
                else:
                    offline_count += 1
            total_count = online_count + offline_count
            # 兼容旧字段 heartbeat（默认账号）
            default_heartbeat = message.get_heartbeat_info()
            # 多账号总览字段
            heartbeat_summary = {
                "online": online_count > 0 and offline_count == 0,
                "online_count": online_count,
                "offline_count": offline_count,
                "total_count": total_count,
                "accounts": accounts_status,
                # 兼容旧字段
                "interval": default_heartbeat.get("interval", 0),
                "timestamp": default_heartbeat.get("timestamp", 0),
                "raw_status": default_heartbeat.get("raw_status", {}),
            }

            status_data = {
                "bot_status": "running",
                "thread_count": len(thread_info),
                "threads": thread_info,
                "uptime": uptime_str,
                "cpu_usage": cpu_usage,
                "memory_usage": memory_usage,
                "bot_name": mjbconfig.get_botname(),
                "version": mjbconfig.get_mjbcver_raw(),
                "client_ip": client_ip,
                "heartbeat": heartbeat_summary,
                "background_tasks": worker.get_status(),
            }
            return jsonify({"success": True, "data": status_data})
        except Exception as e:
            logger.error(f"获取状态失败: {e}")
            return jsonify({"success": False, "message": str(e)})

    # API路由 - 生成验证令牌（从 1.0.2 迁移）
    @app.route('/fe36e4d5-de54-4a06-a872-1c4c21162fde/getauthcode', methods=['GET'])
    def get_auth_code():
        try:
            # 获取请求数据
            data = request.get_json(silent=True)

            # 验证请求格式
            if not data or 'bbxxxxx' not in data:
                return jsonify({'error': 'Invalid request format'}), 400

            # 验证bbxxxxx内容
            if data.get('bbxxxxx') != 'hopexaaaaaaa':
                return jsonify({'error': 'Authentication failed'}), 403

            # 生成六位数字和大写字母混合的验证码
            characters = string.digits + string.ascii_uppercase
            authcode = ''.join(random.choices(characters, k=6))

            # 获取当前时间戳
            create_time = int(time.time())

            # 读取现有数据并添加新令牌
            auth_data = _load_hopexauth()
            auth_data[authcode] = create_time
            _save_hopexauth(auth_data)

            logger.info(f"成功生成验证令牌: {authcode}, 时间: {create_time}")

            return jsonify({
                'authcode': authcode,
                'create_time': create_time,
                'message': '验证令牌生成成功'
            }), 200

        except Exception as e:
            logger.error(f"处理getauthcode请求失败: {e}")
            return jsonify({'error': str(e)}), 500

    # API路由 - 验证令牌状态（从 1.0.2 迁移）
    @app.route('/50584863-774e-4731-8df0-426144380df6/vstatus', methods=['POST'])
    def verify_token_status():
        # 验证令牌内存状态与 modules/cloudlogin.py 的 vcode 命令共享
        mod = _get_cloudlogin()
        if mod is None:
            return jsonify({'error': '验证模块未加载'}), 503
        auth_tokens = mod.auth_tokens
        auth_callbacks = mod.auth_callbacks
        auth_lock = mod.auth_lock

        def process_request(request_data, callback_queue):
            try:
                # 验证请求格式
                if not request_data or 'bbxxxxx' not in request_data or 'authcode' not in request_data:
                    logger.warning("错误: 无效的请求格式")
                    callback_queue.put({'error': 'Invalid request format'})
                    return

                # 验证bbxxxxx内容
                if request_data.get('bbxxxxx') != 'hopexaaaaaaa':
                    logger.warning("错误: 认证失败")
                    callback_queue.put({'error': 'Authentication failed'})
                    return

                authcode = request_data.get('authcode')

                # 检查令牌是否存在
                auth_data = _load_hopexauth()
                if authcode not in auth_data:
                    logger.info(f"令牌 {authcode} 不存在")
                    callback_queue.put({
                        'message': '令牌不存在',
                        'status': False
                    })
                    return

                # 令牌存在，开始keep-alive处理
                # 生成回调ID
                callback_id = f"{authcode}_{int(time.time())}"

                # 设置超时时间（5分钟）
                timeout_time = int(time.time()) + 300

                with auth_lock:
                    # 存储令牌信息
                    auth_tokens[callback_id] = {
                        'authcode': authcode,
                        'timeout_time': timeout_time,
                        'status': 'pending'
                    }
                    # 存储回调队列
                    auth_callbacks[callback_id] = callback_queue

                # 超时检查线程（请求级临时线程，5分钟内自终止，无需登记 worker）
                def check_timeout():
                    while True:
                        current_time = int(time.time())
                        with auth_lock:
                            if callback_id in auth_tokens:
                                token_info = auth_tokens[callback_id]
                                if current_time >= token_info['timeout_time']:
                                    # 超时处理
                                    token_info['status'] = 'timeout'

                                    # 回调超时信息
                                    if callback_id in auth_callbacks:
                                        auth_callbacks[callback_id].put({
                                            'message': '验证超时',
                                            'status': False
                                        })
                                        del auth_callbacks[callback_id]

                                    # 删除令牌信息
                                    del auth_tokens[callback_id]

                                    # 不删除hopexauth.json中的令牌，而是标记为超时状态
                                    latest_auth_data = _load_hopexauth()
                                    if authcode in latest_auth_data:
                                        if isinstance(latest_auth_data[authcode], dict):
                                            latest_auth_data[authcode]['status'] = 'timeout'
                                            latest_auth_data[authcode]['timeout_time'] = current_time
                                            latest_auth_data[authcode]['query_count'] = 0
                                        else:
                                            # 旧格式转换为新格式
                                            latest_auth_data[authcode] = {
                                                'create_time': latest_auth_data[authcode],
                                                'status': 'timeout',
                                                'timeout_time': current_time,
                                                'query_count': 0
                                            }
                                        _save_hopexauth(latest_auth_data)

                                    logger.info(f"令牌 {authcode} 验证超时")
                                    break
                            else:
                                break

                        time.sleep(1)  # 每秒检查一次

                # 启动超时检查线程
                timeout_thread = threading.Thread(target=check_timeout, daemon=True)
                timeout_thread.start()

                logger.info(f"开始验证令牌 {authcode}，超时时间: {timeout_time}")

                # 等待验证结果
                start_time = time.time()
                while time.time() - start_time < 300:  # 最多等待5分钟
                    with auth_lock:
                        if callback_id in auth_tokens:
                            token_info = auth_tokens[callback_id]
                            if token_info['status'] == 'verified':
                                # 验证成功
                                result = {
                                    'message': '验证通过',
                                    'status': True,
                                    'userqq': token_info.get('user_qq', ''),
                                    'username': token_info.get('user_name', ''),
                                    'authtime': token_info.get('auth_time', int(time.time()))
                                }
                                callback_queue.put(result)

                                # 清理令牌信息
                                del auth_tokens[callback_id]
                                if callback_id in auth_callbacks:
                                    del auth_callbacks[callback_id]

                                # 不删除hopexauth.json中的令牌，而是标记为已验证状态
                                latest_auth_data = _load_hopexauth()
                                if authcode in latest_auth_data:
                                    if isinstance(latest_auth_data[authcode], dict):
                                        latest_auth_data[authcode]['status'] = 'verified'
                                        latest_auth_data[authcode]['user_qq'] = token_info.get('user_qq', '')
                                        latest_auth_data[authcode]['user_name'] = token_info.get('user_name', '')
                                        latest_auth_data[authcode]['auth_time'] = token_info.get('auth_time', int(time.time()))
                                        latest_auth_data[authcode]['query_count'] = 0
                                    else:
                                        # 旧格式转换为新格式
                                        latest_auth_data[authcode] = {
                                            'create_time': latest_auth_data[authcode],
                                            'status': 'verified',
                                            'user_qq': token_info.get('user_qq', ''),
                                            'user_name': token_info.get('user_name', ''),
                                            'auth_time': token_info.get('auth_time', int(time.time())),
                                            'query_count': 0
                                        }
                                    _save_hopexauth(latest_auth_data)

                                logger.info(f"令牌 {authcode} 验证成功")
                                break
                            elif token_info['status'] == 'timeout':
                                # 验证超时
                                callback_queue.put({
                                    'message': '验证超时',
                                    'status': False
                                })
                                break

                    time.sleep(0.1)  # 短暂休眠

                # 如果超时仍未完成验证
                if callback_queue.empty():
                    with auth_lock:
                        if callback_id in auth_tokens:
                            token_info = auth_tokens[callback_id]
                            token_info['status'] = 'timeout'

                            # 回调超时信息
                            if callback_id in auth_callbacks:
                                auth_callbacks[callback_id].put({
                                    'message': '验证超时',
                                    'status': False
                                })
                                del auth_callbacks[callback_id]

                            del auth_tokens[callback_id]

                            # 不删除hopexauth.json中的令牌，而是标记为超时状态
                            latest_auth_data = _load_hopexauth()
                            if authcode in latest_auth_data:
                                if isinstance(latest_auth_data[authcode], dict):
                                    latest_auth_data[authcode]['status'] = 'timeout'
                                    latest_auth_data[authcode]['timeout_time'] = int(time.time())
                                    latest_auth_data[authcode]['query_count'] = 0
                                else:
                                    # 旧格式转换为新格式
                                    latest_auth_data[authcode] = {
                                        'create_time': latest_auth_data[authcode],
                                        'status': 'timeout',
                                        'timeout_time': int(time.time()),
                                        'query_count': 0
                                    }
                                _save_hopexauth(latest_auth_data)

                    callback_queue.put({
                        'message': '验证超时',
                        'status': False
                    })

            except Exception as e:
                logger.error(f"验证令牌状态失败: {e}")
                callback_queue.put({'error': str(e)})

        try:
            # 获取请求数据
            data = request.get_json(silent=True)

            # 验证请求格式
            if not data or 'bbxxxxx' not in data or 'authcode' not in data:
                return jsonify({'error': 'Invalid request format'}), 400

            # 验证bbxxxxx内容
            if data.get('bbxxxxx') != 'hopexaaaaaaa':
                return jsonify({'error': 'Authentication failed'}), 403

            authcode = data.get('authcode')

            # 检查令牌是否存在
            auth_data = _load_hopexauth()
            if authcode not in auth_data:
                return jsonify({
                    'message': '令牌不存在',
                    'status': False
                }), 200

            # 创建回调队列并在新线程中处理请求（请求级临时线程，5分钟内自终止）
            callback_queue = queue.Queue()
            thread = threading.Thread(target=process_request, args=(data, callback_queue))
            thread.daemon = True
            thread.start()

            # 等待验证结果
            try:
                result = callback_queue.get(timeout=305)  # 等待5分钟+5秒缓冲
                return jsonify(result)
            except queue.Empty:
                return jsonify({
                    'message': '验证超时',
                    'status': False
                })

        except Exception as e:
            logger.error(f"处理vstatus请求失败: {e}")
            return jsonify({'error': str(e)}), 500

    # API路由 - 验证令牌用户信息查询（从 1.0.2 迁移）
    @app.route('/aad84a17-c28c-4d0c-8a92-dec13b707ba3/getauthuser', methods=['POST'])
    def get_auth_user():
        try:
            # 获取请求数据
            data = request.get_json(silent=True)

            # 验证请求格式
            if not data or 'bbxxxxx' not in data or 'authcode' not in data:
                return jsonify({'error': 'Invalid request format'}), 400

            # 验证bbxxxxx内容
            if data.get('bbxxxxx') != 'hopexaaaaaaa':
                return jsonify({'error': 'Authentication failed'}), 403

            authcode = data.get('authcode')

            # 检查令牌是否存在
            auth_data = _load_hopexauth()
            if authcode not in auth_data:
                return jsonify({
                    'message': '令牌不存在',
                    'status': False
                }), 200

            # 获取令牌信息
            token_info = auth_data[authcode]

            # 检查令牌状态
            if isinstance(token_info, dict):
                status = token_info.get('status', 'unknown')

                # 检查查询次数限制
                query_count = token_info.get('query_count', 0)
                if query_count >= 10:
                    # 超过10次查询，删除令牌数据
                    del auth_data[authcode]
                    _save_hopexauth(auth_data)

                    return jsonify({
                        'message': '令牌查询次数已达上限',
                        'status': False
                    }), 200

                # 增加查询次数
                token_info['query_count'] = query_count + 1
                auth_data[authcode] = token_info

                # 保存更新后的数据
                _save_hopexauth(auth_data)

                # 根据状态返回相应信息
                if status == 'verified':
                    # 验证成功状态
                    result = {
                        'message': '验证通过',
                        'status': True,
                        'userqq': token_info.get('user_qq', ''),
                        'username': token_info.get('user_name', ''),
                        'authtime': token_info.get('auth_time', 0),
                        'query_count': query_count + 1
                    }
                    return jsonify(result)
                elif status == 'timeout':
                    # 超时状态
                    result = {
                        'message': '验证超时',
                        'status': False,
                        'timeout_time': token_info.get('timeout_time', 0),
                        'query_count': query_count + 1
                    }
                    return jsonify(result)
                else:
                    # 其他状态（pending等）
                    result = {
                        'message': '令牌状态未知',
                        'status': False,
                        'query_count': query_count + 1
                    }
                    return jsonify(result)
            else:
                # 旧格式令牌（只有创建时间）
                result = {
                    'message': '令牌未完成验证',
                    'status': False,
                    'create_time': token_info
                }
                return jsonify(result)

        except Exception as e:
            logger.error(f"处理getauthuser请求失败: {e}")
            return jsonify({'error': str(e)}), 500

    # AI生成的HTML页面访问路由（从 1.0.2 迁移）
    @app.route('/aimakeweb/<qq>/<file_id>')
    def aimakeweb_page(qq, file_id):
        file_path = os.path.join(webui_dir, "aigc", "makeweb", qq, file_id, "index.html")
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            response = make_response(html_content)
            response.headers['Content-Type'] = 'text/html; charset=utf-8'
            return response
        else:
            return "文件不存在", 404

    # 屏蔽词列表页面路由（从 1.0.2 迁移）
    @app.route('/fklist')
    def fklist_page():
        # 获取群号参数
        group_id = request.args.get('gp', '')

        # 按群-账号映射定位账号，读取对应账号的屏蔽词配置
        bot_id = mjbconfig.get_bot_id_by_group(group_id) if group_id else None
        current_gpfk_configs = {}
        try:
            data = mjbconfig.load_module_config("gpfk_configs.json", bot_id=bot_id)
            if isinstance(data, dict):
                current_gpfk_configs = data
        except Exception as e:
            logger.error(f"加载屏蔽词配置失败: {e}")

        # 获取指定群的屏蔽词列表
        bad_words = []
        if group_id and group_id in current_gpfk_configs and "words" in current_gpfk_configs[group_id]:
            bad_words = current_gpfk_configs[group_id]["words"]

        # 生成HTML页面
        html_content = f'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>屏蔽词列表 - 群 {group_id}</title>
    <link rel="stylesheet" href="/css/style.css">
    <style>
        .container {{
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            margin-top: 20px;
        }}

        .header {{
            text-align: center;
            margin-bottom: 30px;
        }}

        .header h1 {{
            color: #333;
            margin-bottom: 10px;
        }}

        .group-info {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
        }}

        .search-box {{
            margin-bottom: 20px;
        }}

        .search-box input {{
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 16px;
        }}

        .word-list {{
            max-height: 400px;
            overflow-y: auto;
            border: 1px solid #ddd;
            border-radius: 5px;
            padding: 10px;
        }}

        .word-item {{
            padding: 8px 12px;
            border-bottom: 1px solid #eee;
            font-size: 14px;
        }}

        .word-item:last-child {{
            border-bottom: none;
        }}

        .word-item:hover {{
            background: #f5f5f5;
        }}

        .no-words {{
            text-align: center;
            color: #666;
            padding: 40px;
            font-style: italic;
        }}

        .highlight {{
            background: yellow;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>屏蔽词列表</h1>
            <div class="group-info">
                <strong>查询群号:</strong> {group_id if group_id else "未指定"}
            </div>
        </div>

        <div class="search-box">
            <input type="text" id="searchInput" placeholder="搜索屏蔽词..." oninput="filterWords()" onkeyup="filterWords()" onpaste="filterWords()" onchange="filterWords()">
        </div>

        <div class="word-list" id="wordList">
'''

        if bad_words:
            for word in bad_words:
                html_content += f'            <div class="word-item" data-word="{word}">{word}</div>\n'
        else:
            html_content += '            <div class="no-words">该群暂无屏蔽词</div>\n'

        html_content += '''
        </div>
    </div>

    <script>
        // 保存原始词列表
        let originalWords = [];

        function filterWords() {
            const input = document.getElementById('searchInput');
            const filter = input.value.toLowerCase().trim();
            const wordList = document.getElementById('wordList');

            // 如果没有原始词列表，初始化它
            if (originalWords.length === 0) {
                const items = wordList.getElementsByClassName('word-item');
                for (let i = 0; i < items.length; i++) {
                    originalWords.push({
                        element: items[i],
                        text: items[i].textContent
                    });
                }
            }

            // 清空当前列表
            wordList.innerHTML = '';

            let hasVisibleItems = false;

            // 过滤并显示匹配的词
            originalWords.forEach(item => {
                const word = item.text.toLowerCase();
                if (word.includes(filter)) {
                    const newItem = document.createElement('div');
                    newItem.className = 'word-item';

                    // 高亮匹配部分
                    if (filter) {
                        const regex = new RegExp(`(${filter})`, 'gi');
                        newItem.innerHTML = item.text.replace(regex, '<span class="highlight">$1</span>');
                    } else {
                        newItem.textContent = item.text;
                    }

                    wordList.appendChild(newItem);
                    hasVisibleItems = true;
                }
            });

            // 如果没有匹配项，显示提示
            if (!hasVisibleItems) {
                if (filter) {
                    wordList.innerHTML = '<div class="no-words">未找到匹配的屏蔽词</div>';
                } else {
                    // 如果没有搜索词，显示原始列表
                    originalWords.forEach(item => {
                        const newItem = document.createElement('div');
                        newItem.className = 'word-item';
                        newItem.textContent = item.text;
                        wordList.appendChild(newItem);
                    });

                    // 如果没有词，显示提示
                    if (originalWords.length === 0) {
                        wordList.innerHTML = '<div class="no-words">该群暂无屏蔽词</div>';
                    }
                }
            }
        }

        // 页面加载完成后初始化
        document.addEventListener('DOMContentLoaded', function() {
            filterWords(); // 初始化显示

            // 添加防抖功能，避免频繁搜索
            const searchInput = document.getElementById('searchInput');
            let timeoutId;

            searchInput.addEventListener('input', function() {
                clearTimeout(timeoutId);
                timeoutId = setTimeout(filterWords, 300); // 300ms防抖
            });
        });
    </script>
</body>
</html>
'''

        return html_content

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
