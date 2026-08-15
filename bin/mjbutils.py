# -*- coding: utf-8 -*-
import re

from bin import logger


def extract_qq_from_at(at_str):
    """从 CQ:at 格式字符串中提取 QQ 号

    Args:
        at_str: 如 [CQ:at,qq=123456,type=all]
    Returns:
        提取出的 QQ 号字符串；非 CQ:at 格式则返回原字符串
    """
    match = re.search(r'\[CQ:at,qq=(\d+)[^\]]*\]', str(at_str))
    if match:
        return match.group(1)
    return at_str


def to_list(data):
    """将 str（逗号分隔）或 list 归一化为字符串列表"""
    if isinstance(data, str):
        return [x.strip() for x in data.split(',')] if data else []
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


def safe_int(value, default=0):
    """安全转换为 int，失败返回 default"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_admin_list_from_config(config_data):
    """从 config_data（group.json dict）中解析管理员列表"""
    if not config_data:
        return []
    admin_data = config_data.get("admin", [])
    return to_list(admin_data)
