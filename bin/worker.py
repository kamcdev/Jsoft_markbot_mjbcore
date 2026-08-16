# -*- coding: utf-8 -*-
import ctypes
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from bin import logger

# 命令执行线程池
_executor = None
_executor_lock = threading.Lock()

# 后台线程登记表：name -> {"thread": t, "stop_attr": str|None}
_background_threads = {}
_bg_lock = threading.Lock()

# 需在主线程执行的任务列表
_main_tasks = []


def _ensure_executor(max_workers=16):
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mjb-worker")
    return _executor


def submit(fn, *args, **kwargs):
    """提交任务到线程池执行，返回 Future"""
    return _ensure_executor().submit(fn, *args, **kwargs)


def start_background(name, target, args=(), kwargs=None, daemon=True, stop_attr=None, stop_event=None):
    """启动并登记一个后台线程

    Args:
        name: 线程名称（唯一标识，用于管理）
        target: 线程目标函数
        stop_attr: 若 target 是对象，停止其的方法名（如 "stop"），用于优雅退出
        stop_event: threading.Event，会自动注入到 target 的 kwargs 中（参数名 stop_event），
                    用于通知 while 循环退出
    Returns:
        threading.Thread
    """
    if kwargs is None:
        kwargs = {}
    if stop_event is not None:
        kwargs['stop_event'] = stop_event
    t = threading.Thread(target=target, args=args, kwargs=kwargs, name=name, daemon=daemon)
    with _bg_lock:
        _background_threads[name] = {
            "thread": t,
            "stop_attr": stop_attr,
            "target": target,
            "stop_event": stop_event,
        }
    t.start()
    logger.info(f"后台线程已启动: {name}")
    return t


def register_thread(name, thread, stop_attr=None):
    """登记一个已存在/已启动的线程对象，便于统一回收"""
    with _bg_lock:
        _background_threads[name] = {"thread": thread, "stop_attr": stop_attr, "target": None}
    logger.info(f"已登记后台线程: {name}")


def request_main_thread(fn):
    """注册一个需在主线程执行的任务（如桌面 GUI 初始化等占用主线程的场景）"""
    _main_tasks.append(fn)


def run_main_tasks():
    """由编排层在阻塞主线程前调用，依次执行需占用主线程的任务"""
    while _main_tasks:
        fn = _main_tasks.pop(0)
        try:
            logger.info(f"执行主线程任务: {getattr(fn, '__name__', fn)}")
            fn()
        except Exception as e:
            logger.error(f"主线程任务执行失败: {e}")


def stop_background(name):
    """停止指定后台线程（先尝试 stop_attr，再尝试 stop_event）"""
    with _bg_lock:
        entry = _background_threads.get(name)
    if not entry:
        return False
    target = entry.get("target")
    stop_attr = entry.get("stop_attr")
    # 1. 尝试调用 stop_attr 方法
    if target and stop_attr and hasattr(target, stop_attr):
        try:
            getattr(target, stop_attr)()
            logger.info(f"已请求后台线程停止: {name}")
            return True
        except Exception as e:
            logger.error(f"停止后台线程 {name} 失败: {e}")
    # 2. 尝试设置 stop_event
    stop_event = entry.get("stop_event")
    if stop_event is not None:
        try:
            stop_event.set()
            logger.info(f"已触发后台线程停止事件: {name}")
            return True
        except Exception as e:
            logger.error(f"触发停止事件失败 {name}: {e}")
    return False


def _force_stop_thread(t, retries=3, interval=0.5):
    tid = t.ident
    if tid is None or t is threading.current_thread():
        logger.error(f"无法强制终止线程 {t.name}: 无效线程ID或为当前线程")
        return False
    for _ in range(retries):
        if not t.is_alive():
            return True
        try:
            res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(tid), ctypes.py_object(SystemExit))
            if res == 0:
                logger.error(f"强制终止线程 {t.name} 失败: 无效线程ID {tid}")
                return False
            if res > 1:
                # 理论上不会发生；若发生则撤销注入，避免线程被多次打断
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)
                logger.error(f"强制终止线程 {t.name} 异常: 注入异常计数 {res}")
                return False
        except Exception as e:
            logger.error(f"强制终止线程 {t.name} 出错: {e}")
            return False
        time.sleep(interval)
    return not t.is_alive()


def stop_all_background(wait=True, timeout=5):
    """停止所有后台线程，等待退出后清理登记表

    用于 reload_all：确保旧线程彻底退出后再重新加载模块，避免线程重复创建。
    对无停止机制的线程（如 WebUI Flask）仅记录警告，不阻塞。
    join 超时仍未退出的线程会被强制终止（注入 SystemExit）。

    Args:
        wait: 是否 join 等待线程结束
        timeout: 每个线程 join 超时秒数，超时后强制终止
    """
    with _bg_lock:
        entries = dict(_background_threads)

    # 请求所有线程停止
    for name, entry in entries.items():
        target = entry.get("target")
        stop_attr = entry.get("stop_attr")
        stop_event = entry.get("stop_event")
        if target and stop_attr and hasattr(target, stop_attr):
            try:
                getattr(target, stop_attr)()
            except Exception as e:
                logger.error(f"停止后台线程 {name} 失败: {e}")
        elif stop_event is not None:
            try:
                stop_event.set()
            except Exception as e:
                logger.error(f"触发停止事件失败 {name}: {e}")
        else:
            logger.warning(f"后台线程 {name} 无停止机制，跳过")

    # 等待线程结束
    if wait:
        for name, entry in entries.items():
            t = entry.get("thread")
            if t and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=timeout)
                if t.is_alive():
                    logger.warning(f"后台线程 {name} 在 {timeout}s 后仍未结束，尝试强制关闭")
                    if _force_stop_thread(t):
                        logger.info(f"后台线程 {name} 已被强制关闭")
                    else:
                        logger.error(f"后台线程 {name} 强制关闭失败，仍存活")
                else:
                    logger.info(f"后台线程 {name} 已退出")

    # 清理登记表
    with _bg_lock:
        _background_threads.clear()
    logger.info(f"已停止并清理 {len(entries)} 个后台线程登记")


def unregister_thread(name):
    """从登记表中移除线程（用于临时线程正常结束后自行清理）"""
    with _bg_lock:
        if name in _background_threads:
            del _background_threads[name]
            logger.info(f"已注销后台线程: {name}")


def get_status():
    """获取后台线程状态"""
    with _bg_lock:
        return {
            name: {"alive": entry["thread"].is_alive(), "daemon": entry["thread"].daemon}
            for name, entry in _background_threads.items()
        }


def shutdown(wait=False):
    """退出清理：停止后台线程并关闭线程池"""
    # 停止所有后台线程
    stop_all_background(wait=wait, timeout=3)

    # 关闭线程池
    global _executor
    with _executor_lock:
        if _executor is not None:
            try:
                _executor.shutdown(wait=wait, cancel_futures=True)
                logger.info("线程池已关闭")
            except Exception as e:
                logger.error(f"关闭线程池失败: {e}")
            _executor = None
