# -*- coding: utf-8 -*-
import re

from bin import send, logger, mjbconfig, mjbutils, message


def modcfg():
    return {"version": "1.0.3.4", "author": "JsoftStudio", "description": "屏蔽词检测拦截器：撤回包含屏蔽词的消息"}


def _filter_interceptor(ctx):
    """屏蔽词拦截器：fn(ctx) -> bool

    检测消息是否包含屏蔽词，命中则撤回并返回 True（终止管线）。
    Bot管理员/群管理员豁免；仅在 fkgps_list 中的群启用。
    """
    group_id = ctx["group_id"]
    user_id = ctx["user_id"]
    raw_message = ctx["raw_message"]
    message_id = ctx["message_id"]
    config_data = ctx["config_data"]
    group_id_str = str(group_id)

    # 仅在启用屏蔽词的群检查
    if group_id_str not in mjbconfig.get_fkgps_list():
        return False

    # 检查是否为Bot管理员
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    is_bot_admin = str(user_id) in admin_list

    # 检查是否为群管理员或群主
    member_role = send.get_group_member_role(group_id, user_id)
    is_group_admin_or_owner = member_role in ['owner', 'admin']

    # 只有非管理员用户才需要检查屏蔽词
    if is_bot_admin or is_group_admin_or_owner:
        return False

    # 实时从文件读取屏蔽词配置，确保配置同步
    current_gpfk_configs = mjbconfig.load_module_config("gpfk_configs.json")

    # 获取群的屏蔽词列表
    if group_id_str not in current_gpfk_configs or "words" not in current_gpfk_configs[group_id_str]:
        return False

    bad_words = current_gpfk_configs[group_id_str]["words"]

    # 增强屏蔽词检测：移除消息中的所有符号、空格、换行和零宽空格
    # 首先移除零宽空格和其他不可见字符
    processed_message = re.sub(r'[\u200B\u200E]', '', raw_message)
    # 然后移除所有非中文字符和数字
    processed_message = re.sub(r'[^\u4e00-\u9fa50-9]', '', processed_message)

    # 检查消息是否包含任何屏蔽词
    for word in bad_words:
        if word in processed_message:
            try:
                # 撤回原消息
                send.delete_msg(message_id)
                logger.info(f"已撤回用户{user_id}在群{group_id}发送的包含屏蔽词的消息，屏蔽词：{word}")
            except Exception as e:
                logger.error(f"处理屏蔽词消息失败: {e}")
            return True

    return False


def init():
    """注册屏蔽词拦截器到消息管线"""
    message.register_interceptor(_filter_interceptor)
