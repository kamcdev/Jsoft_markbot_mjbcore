# -*- coding: utf-8 -*-
import os
import sys
import time
import signal
import atexit
import importlib
import subprocess
import traceback

from bin import logger, mjbconfig, send, worker, message, mjbc, socket, webmain


# 模块根目录与包名（用于 importlib 导入 modules.<name>）
_MODULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules")
_MODULES_PKG = "modules"

# 退出流程幂等保护
_cleanup_done = False


def init():
    """初始化内核：加载配置并打印基础信息，同时注册退出钩子。"""
    mjbconfig.load()

    botid = mjbconfig.get_botid()
    botname = mjbconfig.get_botname()
    version = mjbconfig.get_version()

    logger.info("=" * 50)
    logger.info(f"硫酸钠BOT 内核启动 | 内核版本: {version}")
    logger.info(f"机器人QQ: {botid} | 名称: {botname}")
    logger.info("=" * 50)

    _register_exit_hooks()


def modcfg(module_name, commands):
    """记录模块加载信息到日志。"""
    if commands:
        logger.info(f"模块 [{module_name}] 已注册命令: {', '.join(commands)}")
    else:
        logger.info(f"模块 [{module_name}] 已加载（无命令）")


# 自检失败项收集（load_modules 开头会 clear，reload_all 复用同一流程）
_selfcheck_failures = []


def _oneline(msg):
    """将多行错误信息压缩为一行（换行替换为空格）。"""
    return msg.replace("\n", " ").replace("\r", " ").strip()


