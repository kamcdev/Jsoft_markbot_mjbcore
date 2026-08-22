# -*- coding: utf-8 -*-
import asyncio
import threading

from fastapi import FastAPI, Request
import uvicorn

from bin import logger, mjbconfig, message, worker

# 按账号缓存的 FastAPI 应用：bot_id(str) -> app；None 键保留给旧默认（无账号场景）
_apps = {}

# 各 app 的事件循环引用（由 webhook 端点首次执行时设置）：bot_id -> loop
_loops = {}

# Webhook uvicorn 服务器登记表：bot_id -> {"server":.., "port":.., "thread":..}
_webhook_servers = {}


def create_app(bot_id=None):
    """创建绑定账号的 FastAPI 应用并注册 Webhook 端点（按账号缓存）"""
    key = str(bot_id) if bot_id is not None else None
    if key in _apps:
        return _apps[key]
    app = FastAPI()

    @app.post("/")
    async def root(request: Request):
        # 进入账号上下文，保证端点内 mjbconfig 无参调用路由到本账号
        if bot_id is not None:
            mjbconfig.set_current_bot_id(bot_id)
        try:
            # 记录该 app 的事件循环（供 run_on_main_thread 使用）
            _loops[bot_id] = asyncio.get_running_loop()

            # 检查配置文件是否被修改，热重载
            if mjbconfig.check_modified(bot_id):
                mjbconfig.reload(bot_id)

            data = await request.json()
            logger.debug(f"收到上报消息: {data.get('post_type', '未知')}")

            # 交由 worker 线程处理，避免阻塞事件循环（显式携带 bot_id）
            worker.submit(_safe_handle, data, bot_id)
            return {"status": "ok"}
        finally:
            if bot_id is not None:
                mjbconfig.clear_current_bot_id()

    _apps[key] = app
    return app


def run_on_main_thread(fn):
    """将函数提交到主线程（事件循环线程）同步执行，阻塞调用线程直到完成

    用于 pywebview 等必须在主线程运行的库。
    注意：fn 在事件循环线程中同步执行，若 fn 阻塞会阻塞整个事件循环
    （与 1.0.2 中 webview.start() 直接在 async webhook 中调用的行为一致）。
    """
    if not _loops:
        raise RuntimeError("主线程事件循环未就绪")
    result = [None]
    error = [None]
    done = threading.Event()

    def _run():
        try:
            result[0] = fn()
        except Exception as e:
            error[0] = e
        finally:
            done.set()

    # 取第一个 app 的事件循环（多账号共用主线程语义）
    loop = next(iter(_loops.values()))
    loop.call_soon_threadsafe(_run)
    done.wait()
    if error[0]:
        raise error[0]
    return result[0]


def get_app():
    """返回默认账号（第一个账号）的 app，向后兼容"""
    return create_app(mjbconfig.get_default_bot_id())


def _safe_handle(data, bot_id=None):
    """在线程池中安全执行消息处理

    账号上下文由 worker.submit 的 _run 统一管理（提交时快照调用方账号，
    任务运行时恢复，finally 还原）。本函数不再 set/clear 线程上下文，
    避免与 worker.submit 的上下文管理冲突或在异步任务启动前过早清除。
    bot_id 参数保留用于日志/兼容，不再用于设置上下文。
    """
    try:
        message.handle_event(data)
    except Exception as e:
        logger.error(f"消息处理失败: {e}")


def _serve(server, bot_id):
    """在 daemon 线程中运行 uvicorn 服务器"""
    try:
        server.run()
    except Exception as e:
        logger.error(f"账号{bot_id} Webhook 服务运行失败: {e}")


def _start_server(bot_id, port):
    """为单个账号启动 uvicorn 服务器线程并登记到 _webhook_servers"""
    app = create_app(bot_id)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    key = str(bot_id) if bot_id is not None else None
    thread = threading.Thread(
        target=_serve, args=(server, bot_id), name=f"webhook-{key}", daemon=True
    )
    thread.start()
    _webhook_servers[key] = {"server": server, "port": port, "thread": thread}
    if bot_id is None:
        logger.info(f"Webhook 服务启动在端口 {port}")
    else:
        logger.info(f"账号{bot_id} Webhook 服务启动在端口 {port}")


def run(port=None):
    """启动 uvicorn 服务（不阻塞，启动后返回）

    port 显式给定时为默认账号(get_default_bot_id)起单服务器；
    port 为 None 时遍历 get_account_list() 为每个账号起独立 uvicorn 服务器线程，
    端口 = mjbconfig.get_webhook_port(bot_id)；无账号时回退单服务器端口 9762。
    """
    if port is not None:
        _start_server(mjbconfig.get_default_bot_id(), port)
        return

    accounts = mjbconfig.get_account_list()
    if not accounts:
        # 无账号：回退单服务器端口 9762
        _start_server(None, 9762)
        return

    for bot_id in accounts:
        _start_server(bot_id, mjbconfig.get_webhook_port(bot_id))


def wait():
    """阻塞主线程：等待所有 Webhook 服务器线程结束，支持响应 Ctrl+C

    run() 非阻塞返回后，调用本函数保持主线程存活（被 join 的线程为 daemon，
    主线程退出即整体退出，因此 wait 直至服务器线程自然结束）。
    使用带超时的循环 join，避免无超时 join 阻塞信号处理，允许 Ctrl+C 中断。
    """
    try:
        while True:
            # 任一服务器线程仍在运行则继续等待
            any_alive = False
            for key, entry in list(_webhook_servers.items()):
                thread = entry.get("thread")
                if thread is not None and thread is not threading.current_thread() and thread.is_alive():
                    any_alive = True
                    thread.join(timeout=1)  # 超时1秒，允许中断
            if not any_alive:
                break
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在停止所有 Webhook 服务…")
        stop()


def stop():
    """停止所有 Webhook 服务器并清理登记表"""
    for entry in list(_webhook_servers.values()):
        server = entry.get("server")
        if server is not None:
            server.should_exit = True
    for key, entry in list(_webhook_servers.items()):
        thread = entry.get("thread")
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                logger.warning(f"账号{key} Webhook 服务器线程在 5s 内未退出")
        logger.info(f"账号{key} Webhook 服务已停止")
    _webhook_servers.clear()
