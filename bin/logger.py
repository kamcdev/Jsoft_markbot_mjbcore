# -*- coding: utf-8 -*-
import logging
import os
import sys
import threading
import traceback
from datetime import datetime

# ---- 颜色定义（ANSI 转义码）----
_RESET = "\x1b[0m"
_NAME_COLOR = "\x1b[94m"  # 来源颜色：亮蓝
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
    """带颜色的日志格式器：时间 级别 [来源] 消息"""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record):
        # 时间：默认白色
        try:
            dt = datetime.fromtimestamp(record.created)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            time_str = record.asctime

        # 级别：按级别着色
        level_color = _COLORS.get(record.levelno, _RESET)
        level_str = f"{record.levelname:<7}"

        # 来源：统一着色
        name_str = f"[{record.name}]"

        # 消息：默认白色，附带异常堆栈/栈信息
        msg = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            msg = f"{msg}\n{record.exc_text}"
        if record.stack_info:
            msg = f"{msg}\n{self.formatStack(record.stack_info)}"

        return (f"{time_str} {level_color}{level_str}{_RESET} "
                f"{_NAME_COLOR}{name_str}{_RESET} {msg}")


def _detect_source_name():
    """向上遍历调用栈，检测日志调用来源"""
    try:
        _logging_dir = os.path.dirname(os.path.abspath(logging.__file__))
        _self_file = os.path.abspath(__file__)
        frame = sys._getframe(1)
        while frame:
            filename = frame.f_code.co_filename
            abs_path = os.path.abspath(filename)
            frame = frame.f_back
            # 跳过本模块与 logging 库内部帧
            if abs_path == _self_file or abs_path.startswith(_logging_dir):
                continue
            # 首个外部调用帧：位于 modules/ 目录则作为插件名
            if os.sep + "modules" + os.sep in abs_path:
                base = os.path.basename(filename)
                if base.endswith(".py"):
                    return base[:-3]
            return None
        return None
    except Exception:
        return None


class _SourceNameFilter(logging.Filter):
    """动态设置日志来源：插件调用显示插件名，核心调用保持 mjbcore"""

    def filter(self, record):
        source = _detect_source_name()
        if source:
            record.name = source
        return True


def _build_logger():
    lg = logging.getLogger("mjbcore")
    if lg.handlers:
        # 重新加载场景：补挂来源过滤器，避免重复
        if not any(isinstance(f, _SourceNameFilter) for f in lg.filters):
            lg.addFilter(_SourceNameFilter())
        return lg
    lg.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_ColorFormatter())
    lg.addHandler(handler)
    lg.propagate = False
    lg.addFilter(_SourceNameFilter())
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
    """全视系统：记录命令执行日志到 usage_records.txt"""
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