def _syntax_check(module_path):
    """使用启动 bot 的 Python 解释器对模块做语法检查。

    返回 (passed, error_msg)：
    - 成功：(True, "")
    - 失败：(False, stderr.strip())
    - 子进程异常：(False, str(异常))
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", module_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        return (False, str(e))

    if result.returncode == 0:
        return (True, "")
    return (False, result.stderr.strip())


def load_modules():
    """扫描 modules/*.py，动态导入并注册命令。

    - 排除 __init__.py 与 config/ 目录
    - 用 importlib.import_module 导入每个模块
    - 收集模块中所有 cmd_ 开头的可调用函数
    - 对每个 cmd_xxx，同时注册 "xxx" 和 "cmd_xxx" 两个名称（兼容两种命令命名）
    - 调用模块 init()（若存在），用于注册拦截器 / 启动后台线程
    """
    if not os.path.isdir(_MODULES_DIR):
        logger.warning(f"模块目录不存在: {_MODULES_DIR}")
        return

    # 清空自检失败项（reload 时也会重置）
    _selfcheck_failures.clear()

    loaded = 0
    failed = 0

    for fname in sorted(os.listdir(_MODULES_DIR)):
        # 仅处理 .py 文件，自动排除 config/ 目录与子目录
        if not fname.endswith(".py"):
            continue
        if fname == "__init__.py":
            continue

        mod_name = fname[:-3]
        full_name = f"{_MODULES_PKG}.{mod_name}"

        # 语法检查：在 import 之前用子进程校验模块语法
        module_path = os.path.join(_MODULES_DIR, fname)
        passed, error_msg = _syntax_check(module_path)
        if not passed:
            logger.error(f"模块 {mod_name} 语法检查失败:\n{error_msg}")
            _selfcheck_failures.append(f"模块{mod_name}语法检查失败: {_oneline(error_msg)}")
            failed += 1
            continue

        try:
            mod = importlib.import_module(full_name)
        except Exception:
            logger.error(f"加载模块失败: {mod_name}\n{traceback.format_exc()}")
            _selfcheck_failures.append(f"模块{mod_name}加载失败: {_oneline(traceback.format_exc())}")
            failed += 1
            continue

        # 收集 cmd_ 开头的可调用对象，同时注册两种命名
        cmd_names = []
        reg_map = {}
        for attr in dir(mod):
            if not attr.startswith("cmd_"):
                continue
            fn = getattr(mod, attr, None)
            if not callable(fn):
                continue
            short_name = attr[4:]  # 去掉 "cmd_" 前缀
            # 兼容 group.json 中 "ncc": "ncc" 与 "cmd_ai_deepseek": "cmd_ai_deepseek"
            reg_map[short_name] = fn
            reg_map[attr] = fn
            cmd_names.append(short_name)

        if reg_map:
            mjbc.register_commands(reg_map)

        # 读取 modcfg().autoreg 并合并到运行时命令注册表
        # （模块自带默认注册，group.json 已有项优先）
        try:
            mod_meta = mod.modcfg() if callable(getattr(mod, "modcfg", None)) else {}
        except Exception:
            mod_meta = {}
        autoreg = mod_meta.get("autoreg") if isinstance(mod_meta, dict) else None
        if autoreg:
            mjbconfig.apply_module_autoreg(autoreg, module_name=mod_name)

        modcfg(mod_name, sorted(cmd_names))

        # 调用模块 init()（注册拦截器 / 启动后台线程）
        init_fn = getattr(mod, "init", None)
        if callable(init_fn):
            try:
                init_fn()
                logger.debug(f"模块 [{mod_name}] init() 已执行")
            except Exception:
                logger.error(f"模块 [{mod_name}] init() 失败:\n{traceback.format_exc()}")

        loaded += 1

    # 命令功能函数校验：检查 commands_map 中每条命令的函数是否已注册
    commands_map = mjbconfig.get_commands_map()
    registered_funcs = mjbc.get_command_functions()
    invalid_triggers = []
    for trigger, func_name in list(commands_map.items()):
        if func_name not in registered_funcs:
            logger.warning(f"命令 '{trigger}' 指向的功能函数 '{func_name}' 未提供，已移除该命令")
            _selfcheck_failures.append(f"命令{trigger}指向的功能函数{func_name}未提供")
            invalid_triggers.append(trigger)
    for trigger in invalid_triggers:
        del commands_map[trigger]

    logger.info(f"模块加载完成: 成功 {loaded} 个, 失败 {failed} 个")


def cleanup(*_args):
    """退出清理：停止线程池与后台线程，保存配置（幂等）。"""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    logger.info("正在执行退出清理...")
    try:
        worker.shutdown(wait=False)
    except Exception:
        logger.error(f"停止线程池失败:\n{traceback.format_exc()}")
    try:
        mjbconfig.save()
    except Exception:
        logger.error(f"保存配置失败:\n{traceback.format_exc()}")
    logger.info("退出清理完成")


def reload_all():
    """重载模块与配置但不重启程序（供 mjb.reload 命令调用）

    流程：
    1. 停止所有 worker 后台线程并等待退出（保留线程池）
    2. 清理 message 拦截器/通知处理器
    3. 清理已注册的模块命令（保留内置命令）
    4. 卸载已导入的 modules.* 模块
    5. 重新加载配置
    6. 重新扫描加载模块
    7. 重新自检通知
    """
    import bin.message as _message

    # 1. 停止所有后台线程并等待退出（确保旧线程彻底销毁后再创建新线程）
    logger.info("reload: 停止后台线程...")
    worker.stop_all_background(wait=True, timeout=5)

    # 2. 清理拦截器与通知处理器
    logger.info("reload: 清理拦截器与通知处理器...")
    _message.clear_handlers()

    # 3. 清理已注册的模块命令
    logger.info("reload: 清理模块命令注册...")
    mjbc.clear_commands()

    # 4. 卸载已导入的 modules.* 模块
    logger.info("reload: 卸载已导入模块...")
    mods_to_remove = [k for k in sys.modules if k.startswith(f"{_MODULES_PKG}.")]
    for k in mods_to_remove:
        del sys.modules[k]

    # 5. 重新加载配置
    logger.info("reload: 重新加载配置...")
    mjbconfig.load()
    mjbconfig.reload()

    # 6. 重新加载模块
    logger.info("reload: 重新加载模块...")
    load_modules()

    # 7. 重新自检
    logger.info("reload: 重新自检...")
    _send_startup_notice()

    logger.info("reload: 重载完成")


def _register_exit_hooks():
    """注册退出钩子：SIGINT / SIGTERM / atexit。"""
    def _sig_handler(signum, frame):
        logger.info(f"收到信号 {signum}，准备退出")
        cleanup()
        sys.exit(0)

    # signal.signal 仅能在主线程注册，失败时静默跳过
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _sig_handler)
        except (ValueError, OSError):
            pass

    atexit.register(cleanup)


def start():
    """启动 WebUI（daemon 线程）与 Webhook 服务（主线程阻塞）。"""
    webmain.start_webui_thread()
    logger.info("WebUI 服务已启动（daemon 线程）")

    # 启动自检通知（向主群发送连接/自检提示，与 1.0.2 行为一致）
    _send_startup_notice()

    # 启用错误转发到主群（捕获 ERROR 日志与未捕获异常）
    logger.setup_error_notify()

    # 注册 reload 回调（供 mjb.reload 命令调用）
    mjbc.set_reload_callback(reload_all)

    logger.info("Webhook 服务启动，监听端口 9762")
    socket.run(port=9762)


def _send_startup_notice():
    """启动时向主群发送自检通知。

    与 1.0.2 行为一致：发送"正在连接..."/"正在自检多个模块..."，等待 3 秒，
    再发送"自检成功，当前监听QQ号: ..."。
    """
    target_group = mjbconfig.get_target_group()
    if not target_group or target_group == "0":
        logger.info("未设置目标群号，跳过自检消息发送")
        return

    try:
        send.group(target_group, "正在连接...")
        send.group(target_group, "正在自检多个模块...")
        if _selfcheck_failures:
            notice = f"存在自检失败项：{len(_selfcheck_failures)}项\n" + "\n".join(_selfcheck_failures)
            send.group(target_group, notice)
            logger.warning(f"已在群{target_group}发送自检结果（{len(_selfcheck_failures)}项失败）")
        else:
            send.group(target_group, "自检成功")
            logger.info(f"已在群{target_group}发送自检成功消息")
    except Exception as e:
        logger.error(f"发送自检消息失败: {e}")


if __name__ == "__main__":
    init()
    load_modules()
    start()
