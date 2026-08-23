# -*- coding: utf-8 -*-
import os
import platform
import psutil

from bin import logger, mjbconfig


def generate():
    """生成状态信息字符串"""
    try:
        try:
            system_cpu_percent = psutil.cpu_percent(interval=1)
            memory_info = psutil.virtual_memory()
            system_memory_percent = memory_info.percent

            current_process = psutil.Process(os.getpid())
            current_process.cpu_percent(interval=0.1)  # 初始采样
            process_cpu_percent = current_process.cpu_percent(interval=1) / psutil.cpu_count()
            process_memory_mb = current_process.memory_info().rss / (1024 * 1024)
            process_memory_percent = current_process.memory_percent()

            cpu_display = f"{process_cpu_percent:.1f}/{system_cpu_percent}%"
            memory_display = f"{process_memory_percent:.1f}/{system_memory_percent}%"
        except ImportError:
            cpu_display = "未知/未知"
            memory_display = "未知/未知"
            logger.warning("未安装psutil库，无法获取CPU和内存占用信息")

        system_info = platform.system() + " " + platform.release()
        botname = mjbconfig.get_botname()
        version = mjbconfig.get_mjbcver_raw()

        status_message = f"{botname}\n\n"
        status_message += f"📀内核版本:{version}\n\n"
        status_message += f"💻当前系统:{system_info}\n\n"
        status_message += f"🧠CPU占用:{cpu_display}\n\n"
        status_message += f"📚内存占用:{memory_display}"
        return status_message
    except Exception as e:
        logger.error(f"获取系统状态失败: {e}")
        return "获取系统状态失败，请稍后再试"
