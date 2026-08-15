# -*- coding: utf-8 -*-
from bin import mjbconfig


def _build_copyright():
    return ("Made By JsoftStudio\n"
            "Powered by mjbcore\n"
            "Copyright © JsoftStudio 2024-2026\n"
            "All Right Reserved")


def generate(config_data, is_bot_admin, is_group_admin, showhidden):
    """生成帮助菜单文本与合并转发节点

    Args:
        config_data: group.json dict
        is_bot_admin: 是否为 bot 管理员
        is_group_admin: 是否为群管理员/群主
        showhidden: 是否显示隐藏命令
    Returns:
        {"help_text": str, "forward_messages": list}
    """
    commands_map = config_data.get("commands", {}) if config_data else {}
    commands_info = config_data.get("commandsinfo", {}) if config_data else {}
    commandscategory = config_data.get("commandscategory", {}) if config_data else {}
    commandshidden = mjbconfig.get_commandshidden()
    bot_admin_commands = mjbconfig.get_bot_admin_commands()
    group_admin_commands = mjbconfig.get_group_admin_commands()

    # 按分类组织命令
    category_commands = {}
    for cmd in commands_map:
        if cmd in commandshidden and not showhidden:
            continue
        skip_cmd = cmd in bot_admin_commands  # 管理员命令单独处理
        if not skip_cmd:
            category = commandscategory.get(cmd, "未分类")
            category_commands.setdefault(category, []).append(cmd)

    # 构建纯文本帮助
    help_text = "可用命令列表：\n--------\n"
    help_text += "使用提示：直接发送命令以执行相应功能，使用空格添加参数\n"
    help_text += "示例：“ncc 我的世界”\n"
    if showhidden:
        help_text += "\n(已显示隐藏命令)"
    help_text += "\n"

    for category, cmds in category_commands.items():
        help_text += "--------\n"
        help_text += f"{category}：\n\n"
        for cmd in cmds:
            cmd_desc = commands_info.get(cmd, "")
            help_text += f"{cmd}\n"
            if cmd_desc:
                help_text += f"  {cmd_desc}\n"

    # 管理员命令
    admin_cmds = []
    for cmd in commands_map:
        if cmd in commandshidden and not showhidden:
            continue
        target_list = bot_admin_commands if is_bot_admin else (group_admin_commands if is_group_admin else [])
        if cmd in target_list:
            admin_cmds.append(cmd)
    if admin_cmds and (is_bot_admin or is_group_admin):
        help_text += "--------\n管理员命令：\n"
        for cmd in admin_cmds:
            cmd_desc = commands_info.get(cmd, "")
            help_text += f"{cmd}\n"
            if cmd_desc:
                help_text += f"  {cmd_desc}\n"

    help_text += "\n--------\n" + _build_copyright()

    # 构建合并转发节点
    forward_messages = [{
        "type": "node",
        "data": {"name": "命令帮助", "content": [{"type": "text", "data": {"text": "可用命令列表："}}]},
    }]
    usage_text = "使用提示：直接发送命令以执行相应功能，使用空格添加参数\n示例：\"ncc 我的世界\"\n"
    if showhidden:
        usage_text += "\n(已显示隐藏命令)"
    forward_messages.append({
        "type": "node",
        "data": {"name": "命令帮助", "content": [{"type": "text", "data": {"text": usage_text}}]},
    })
    for category, cmds in category_commands.items():
        category_text = f"{category}：\n"
        for cmd in cmds:
            cmd_desc = commands_info.get(cmd, "")
            category_text += f"{cmd}\n"
            if cmd_desc:
                category_text += f"  {cmd_desc}\n"
        forward_messages.append({
            "type": "node",
            "data": {"name": "命令帮助", "content": [{"type": "text", "data": {"text": category_text}}]},
        })
    if admin_cmds and (is_bot_admin or is_group_admin):
        admin_text = "管理员命令：\n"
        for cmd in admin_cmds:
            cmd_desc = commands_info.get(cmd, "")
            admin_text += f"{cmd}\n"
            if cmd_desc:
                admin_text += f"  {cmd_desc}\n"
        forward_messages.append({
            "type": "node",
            "data": {"name": "命令帮助", "content": [{"type": "text", "data": {"text": admin_text}}]},
        })
    forward_messages.append({
        "type": "node",
        "data": {"name": "命令帮助", "content": [{"type": "text", "data": {"text": _build_copyright()}}]},
    })

    return {"help_text": help_text, "forward_messages": forward_messages}
