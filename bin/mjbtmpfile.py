# -*- coding: utf-8 -*-
import os
import time
import tempfile

from bin import logger, worker


def create(suffix="", prefix="mjb_", dir=None):
    """创建一个临时文件并返回其路径（已创建并可写入）"""
    try:
        fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=dir)
        os.close(fd)
        return path
    except Exception as e:
        logger.error(f"创建临时文件失败: {e}")
        return None


def cleanup_now(path, retries=3):
    """立即删除文件，失败时重试若干次"""
    if not path or not os.path.exists(path):
        return
    for i in range(retries):
        try:
            os.remove(path)
            logger.debug(f"已清理临时文件: {path}")
            return
        except Exception as e:
            logger.debug(f"清理临时文件失败({i + 1}/{retries}): {path} - {e}")
            time.sleep(0.5)
    logger.warning(f"清理临时文件最终失败: {path}")


def cleanup(path, delay=5, retries=3):
    """延迟删除文件（提交到 worker 线程池等待 delay 秒后删除），确保发送完成

    delay 默认 5 秒（短时一次性任务），提交到 worker 线程池统一管理，
    避免裸 threading.Thread 散落创建。
    """
    if not path:
        return

    def _do():
        time.sleep(delay)
        cleanup_now(path, retries=retries)

    worker.submit(_do)
