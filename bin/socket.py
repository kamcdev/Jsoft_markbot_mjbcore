# -*- coding: utf-8 -*-
import asyncio
import threading

from fastapi import FastAPI, Request
import uvicorn

from bin import logger, mjbconfig, message, worker

_app = None

# 主线程事件循环引用（由 webhook 端点首次执行时设置）
_loop = None


def create_app():
    """创建 FastAPI 应用并注册 Webhook 端点"""
    global _app
    if _app is not None:
        return _app
    app = FastAPI()

    @app.post("/")
    async def root(request: Request):
        global _loop
        if _loop is None:
            _loop = asyncio.get_running_loop()

        # 检查配置文件是否被修改，热重载
        if mjbconfig.check_modified():
            mjbconfig.reload()

        data = await request.json()
        logger.debug(f"收到上报消息: {data.get('post_type', '未知')}")

        # 交由 worker 线程处理，避免阻塞事件循环
        worker.submit(_safe_handle, data)
        return {"status": "ok"}

    _app = app
    return app


def run_on_main_thread(fn):
    """将函数提交到主线程（事件循环线程）同步执行，阻塞调用线程直到完成

    用于 pywebview 等必须在主线程运行的库。
    注意：fn 在事件循环线程中同步执行，若 fn 阻塞会阻塞整个事件循环
    （与 1.0.2 中 webview.start() 直接在 async webhook 中调用的行为一致）。
    """
    if _loop is None:
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

    _loop.call_soon_threadsafe(_run)
    done.wait()
    if error[0]:
        raise error[0]
    return result[0]


def get_app():
    if _app is None:
        create_app()
    return _app


def _safe_handle(data):
    """在线程池中安全执行消息处理"""
    try:
        message.handle_event(data)
    except Exception as e:
        logger.error(f"消息处理失败: {e}")


def run(port=None):
    """启动 uvicorn 服务（阻塞主线程）

    port 为 None 时从 mjbconfig 读取 webhook_port（group.json 配置）。
    """
    if port is None:
        port = mjbconfig.get_webhook_port()
    app = get_app()
    logger.info(f"Webhook 服务启动在端口 {port}")
    try:
        uvicorn.run(app, port=port)
    except Exception as e:
        logger.error(f"启动 Web 服务失败: {e}")
