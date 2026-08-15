# -*- coding: utf-8 -*-
import logging
import os
import sys
import threading
import traceback
from datetime import datetime

# ---- 颜色定义（ANSI 转义码）----
_RESET = "\x1b[0m"
_COLORS = {
    logging.DEBUG: "\x1b[36m",      # 青色
    logging.INFO: "\x1b[32m",       # 绿色
    logging.WARNING: "\x1b[33m",    # 黄色
    logging.ERROR: "\x1b[31m",      # 红色
    logging.CRITICAL: "\x1b[35m",   # 紫色
}

# Windows 终端适配：尝试启用 colorama，否则使用 Windows 10+ 原生 ANSI 支持
try:
    import colorama
    colorama.just_fix_windows_console()
except Exception:
    # 若 colorama 不可用，尝试启用 Windows ANSI 处理
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass


class _ColorFormatter(logging.Formatter):
    """带颜色的日志格式器：时间 级别 [模块] 消息"""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record):
        color = _COLORS.get(record.levelno, "")
        original = super().format(record)
        if color:
            return f"{color}{original}{_RESET}"
        return original


def _build_logger():
    lg = logging.getLogger("mjbcore")
    if lg.handlers:
        return lg
    lg.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_ColorFormatter())
    lg.addHandler(handler)
    lg.propagate = False
    return lg


logger = _build_logger()


def get_logger(name="mjbcore"):
    """获取子 logger，便于按模块区分日志来源"""
    return logging.getLogger(name)


# 标准日志方法
debug = logger.debug
info = logger.info
warning = logger.warning
error = logger.error
critical = logger.critical


def supereye_log_command(group_id, user_id, command_name, command_args, permission_level):
    """全视系统：记录命令执行日志到 usage_records.txt

    与原 supereye_log_command 行为一致：一行记录，两个记录之间空一行。
    """
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        args_str = " ".join(command_args) if command_args else ""
        record = (f"[{current_time}] 用户: {user_id} | 群: {group_id} | "
                  f"命令: {command_name} {args_str} | 权限: {permission_level}")
        with open("usage_records.txt", "a", encoding="utf-8") as f:
            f.write(record + "\n\n")
        info(f"全视系统记录: {record}")
    except Exception as e:
        error(f"全视系统记录失败: {e}")


# ---- 错误转发到主群 ----
_notify_enabled = False
_sending = False  # 防止发送失败的 error 再次触发转发导致递归


class _GroupErrorHandler(logging.Handler):
    """将 ERROR 及以上级别日志转发到主群"""

    def emit(self, record):
        global _sending
        if not _notify_enabled or _sending:
            return
        try:
            # 延迟导入避免循环依赖（send 依赖 logger）
            from bin import send as _send, mjbconfig as _cfg
            target_group = _cfg.get_target_group()
            if not target_group or target_group == "0":
                return

            msg = self.format(record)
            # 截断过长消息（QQ 单条消息限制）
            if len(msg) > 1500:
                msg = msg[:1500] + "\n...(内容过长已截断)"

            _sending = True
            try:
                _send.group(target_group, f"[错误告警]\n{msg}")
            finally:
                _sending = False
        except Exception:
            # 转发失败静默处理，避免递归
            pass


def _uncaught_exception_handler(exc_type, exc_value, exc_tb):
    """全局未捕获异常钩子：打印并转发到主群"""
    # 先走默认处理（打印到控制台）
    sys.__excepthook__(exc_type, exc_value, exc_tb)
    if issubclass(exc_type, KeyboardInterrupt):
        return
    _forward_traceback(exc_type, exc_value, exc_tb)


def _forward_traceback(exc_type, exc_value, exc_tb):
    """将异常 traceback 转发到主群"""
    global _sending
    if not _notify_enabled or _sending:
        return
    try:
        from bin import send as _send, mjbconfig as _cfg
        target_group = _cfg.get_target_group()
        if not target_group or target_group == "0":
            return

        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        if len(tb_text) > 1500:
            tb_text = tb_text[:1500] + "\n...(内容过长已截断)"

        _sending = True
        try:
            _send.group(target_group, f"[未捕获异常]\n{tb_text}")
        finally:
            _sending = False
    except Exception:
        pass


def setup_error_notify():
    """启用错误转发到主群

    在编排层启动 Webhook 后调用（确保 send/mjbconfig 已就绪）。
    - 注册 _GroupErrorHandler 到 mjbcore logger（捕获 ERROR 及以上）
    - 替换 sys.excepthook 捕获未处理异常
    """
    global _notify_enabled
    if _notify_enabled:
        return
    _notify_enabled = True

    handler = _GroupErrorHandler()
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)

    sys.excepthook = _uncaught_exception_handler
    info("错误转发到主群已启用")
