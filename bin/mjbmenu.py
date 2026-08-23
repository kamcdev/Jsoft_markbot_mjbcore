# -*- coding: utf-8 -*-
import html as _html

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
    commands_map = mjbconfig.get_commands_map()
    commands_info = mjbconfig.get_commandsinfo()
    commandscategory = mjbconfig.get_commandscategory()
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


def _collect_commands(showhidden, is_bot_admin, is_group_admin):
    """收集并整理命令分类数据，供文本/HTML/图片渲染复用

    Returns:
        (category_commands, admin_cmds)
        category_commands: dict  分类名 -> [cmd, ...]
        admin_cmds: list  当前用户可见的管理员命令
    """
    commands_map = mjbconfig.get_commands_map()
    commands_info = mjbconfig.get_commandsinfo()
    commandscategory = mjbconfig.get_commandscategory()
    commandshidden = mjbconfig.get_commandshidden()
    bot_admin_commands = mjbconfig.get_bot_admin_commands()
    group_admin_commands = mjbconfig.get_group_admin_commands()

    category_commands = {}
    for cmd in commands_map:
        if cmd in commandshidden and not showhidden:
            continue
        if cmd in bot_admin_commands:
            continue
        category = commandscategory.get(cmd, "未分类")
        category_commands.setdefault(category, []).append(cmd)

    admin_cmds = []
    target_list = bot_admin_commands if is_bot_admin else (group_admin_commands if is_group_admin else [])
    for cmd in commands_map:
        if cmd in commandshidden and not showhidden:
            continue
        if cmd in target_list:
            admin_cmds.append(cmd)

    return category_commands, admin_cmds, commands_info


def generate_html(showhidden, is_bot_admin, is_group_admin):
    """生成帮助菜单的完整 HTML 字符串，供无头浏览器渲染截图

    Args:
        showhidden: 是否显示隐藏命令
        is_bot_admin: 是否为 bot 管理员
        is_group_admin: 是否为群管理员/群主
    Returns:
        str: 完整 HTML 文档
    """
    category_commands, admin_cmds, commands_info = _collect_commands(
        showhidden, is_bot_admin, is_group_admin)

    botname = mjbconfig.get_botname()
    version = mjbconfig.get_mjbcver_raw()
    copyright_text = _build_copyright()

    # 构建分类区块
    category_blocks = []
    for category, cmds in category_commands.items():
        rows = []
        for cmd in cmds:
            desc = commands_info.get(cmd, "")
            rows.append(
                f'<div class="cmd-row">'
                f'<span class="cmd-name">{_html.escape(cmd)}</span>'
                f'<span class="cmd-desc">{_html.escape(desc)}</span>'
                f'</div>'
            )
        category_blocks.append(
            f'<div class="category">'
            f'<div class="category-title">{_html.escape(category)}</div>'
            f'<div class="cmd-list">{"".join(rows)}</div>'
            f'</div>'
        )

    # 管理员命令区块
    admin_block = ""
    if admin_cmds and (is_bot_admin or is_group_admin):
        rows = []
        for cmd in admin_cmds:
            desc = commands_info.get(cmd, "")
            rows.append(
                f'<div class="cmd-row admin">'
                f'<span class="cmd-name">{_html.escape(cmd)}</span>'
                f'<span class="cmd-desc">{_html.escape(desc)}</span>'
                f'</div>'
            )
        admin_block = (
            f'<div class="category admin-section">'
            f'<div class="category-title">管理员命令</div>'
            f'<div class="cmd-list">{"".join(rows)}</div>'
            f'</div>'
        )

    hidden_note = '<div class="hidden-note">（已显示隐藏命令）</div>' if showhidden else ''

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
    background: #f0f2f5;
    padding: 24px;
    width: 720px;
}}
.header {{
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: #fff;
    padding: 28px 32px;
    border-radius: 16px 16px 0 0;
    text-align: center;
}}
.header h1 {{
    font-size: 26px;
    font-weight: 700;
    margin-bottom: 6px;
}}
.header .subtitle {{
    font-size: 13px;
    opacity: 0.85;
}}
.tip {{
    background: #fff;
    padding: 14px 32px;
    font-size: 13px;
    color: #666;
    border-bottom: 1px solid #eee;
}}
.tip code {{
    background: #f0f0f0;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 12px;
    color: #d63384;
}}
.content {{
    background: #fff;
    padding: 8px 0;
}}
.category {{
    padding: 12px 32px 8px;
}}
.category-title {{
    font-size: 15px;
    font-weight: 700;
    color: #667eea;
    padding-bottom: 8px;
    border-bottom: 2px solid #f0f0f5;
    margin-bottom: 8px;
}}
.cmd-row {{
    display: flex;
    padding: 5px 0;
    font-size: 14px;
    line-height: 1.6;
}}
.cmd-name {{
    min-width: 160px;
    font-weight: 600;
    color: #333;
    flex-shrink: 0;
}}
.cmd-desc {{
    color: #888;
    font-size: 13px;
}}
.cmd-row.admin .cmd-name {{
    color: #d63384;
}}
.admin-section .category-title {{
    color: #d63384;
}}
.hidden-note {{
    text-align: center;
    padding: 6px 0;
    font-size: 12px;
    color: #999;
    background: #fffbeb;
}}
.footer {{
    background: #fff;
    padding: 16px 32px 24px;
    border-radius: 0 0 16px 16px;
    text-align: center;
    font-size: 11px;
    color: #bbb;
    line-height: 1.8;
    border-top: 1px solid #f0f0f5;
}}
</style>
</head>
<body>
<div class="header">
    <h1>{_html.escape(botname)} 命令帮助</h1>
    <div class="subtitle">版本 {_html.escape(version)}</div>
</div>
<div class="tip">
    直接发送命令以执行相应功能，使用空格添加参数<br>
    示例：<code>ncc 我的世界</code>
</div>
{hidden_note}
<div class="content">
{"".join(category_blocks)}
{admin_block}
</div>
<div class="footer">
    {_html.escape(copyright_text)}
</div>
</body>
</html>"""
