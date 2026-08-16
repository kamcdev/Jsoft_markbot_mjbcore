# -*- coding: utf-8 -*-
import os
import json
import random

from bin import logger, mjbconfig, send, mjbmenu, mjbstatus, mjbutils, mjbtmpfile, worker

# 命令分发表：func_name -> 可调用对象
command_functions = {}


def register_command(func_name, func):
    """注册单个命令函数，支持顶替内置命令，插件注册同名函数（如 cmd_autogpwel）会覆盖内置命令函数，优先级大于内置"""
    command_functions[func_name] = func
    if func_name.startswith("cmd_"):
        short = func_name[4:]
        if short:
            command_functions[short] = func
    else:
        command_functions[f"cmd_{func_name}"] = func
    logger.debug(f"已注册命令函数: {func_name}")


def register_commands(mapping):
    """批量注册命令函数 {func_name: func}（同 register_command 的双名注册语义）"""
    for func_name, func in mapping.items():
        command_functions[func_name] = func
        if func_name.startswith("cmd_"):
            short = func_name[4:]
            if short:
                command_functions[short] = func
        else:
            command_functions[f"cmd_{func_name}"] = func


def clear_commands():
    """清空所有已注册命令（供 mjb.reload 使用，保留内置命令）"""
    # 保留 _builtin_commands 中的内置命令
    builtin_names = set(_builtin_commands.keys()) | {f"cmd_{k}" for k in _builtin_commands}
    to_remove = [name for name in command_functions if name not in builtin_names]
    for name in to_remove:
        del command_functions[name]
    logger.debug(f"已清除 {len(to_remove)} 个非内置命令")


# reload 回调（由编排层注册，cmd_reload 执行时调用）
_reload_callback = None


def set_reload_callback(fn):
    """注册 reload 回调函数（由编排层调用）"""
    global _reload_callback
    _reload_callback = fn


def get_command_functions():
    return command_functions


def dispatch(cmd_name, group_id, user_id, config_data, *cmd_args):
    """命令解析与分发

    Returns:
        True  - cmd_name 是已注册命令（无论是否被权限/禁用拦截）
        False - cmd_name 不是命令（交给关键词流程）
    """
    commands_map = mjbconfig.get_commands_map()
    if cmd_name not in commands_map:
        return False

    func_name = commands_map[cmd_name]
    logger.info(f"检测到命令: {cmd_name}, 参数: {cmd_args}")

    # 权限等级判定
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    is_admin = str(user_id) in admin_list
    member_role = "unknown"
    try:
        member_role = send.get_group_member_role(group_id, user_id)
    except Exception as e:
        logger.error(f"获取群成员身份时出错: {e}")

    if is_admin:
        permission_level = "bot管理员"
    elif member_role in ("owner", "admin"):
        permission_level = "群管理员"
    else:
        permission_level = "普通用户"

    # 全视系统记录
    logger.supereye_log_command(group_id, user_id, cmd_name, cmd_args, permission_level)

    # 功能禁用群：仅管理员可执行
    if str(group_id) in mjbconfig.get_bangroup_list() and not is_admin:
        logger.info(f"群{group_id}已禁用功能，忽略非管理员{user_id}的命令: {cmd_name}")
        return True

    # 解析并调用函数
    if func_name in command_functions:
        try:
            result = command_functions[func_name](group_id, user_id, config_data, *cmd_args)
            logger.info(f"命令执行结果: {result}")
        except Exception as e:
            logger.error(f"命令执行出错: {e}")
            send.group(group_id, f"命令执行出错: {e}")
    else:
        logger.error(f"函数 '{func_name}' 未定义")
        send.group(group_id, f"函数 '{func_name}' 未定义")
    return True


def _get_user_roles(group_id, user_id, config_data):
    """获取用户角色：是否为 bot 管理员 / 群管理员（群主或管理员）"""
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    is_bot_admin = str(user_id) in admin_list
    is_group_admin = False
    try:
        role = send.get_group_member_role(group_id, user_id)
        is_group_admin = role in ("owner", "admin")
    except Exception as e:
        logger.error(f"获取群成员身份时出错: {e}")
    return is_bot_admin, is_group_admin


def _render_help_image(group_id, showhidden, is_bot_admin, is_group_admin):
    """在线程池中用无头浏览器渲染帮助菜单 HTML 并截图发送

    流程：生成 HTML -> 写临时文件 -> Selenium(Edge headless) 渲染并全页截图 -> 发图 -> 清理
    """
    import time

    html_path = None
    png_path = None
    driver = None
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options

        html_content = mjbmenu.generate_html(showhidden, is_bot_admin, is_group_admin)
        html_path = mjbtmpfile.create(suffix=".html", prefix="help_")
        png_path = mjbtmpfile.create(suffix=".png", prefix="help_")
        if not html_path or not png_path:
            send.group(group_id, "帮助图片生成失败：无法创建临时文件")
            return

        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        # file:// URI（Windows 路径反斜杠需转为正斜杠）
        file_uri = "file:///" + os.path.abspath(html_path).replace(os.sep, "/")

        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=760,1080')

        driver = webdriver.Edge(options=options)
        driver.set_page_load_timeout(15)
        driver.get(file_uri)

        # 等待字体与布局就绪
        time.sleep(1)

        # 全页截图：将窗口高度调整为内容实际高度后截图
        try:
            total_height = driver.execute_script(
                "return Math.max(document.body.scrollHeight, "
                "document.documentElement.scrollHeight);")
            driver.set_window_size(760, int(total_height))
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"获取页面高度失败: {e}")

        driver.save_screenshot(png_path)
        logger.info(f"帮助图片已生成: {png_path}")

        success = send.group_image(group_id, png_path)
        if not success:
            send.group(group_id, "帮助图片发送失败")
    except Exception as e:
        logger.error(f"渲染帮助图片失败: {e}")
        send.group(group_id, f"帮助图片生成失败: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        if html_path and os.path.exists(html_path):
            mjbtmpfile.cleanup_now(html_path)
        if png_path and os.path.exists(png_path):
            mjbtmpfile.cleanup_now(png_path)


# ===================== 内置核心命令 =====================
def cmd_help(group_id, user_id, config_data, *args):
    """显示帮助信息，根据用户权限显示不同命令列表"""
    showhidden = len(args) > 0 and args[0] == "-showhidden"
    onimagehelp = mjbconfig.get_onimagehelp()

    # -h/-H 或关闭图片帮助 → 文字帮助
    if (len(args) > 0 and args[0] in ("-h", "-H")) or not onimagehelp:
        is_bot_admin, is_group_admin = _get_user_roles(group_id, user_id, config_data)

        result = mjbmenu.generate(config_data, is_bot_admin, is_group_admin, showhidden)
        try:
            requests_post = send.api  # 通用 API
            # 直接调用合并转发接口发送节点
            import requests
            from bin import mjbconfig as _cfg
            response = requests.post(
                f"{_cfg.get_LLbot_url()}/send_group_forward_msg",
                json={"group_id": int(group_id), "messages": result["forward_messages"]},
                timeout=15,
            )
            return "已发送帮助信息"
        except Exception as e:
            logger.error(f"发送合并转发消息失败: {e}")
            send.send_group_forward_msg(group_id, [result["help_text"]], fake_name="命令帮助")
            return "已发送帮助信息"
    else:
        # 图片帮助：无头浏览器渲染 HTML 截图
        is_bot_admin, is_group_admin = _get_user_roles(group_id, user_id, config_data)
        worker.submit(_render_help_image, group_id, showhidden, is_bot_admin, is_group_admin)
        send.group(group_id, "正在生成帮助图片，请稍候...")
        return "帮助图片生成任务已启动"


def cmd_test(group_id, user_id, config_data, *args):
    """测试命令"""
    send.group(group_id, "状态正常")
    return "测试命令执行成功"


def cmd_echo(group_id, user_id, config_data, *args):
    """回显参数内容"""
    if args:
        echo_text = " ".join(args)
        send.send_group_forward_msg(group_id, [f"回显: {echo_text}"], fake_name="打印机")
        return f"已回显消息: {echo_text}"
    send.send_group_forward_msg(group_id, ["请提供要回显的内容"], fake_name="打印机")
    return "未提供回显内容"


def cmd_status(group_id, user_id, config_data, *args):
    """获取当前 bot 系统状态"""
    status_message = mjbstatus.generate()
    send.group(group_id, status_message)
    return "已发送系统状态信息"


def cmd_notice(group_id, user_id, config_data, *args):
    """查看当前公告"""
    current_notice = config_data.get("current_notice", "暂无公告")
    send.send_group_forward_msg(group_id, [f"【当前公告】\n{current_notice}"], fake_name="公告查询")
    return "已发送当前公告"


def cmd_reboot(group_id, user_id, config_data, *args):
    """重启 bot（仅管理员）"""
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    if str(user_id) not in admin_list:
        send.group(group_id, "权限不足，仅Bot管理员可重启")
        return "权限不足"
    send.group(group_id, "正在重启...")
    logger.warning("收到重启命令，即将重启程序")
    try:
        import sys
        import subprocess
        python = sys.executable
        subprocess.Popen([python, __file__])  # 由编排层重写实际入口
        import os as _os
        _os._exit(0)
    except Exception as e:
        logger.error(f"重启失败: {e}")
        send.group(group_id, f"重启失败: {e}")
        return f"重启失败: {e}"


def cmd_reload(group_id, user_id, config_data, *args):
    """重载模块但不重启程序（仅Bot管理员）

    执行后会阻塞主线程，卸载所有模块/配置/命令并重新加载，然后重新自检并加载配置。
    """
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    if str(user_id) not in admin_list:
        send.group(group_id, "权限不足，仅Bot管理员可重载")
        return "权限不足"

    if _reload_callback is None:
        send.group(group_id, "重载功能未就绪，无法执行")
        return "reload 回调未注册"

    send.group(group_id, "正在重载模块...")
    logger.warning("收到重载命令，开始卸载并重新加载模块")

    try:
        _reload_callback()
        send.group(group_id, "模块与配置重载完成")
        return "重载完成"
    except Exception as e:
        logger.error(f"重载失败: {e}")
        send.group(group_id, f"重载失败: {e}")
        return f"重载失败: {e}"


def _collect_loaded_modules():
    """收集当前已载入的 modules.* 模块信息

    Returns:
        {模块短名: {"version": ..., "author": ..., "description": ...}}
    """
    import sys

    mods = {}
    for full_name, mod in list(sys.modules.items()):
        if not full_name.startswith("modules."):
            continue
        short_name = full_name[len("modules."):]
        if "." in short_name:
            continue  # 跳过嵌套子模块
        info = {}
        try:
            modcfg_fn = getattr(mod, "modcfg", None)
            if callable(modcfg_fn):
                info = modcfg_fn() or {}
        except Exception as e:
            logger.error(f"获取模块 {short_name} 信息失败: {e}")
        mods[short_name] = info
    return mods


def cmd_mod(group_id, user_id, config_data, *args):
    """查看已载入模块（仅Bot管理员）

    用法：
    - mjb.mod list        - 以合并转发卡片列出已载入模块
    - mjb.mod info <模块名> - 查看指定模块的版本/作者/介绍
    """
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    if str(user_id) not in admin_list:
        send.group(group_id, "权限不足，仅Bot管理员可使用此命令")
        return "权限不足"

    if not args:
        send.group(group_id, "用法：mjb.mod list 或 mjb.mod info <模块名>")
        return "用法错误"

    mods = _collect_loaded_modules()
    sub = args[0].lower()

    if sub == "list":
        mod_list = sorted(mods.keys())
        content = "当前已载入模块：\n" + "\n".join(mod_list)
        send.send_group_forward_msg(group_id, [content], fake_name="已载入模块")
        return "已发送模块列表"

    if sub == "info":
        if len(args) < 2:
            send.group(group_id, "用法：mjb.mod info <模块名>")
            return "缺少模块名"
        mod_name = args[1]
        if mod_name not in mods:
            send.group(group_id, f"模块 {mod_name} 未载入")
            return "模块未载入"
        info = mods[mod_name]
        content = (
            f"模块{mod_name}的信息：\n"
            f"版本：{info.get('version', '未知')}\n"
            f"作者：{info.get('author', '未知')}\n"
            f"介绍：{info.get('description', '未知')}"
        )
        send.group(group_id, content)
        return "已发送模块信息"

    send.group(group_id, "用法：mjb.mod list 或 mjb.mod info <模块名>")
    return "用法错误"


def cmd_botban(group_id, user_id, config_data, *args):
    """将用户加入 bot 黑名单"""
    if not args:
        send.group(group_id, "请提供要拉黑的用户QQ或@用户")
        return "未提供参数"
    target = mjbutils.extract_qq_from_at(args[0])
    banned = mjbconfig.load_banuser()
    banned[str(target)] = {"banned_by": str(user_id), "banned_time": int(__import__("time").time())}
    mjbconfig.save_banuser(banned)
    send.group(group_id, f"已将 {target} 加入bot黑名单")
    return f"已拉黑 {target}"


def cmd_botunban(group_id, user_id, config_data, *args):
    """将用户移出 bot 黑名单"""
    if not args:
        send.group(group_id, "请提供要解除拉黑的用户QQ")
        return "未提供参数"
    target = mjbutils.extract_qq_from_at(args[0])
    banned = mjbconfig.load_banuser()
    if str(target) in banned:
        del banned[str(target)]
        mjbconfig.save_banuser(banned)
        send.group(group_id, f"已将 {target} 移出bot黑名单")
        return f"已解除拉黑 {target}"
    send.group(group_id, f"{target} 不在黑名单中")
    return "不在黑名单"


def cmd_event(group_id, user_id, config_data, *args):
    """关键词事件管理命令（仅管理员）：mjb.event add/del/set/list"""
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    if str(user_id) not in admin_list:
        send.group(group_id, "权限不足，仅Bot管理员可管理事件关键词")
        return "权限不足"
    gpevent = mjbconfig.load_gpevent()
    group_str = str(group_id)
    if not args:
        send.group(group_id, "用法：mjb.event add 关键词 回复 / del 关键词 / list")
        return "未提供子命令"
    sub = args[0]
    if sub == "list":
        events = gpevent.get(group_str, {})
        if not events:
            send.group(group_id, "本群暂无事件关键词")
            return "无关键词"
        msg = "本群事件关键词：\n" + "\n".join(f"{k}: {v}" for k, v in events.items())
        send.group(group_id, msg)
        return "已列出"
    if sub == "add" and len(args) >= 3:
        keyword = args[1]
        reply = " ".join(args[2:])
        gpevent.setdefault(group_str, {})[keyword] = reply
        mjbconfig.save_gpevent(gpevent)
        send.group(group_id, f"已添加关键词: {keyword}")
        return "已添加"
    if sub == "del" and len(args) >= 2:
        keyword = args[1]
        if group_str in gpevent and keyword in gpevent[group_str]:
            del gpevent[group_str][keyword]
            mjbconfig.save_gpevent(gpevent)
            send.group(group_id, f"已删除关键词: {keyword}")
            return "已删除"
        send.group(group_id, f"关键词 {keyword} 不存在")
        return "不存在"
    send.group(group_id, "用法：mjb.event add 关键词 回复 / del 关键词 / list")
    return "用法错误"


# ===================== mjb.* 扩展命令 =====================
def cmd_likeme(group_id, user_id, config_data, *args):
    """点赞命令，有参数时为指定用户点赞，无参数时为执行者点赞
    参数支持直接输入QQ号或CQ:at格式(@用户)"""
    try:
        if args:
            target_user_id = mjbutils.extract_qq_from_at(args[0])
        else:
            target_user_id = user_id

        result = send.api('send_like', user_id=int(target_user_id), times=10)

        if result.get('status') == 'ok' and result.get('retcode') == 0:
            send.group(group_id, f"已为{target_user_id}点赞10次！")
            logger.info(f"已为用户{target_user_id}点赞10次")
            return "点赞成功"
        else:
            error_msg = result.get('message', '点赞失败')
            send.group(group_id, f"为{target_user_id}点赞失败：{error_msg}")
            logger.info(f"为用户{target_user_id}点赞失败：{error_msg}")
            return f"点赞失败：{error_msg}"

    except Exception as e:
        error_msg = f"为{target_user_id}点赞过程中发生错误：{e}"
        logger.error(error_msg)
        send.group(group_id, error_msg)
        return error_msg


def cmd_poke(group_id, user_id, config_data, *args):
    """戳一戳命令，有参数时戳指定用户，无参数时戳执行者
    参数支持直接输入QQ号或CQ:at格式(@用户)"""
    try:
        if args:
            target_user_id = mjbutils.extract_qq_from_at(args[0])
        else:
            target_user_id = user_id

        result = send.api('group_poke', group_id=int(group_id), user_id=int(target_user_id))

        if result.get('status') == 'ok' and result.get('retcode') == 0:
            logger.info(f"已戳用户{target_user_id}一下")
            return "戳一戳成功"
        else:
            error_msg = result.get('message', '戳一戳失败')
            send.group(group_id, f"戳{target_user_id}失败：{error_msg}")
            logger.info(f"戳用户{target_user_id}失败：{error_msg}")
            return f"戳一戳失败：{error_msg}"

    except Exception as e:
        error_msg = f"戳{target_user_id}过程中发生错误：{e}"
        logger.error(error_msg)
        send.group(group_id, error_msg)
        return error_msg


def cmd_autogpsing(group_id, user_id, config_data, *args):
    """自动打卡命令，将当前群添加到自动打卡列表，每天凌晨00:00:10自动打卡
    仅管理员可用"""
    admin_list = mjbutils.get_admin_list_from_config(config_data)

    if str(user_id) not in admin_list:
        send.group(group_id, "权限不足，只有管理员可以使用此命令。")
        logger.info(f"非管理员{user_id}尝试使用autogpsing命令")
        return "权限不足"

    current_autosing_list = mjbutils.to_list(config_data.get("autosinggps", [])) if config_data else []
    group_str = str(group_id)

    if group_str not in current_autosing_list:
        current_autosing_list.append(group_str)
        try:
            config_data["autosinggps"] = current_autosing_list
            mjbconfig.save(config_data)
            send.group(group_id, "已将本群加入自动打卡列表，将在每天凌晨00:00:10自动打卡。")
            logger.info(f"已将群{group_id}加入自动打卡列表")
            return f"成功将群{group_id}加入自动打卡列表"
        except Exception as e:
            error_msg = f"保存配置时出错: {e}"
            logger.error(error_msg)
            send.group(group_id, error_msg)
            return error_msg
    else:
        send.group(group_id, "本群已经在自动打卡列表中。")
        return f"群{group_id}已在自动打卡列表中"


def cmd_closeingp(group_id, user_id, config_data, *args):
    """禁用群功能命令，将当前群加入功能禁用列表，除自动打卡外的其他功能均不可用
    群主、群管理员和Bot管理员可用"""
    admin_list = mjbutils.get_admin_list_from_config(config_data)

    has_permission = False
    if str(user_id) in admin_list:
        has_permission = True
    else:
        try:
            member_role = send.get_group_member_role(group_id, user_id)
            if member_role in ('owner', 'admin'):
                has_permission = True
        except Exception as e:
            logger.error(f"获取群成员身份时出错: {e}")

    if not has_permission:
        send.group(group_id, "权限不足，只有群主、群管理员或Bot管理员可以使用此命令。")
        logger.info(f"无权限用户{user_id}尝试使用closeingp命令")
        return "权限不足"

    current_ban_list = mjbutils.to_list(config_data.get("bangroup", [])) if config_data else []
    current_banrep_list = mjbutils.to_list(config_data.get("banrepgroup", [])) if config_data else []
    group_str = str(group_id)

    if group_str not in current_ban_list:
        current_ban_list.append(group_str)
        if group_str not in current_banrep_list:
            current_banrep_list.append(group_str)

        try:
            config_data["bangroup"] = current_ban_list
            config_data["banrepgroup"] = current_banrep_list
            mjbconfig.save(config_data)
            mjbconfig.reload()
            send.group(group_id, "已将本群加入功能禁用列表，除自动打卡外的所有功能已禁用。")
            logger.info(f"已将群{group_id}加入功能禁用列表")
            return f"成功将群{group_id}加入功能禁用列表"
        except Exception as e:
            error_msg = f"保存配置时出错: {e}"
            logger.error(error_msg)
            send.group(group_id, error_msg)
            return error_msg
    else:
        send.group(group_id, "本群已经在功能禁用列表中。")
        return f"群{group_id}已在功能禁用列表中"


def cmd_allowingp(group_id, user_id, config_data, *args):
    """解除群功能禁用命令，将当前群从功能禁用列表中移除
    群主、群管理员和Bot管理员可用"""
    admin_list = mjbutils.get_admin_list_from_config(config_data)

    has_permission = False
    if str(user_id) in admin_list:
        has_permission = True
    else:
        try:
            member_role = send.get_group_member_role(group_id, user_id)
            if member_role in ('owner', 'admin'):
                has_permission = True
        except Exception as e:
            logger.error(f"获取群成员身份时出错: {e}")

    if not has_permission:
        send.group(group_id, "权限不足，只有群主、群管理员或Bot管理员可以使用此命令。")
        logger.info(f"无权限用户{user_id}尝试使用allowingp命令")
        return "权限不足"

    current_ban_list = mjbutils.to_list(config_data.get("bangroup", [])) if config_data else []
    group_str = str(group_id)
    if group_str in current_ban_list:
        current_ban_list.remove(group_str)
        try:
            config_data["bangroup"] = current_ban_list
            mjbconfig.save(config_data)
            mjbconfig.reload()
            send.group(group_id, "已将本群从功能禁用列表中移除，所有功能现已恢复正常使用。")
            logger.info(f"已将群{group_id}从功能禁用列表中移除")
            return f"成功将群{group_id}从功能禁用列表中移除"
        except Exception as e:
            error_msg = f"保存配置时出错: {e}"
            logger.error(error_msg)
            send.group(group_id, error_msg)
            return error_msg
    else:
        send.group(group_id, "本群不在功能禁用列表中，无需移除。")
        return f"群{group_id}不在功能禁用列表中"


def cmd_fk(group_id, user_id, config_data, *args):
    """管理群屏蔽词功能

    仅bot管理和群管理可用，可开启/关闭或设置参数
    功能：使用on/off参数在群内开启或关闭屏蔽词，使用add word 内容 添加屏蔽词，使用del word 内容 删除屏蔽词
    """
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    is_admin = str(user_id) in admin_list
    is_group_admin = False
    if not is_admin:
        try:
            member_role = send.get_group_member_role(group_id, user_id)
            if member_role in ('owner', 'admin'):
                is_group_admin = True
        except Exception as e:
            logger.error(f"获取群成员身份时出错: {e}")

    if not (is_admin or is_group_admin):
        send.group(group_id, "权限不足，只有bot管理或群管理可以使用该命令")
        return "权限不足"

    if len(args) == 0:
        help_msg = "群屏蔽词管理命令用法：\n"
        help_msg += "1. mjb.fk on - 开启群屏蔽词功能\n"
        help_msg += "2. mjb.fk off - 关闭群屏蔽词功能\n"
        help_msg += "3. mjb.fk add word 内容 - 添加屏蔽词\n"
        help_msg += "4. mjb.fk del word 内容 - 删除屏蔽词\n"
        help_msg += "5. mjb.fk list - 查看当前群屏蔽词列表"
        send.group(group_id, help_msg)
        return "已发送帮助信息"

    subcmd = args[0].lower()
    group_str = str(group_id)
    fkgps_list = mjbutils.to_list(config_data.get("fkgps", [])) if config_data else []
    gpfk_configs = mjbconfig.load_module_config("gpfk_configs.json")

    if subcmd == "on":
        if group_str not in fkgps_list:
            fkgps_list.append(group_str)
            config_data["fkgps"] = fkgps_list
            if group_str not in gpfk_configs:
                gpfk_configs[group_str] = {'words': []}
                mjbconfig.save_module_config("gpfk_configs.json", gpfk_configs)
            mjbconfig.save(config_data)
            mjbconfig.reload()
            send.group(group_id, "群屏蔽词功能已开启")
            return "群屏蔽词功能已开启"
        else:
            send.group(group_id, "群屏蔽词功能已经是开启状态")
            return "群屏蔽词功能已经是开启状态"

    elif subcmd == "off":
        if group_str in fkgps_list:
            fkgps_list.remove(group_str)
            config_data["fkgps"] = fkgps_list
            if group_str in gpfk_configs:
                del gpfk_configs[group_str]
                mjbconfig.save_module_config("gpfk_configs.json", gpfk_configs)
            mjbconfig.save(config_data)
            mjbconfig.reload()
            send.group(group_id, "群屏蔽词功能已关闭")
            return "群屏蔽词功能已关闭"
        else:
            send.group(group_id, "群屏蔽词功能已经是关闭状态")
            return "群屏蔽词功能已经是关闭状态"

    elif subcmd == "add" and len(args) >= 3 and args[1].lower() == "word":
        if group_str not in fkgps_list:
            send.group(group_id, "请先开启群屏蔽词功能")
            return "未开启屏蔽词功能"

        word_content = " ".join(args[2:])

        if any(c.isalpha() and c.isascii() for c in word_content):
            send.group(group_id, "暂不支持英文屏蔽词，优化中")
            return "暂不支持英文屏蔽词"

        if group_str not in gpfk_configs:
            gpfk_configs[group_str] = {'words': []}

        if word_content not in gpfk_configs[group_str]['words']:
            gpfk_configs[group_str]['words'].append(word_content)
            mjbconfig.save_module_config("gpfk_configs.json", gpfk_configs)
            send.group(group_id, f"已添加屏蔽词：{word_content}")
            return f"已添加屏蔽词：{word_content}"
        else:
            send.group(group_id, f"屏蔽词'{word_content}'已存在")
            return "屏蔽词已存在"

    elif subcmd == "del" and len(args) >= 3 and args[1].lower() == "word":
        if group_str not in fkgps_list:
            send.group(group_id, "请先开启群屏蔽词功能")
            return "未开启屏蔽词功能"

        word_content = " ".join(args[2:])

        if group_str in gpfk_configs:
            if word_content in gpfk_configs[group_str]['words']:
                gpfk_configs[group_str]['words'].remove(word_content)
                mjbconfig.save_module_config("gpfk_configs.json", gpfk_configs)
                send.group(group_id, f"已删除屏蔽词：{word_content}")
                return f"已删除屏蔽词：{word_content}"
            else:
                send.group(group_id, f"屏蔽词'{word_content}'不存在")
                return "屏蔽词不存在"
        else:
            send.group(group_id, "当前群没有配置屏蔽词")
            return "没有配置屏蔽词"

    elif subcmd == "list":
        if group_str in fkgps_list:
            if group_str in gpfk_configs and 'words' in gpfk_configs[group_str]:
                words = gpfk_configs[group_str]['words']
                if words:
                    webui_url = f"https://mjb.jsoftstudio.top/fklist?gp={group_id}"
                    send.group(group_id, f"请前往{webui_url}查看本群屏蔽词列表")
                    return "已发送WebUI链接"
                else:
                    send.group(group_id, "当前群没有设置屏蔽词")
                    return "没有设置屏蔽词"
            else:
                send.group(group_id, "当前群没有设置屏蔽词")
                return "没有设置屏蔽词"
        else:
            send.group(group_id, "当前群未开启屏蔽词功能")
            return "未开启屏蔽词功能"

    else:
        send.group(group_id, "无效命令，请查看帮助信息")
        return "无效命令"


def cmd_setnotice(group_id, user_id, config_data, *args):
    """推送公告（管理员功能）"""
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    if str(user_id) not in admin_list:
        send.group(group_id, "权限不足，只有管理员可以使用该命令")
        return "权限不足"

    if len(args) == 0:
        send.group(group_id, "请提供公告内容，格式：mjb.setnotice 公告内容")
        return "缺少公告内容"

    notice_content = args[0]
    config_data["current_notice"] = notice_content

    try:
        mjbconfig.save(config_data)
    except Exception as e:
        error_message = f"保存公告失败: {e}"
        send.group(group_id, error_message)
        return error_message

    notice_list = mjbutils.to_list(config_data.get("noticelist", []))
    notice_message = "管理员发布了新的公告,请及时查看bot新修改"

    success_count = 0
    for target_group in notice_list:
        try:
            send.group(target_group, notice_message)
            send.send_group_forward_msg(target_group, [f"【公告】\n{notice_content}"], fake_name="公告推送")
            success_count += 1
        except Exception as e:
            logger.error(f"向群{target_group}发送公告失败: {e}")

    result_message = f"公告推送完成，成功发送到{success_count}个群"
    send.group(group_id, result_message)
    return result_message


def cmd_allownotice(group_id, user_id, config_data, *args):
    """将当前群加入公告白名单
    群主、群管理员和Bot管理员可用"""
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    has_permission = False
    if str(user_id) in admin_list:
        has_permission = True
    else:
        try:
            member_role = send.get_group_member_role(group_id, user_id)
            if member_role in ('owner', 'admin'):
                has_permission = True
        except Exception as e:
            logger.error(f"获取群成员身份时出错: {e}")

    if not has_permission:
        send.group(group_id, "权限不足，只有群主、群管理员或Bot管理员可以使用该命令")
        logger.info(f"无权限用户{user_id}尝试使用allownotice命令")
        return "权限不足"

    notice_list = mjbutils.to_list(config_data.get("noticelist", []))
    group_str = str(group_id)
    if group_str not in notice_list:
        notice_list.append(group_str)
        config_data["noticelist"] = notice_list
        try:
            mjbconfig.save(config_data)
            message = "已成功将本群加入公告白名单"
            send.group(group_id, message)
            return message
        except Exception as e:
            error_message = f"保存配置失败: {e}"
            send.group(group_id, error_message)
            return error_message
    else:
        message = "本群已在公告白名单中"
        send.group(group_id, message)
        return message


def cmd_closenotice(group_id, user_id, config_data, *args):
    """将当前群从公告白名单移除
    群主、群管理员和Bot管理员可用"""
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    has_permission = False
    if str(user_id) in admin_list:
        has_permission = True
    else:
        try:
            member_role = send.get_group_member_role(group_id, user_id)
            if member_role in ('owner', 'admin'):
                has_permission = True
        except Exception as e:
            logger.error(f"获取群成员身份时出错: {e}")

    if not has_permission:
        send.group(group_id, "权限不足，只有群主、群管理员或Bot管理员可以使用该命令")
        logger.info(f"无权限用户{user_id}尝试使用closenotice命令")
        return "权限不足"

    notice_list = mjbutils.to_list(config_data.get("noticelist", []))
    group_str = str(group_id)
    if group_str in notice_list:
        notice_list.remove(group_str)
        config_data["noticelist"] = notice_list
        try:
            mjbconfig.save(config_data)
            message = "已成功将本群从公告白名单中移除"
            send.group(group_id, message)
            return message
        except Exception as e:
            error_message = f"保存配置失败: {e}"
            send.group(group_id, error_message)
            return error_message
    else:
        message = "本群不在公告白名单中"
        send.group(group_id, message)
        return message


def cmd_autogpauth(group_id, user_id, config_data, *args):
    """群验证功能开关

    开启或关闭群加入验证功能，bot管理员和群管理员可用
    """
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    is_admin = str(user_id) in admin_list

    if not is_admin:
        try:
            member_role = send.get_group_member_role(group_id, user_id)
        except Exception as e:
            logger.error(f"获取群成员身份时出错: {e}")
            member_role = "unknown"
        if member_role not in ('owner', 'admin'):
            message = "权限不足，只有Bot管理员和群管理员可以使用此命令"
            send.group(group_id, message)
            return message

    if len(args) >= 4 and args[0].lower() == 'set' and args[3] == '-g':
        if not is_admin:
            message = "权限不足，只有Bot管理员可以设置全局参数"
            send.group(group_id, message)
            return message

    gpauthgroups = mjbutils.to_list(mjbconfig.get("autoauthgps", []))
    gpauthfrequency = mjbconfig.get("gpauthfrequency", 3)
    gpauthtime = mjbconfig.get("gpauthtime", 300)
    gpauth_configs = mjbconfig.get("gpauth_configs", {}) or {}

    def get_group_auth_params(gp_id):
        group_frequency = gpauthfrequency
        group_timeout = gpauthtime
        if str(gp_id) in gpauth_configs:
            if "frequency" in gpauth_configs[str(gp_id)]:
                group_frequency = gpauth_configs[str(gp_id)]["frequency"]
            if "timeout" in gpauth_configs[str(gp_id)]:
                group_timeout = gpauth_configs[str(gp_id)]["timeout"]
        return group_frequency, group_timeout

    if len(args) == 0:
        if str(group_id) in gpauthgroups:
            group_frequency, group_timeout = get_group_auth_params(group_id)
            message = f"当前群已开启验证功能\n验证最多尝试次数: {group_frequency}"
            if str(group_id) in gpauth_configs and "frequency" in gpauth_configs[str(group_id)]:
                message += " (单群设置)"
            message += f"\n验证超时时间: {group_timeout}秒"
            if str(group_id) in gpauth_configs and "timeout" in gpauth_configs[str(group_id)]:
                message += " (单群设置)"
        else:
            message = "当前群未开启验证功能，使用 'mjb.autogpauth on' 开启"
        send.group(group_id, message)
        return message

    subcmd = args[0].lower()

    if subcmd == 'on':
        try:
            autoauthgps = mjbutils.to_list(config_data.get("autoauthgps", []))
            if str(group_id) not in autoauthgps:
                autoauthgps.append(str(group_id))
                config_data["autoauthgps"] = autoauthgps
                mjbconfig.save(config_data)
                mjbconfig.reload()
                message = "群验证功能已开启，新人加入将需要进行口算题验证"
            else:
                message = "群验证功能已经是开启状态"
        except Exception as e:
            message = f"开启验证失败: {e}"
            logger.error(f"开启验证失败: {e}")
        send.group(group_id, message)
        return message
    elif subcmd == 'off':
        try:
            autoauthgps = mjbutils.to_list(config_data.get("autoauthgps", []))
            if str(group_id) in autoauthgps:
                autoauthgps.remove(str(group_id))
                config_data["autoauthgps"] = autoauthgps
                mjbconfig.save(config_data)
                mjbconfig.reload()
                authusers = mjbconfig.load_gpauths()
                if str(group_id) in authusers:
                    del authusers[str(group_id)]
                    mjbconfig.save_gpauths(authusers)
                message = "群验证功能已关闭"
            else:
                message = "群验证功能已经是关闭状态"
        except Exception as e:
            message = f"关闭验证失败: {e}"
            logger.error(f"关闭验证失败: {e}")
        send.group(group_id, message)
        return message
    elif subcmd == 'set' and len(args) >= 3:
        param = args[1].lower()
        try:
            value = int(args[2])
            if "gpauth_configs" not in config_data:
                config_data["gpauth_configs"] = {}

            is_global = False
            if len(args) >= 4 and args[3] == "-g":
                is_global = True

            if is_global:
                if param == 'frequency':
                    new_value = max(1, value)
                    config_data["gpauthfrequency"] = new_value
                    message = f"全局验证尝试次数已设置为: {new_value}"
                elif param == 'timeout':
                    new_value = max(60, value)
                    config_data["gpauthtime"] = new_value
                    message = f"全局验证超时时间已设置为: {new_value}秒"
                else:
                    message = "无效的参数名，可选: frequency, timeout"
            else:
                if param == 'frequency':
                    new_value = max(1, value)
                    if str(group_id) not in config_data["gpauth_configs"]:
                        config_data["gpauth_configs"][str(group_id)] = {}
                    config_data["gpauth_configs"][str(group_id)]["frequency"] = new_value
                    message = f"本群验证尝试次数已设置为: {new_value}"
                elif param == 'timeout':
                    new_value = max(60, value)
                    if str(group_id) not in config_data["gpauth_configs"]:
                        config_data["gpauth_configs"][str(group_id)] = {}
                    config_data["gpauth_configs"][str(group_id)]["timeout"] = new_value
                    message = f"本群验证超时时间已设置为: {new_value}秒"
                else:
                    message = "无效的参数名，可选: frequency, timeout"

            mjbconfig.save(config_data)
            mjbconfig.reload()
        except ValueError:
            message = "参数值必须是数字"
        except Exception as e:
            message = f"设置参数失败: {e}"
            logger.error(f"设置参数失败: {e}")
        send.group(group_id, message)
        return message
    elif subcmd == 'del' and len(args) >= 2:
        param = args[1].lower()
        try:
            if "gpauth_configs" in config_data and str(group_id) in config_data["gpauth_configs"]:
                if param in config_data["gpauth_configs"][str(group_id)]:
                    del config_data["gpauth_configs"][str(group_id)][param]
                    if not config_data["gpauth_configs"][str(group_id)]:
                        del config_data["gpauth_configs"][str(group_id)]
                    mjbconfig.save(config_data)
                    mjbconfig.reload()
                    message = f"本群验证{param}已重置为全局设置"
                else:
                    message = f"本群未设置验证{param}"
            else:
                message = "本群未设置任何单群验证参数"
        except Exception as e:
            message = f"重置参数失败: {e}"
            logger.error(f"重置参数失败: {e}")
        send.group(group_id, message)
        return message
    else:
        message = """
用法:
1. mjb.autogpauth [on/off] - 开关群验证
2. mjb.autogpauth set [frequency/timeout] 值 - 设置本群验证参数
3. mjb.autogpauth set [frequency/timeout] 值 -g - 设置全局验证参数
4. mjb.autogpauth del [frequency/timeout] - 删除本群验证参数，恢复使用全局设置"""
        send.group(group_id, message)
        return message


def cmd_setauthok(group_id, user_id, config_data, *args):
    """将指定用户直接设置为验证通过

    仅bot管理员可用，功能是如果第一个参数中的qq号正在本群进行入群口算验证，
    则直接将此用户设置为通过验证（支持解析@cq码为qq号）
    """
    admin_list = mjbutils.get_admin_list_from_config(config_data)

    if str(user_id) not in admin_list:
        send.group(group_id, "权限不足，只有Bot管理员可以使用此命令")
        logger.info(f"无权限用户{user_id}尝试使用setauthok命令")
        return "权限不足"

    if not args:
        send.group(group_id, "请提供要设置通过验证的QQ号，格式：setauthok QQ号 或 setauthok @用户")
        return "缺少QQ号参数"

    target_qq = args[0].strip()

    if '[CQ:at' in target_qq:
        try:
            qq_start = target_qq.find('qq=') + 3
            qq_end = target_qq.find(']')
            if qq_start > 2 and qq_end > qq_start:
                target_qq = target_qq[qq_start:qq_end]
        except Exception as e:
            logger.error(f"解析@cq码失败: {e}")
    elif target_qq.startswith('@'):
        try:
            start_idx = target_qq.find('(')
            end_idx = target_qq.find(')')
            if start_idx > 0 and end_idx > start_idx:
                target_qq = target_qq[start_idx + 1:end_idx]
        except Exception as e:
            logger.error(f"解析@格式失败: {e}")

    target_qq = str(target_qq)

    if not target_qq.isdigit():
        send.group(group_id, "无效的QQ号格式，请提供正确的QQ号或使用@功能")
        return "无效的QQ号格式"

    group_id_str = str(group_id)
    authusers = mjbconfig.load_gpauths()
    if group_id_str not in authusers or target_qq not in authusers[group_id_str]:
        send.group(group_id, f"用户{target_qq}当前不在验证列表中，无需设置通过")
        return "用户不在验证列表中"

    del authusers[group_id_str][target_qq]
    mjbconfig.save_gpauths(authusers)

    message = f"已将用户{target_qq}设置为验证通过"
    send.group(group_id, message)
    logger.info(f"Bot管理员{user_id}已将用户{target_qq}在群{group_id}中设置为验证通过")

    try:
        send.group_at(group_id, int(target_qq), " 验证已通过，新人请看公告，欢迎入群聊天")
    except Exception as e:
        logger.error(f"@用户通知验证成功失败: {e}")

    return message


def cmd_autogpwel(group_id, user_id, config_data, *args):
    """群入群欢迎功能管理

    开启或关闭群入群欢迎功能，设置欢迎内容，bot管理员和群管理员可用
    """
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    is_admin = str(user_id) in admin_list

    if not is_admin:
        try:
            member_role = send.get_group_member_role(group_id, user_id)
        except Exception as e:
            logger.error(f"获取群成员身份时出错: {e}")
            member_role = "unknown"
        if member_role not in ('owner', 'admin'):
            message = "权限不足，只有Bot管理员和群管理员可以使用此命令"
            send.group(group_id, message)
            return message

    autowelgps_list = mjbutils.to_list(config_data.get("autowelgps", [])) if config_data else []
    gpwel_configs = config_data.get("gpwel_configs", {}) if config_data else {}

    if len(args) == 0:
        if str(group_id) in autowelgps_list:
            welcome_text = gpwel_configs.get(str(group_id), {}).get('welcome_text', '欢迎加入本群，请联系管理员使用"mjb.autogpwel set wel 内容"设置欢迎内容')
            message = f"当前群已开启入群欢迎功能\n欢迎内容: {welcome_text}"
        else:
            message = "当前群未开启入群欢迎功能，使用 'mjb.autogpwel on' 开启"
        send.group(group_id, message)
        return message

    subcmd = args[0].lower()

    if subcmd == 'on':
        try:
            if str(group_id) not in autowelgps_list:
                autowelgps_list.append(str(group_id))
                config_data["autowelgps"] = autowelgps_list

                if str(group_id) not in gpwel_configs:
                    gpwel_configs[str(group_id)] = {
                        'welcome_text': '欢迎加入本群，请联系管理员使用"mjb.autogpwel set wel 内容"设置欢迎内容'
                    }
                    config_data["gpwel_configs"] = gpwel_configs

                mjbconfig.save(config_data)
                mjbconfig.reload()
                message = "群入群欢迎功能已开启"
                send.group(group_id, message)
            else:
                message = "群入群欢迎功能已经是开启状态"
                send.group(group_id, message)
        except Exception as e:
            message = f"开启群入群欢迎功能失败: {e}"
            logger.error(f"开启群入群欢迎功能失败: {e}")
            send.group(group_id, message)

    elif subcmd == 'off':
        try:
            if str(group_id) in autowelgps_list:
                autowelgps_list.remove(str(group_id))
                config_data["autowelgps"] = autowelgps_list
                mjbconfig.save(config_data)
                mjbconfig.reload()
                message = "群入群欢迎功能已关闭"
                send.group(group_id, message)
            else:
                message = "群入群欢迎功能已经是关闭状态"
                send.group(group_id, message)
        except Exception as e:
            message = f"关闭群入群欢迎功能失败: {e}"
            logger.error(f"关闭群入群欢迎功能失败: {e}")
            send.group(group_id, message)

    elif subcmd == 'set' and len(args) >= 2:
        if args[1].lower() == 'wel' and len(args) >= 3:
            welcome_text = ' '.join(args[2:])
            if len(welcome_text) > 30:
                message = "欢迎内容不能超过30字"
                send.group(group_id, message)
            else:
                try:
                    if str(group_id) not in gpwel_configs:
                        gpwel_configs[str(group_id)] = {}
                    gpwel_configs[str(group_id)]['welcome_text'] = welcome_text
                    config_data["gpwel_configs"] = gpwel_configs
                    mjbconfig.save(config_data)
                    mjbconfig.reload()
                    message = f"群欢迎内容已设置为: {welcome_text}"
                    send.group(group_id, message)
                except Exception as e:
                    message = f"设置群欢迎内容失败: {e}"
                    logger.error(f"设置群欢迎内容失败: {e}")
                    send.group(group_id, message)
        else:
            message = "无效的参数，请使用 'mjb.autogpwel set wel 内容' 设置欢迎内容"
            send.group(group_id, message)
    else:
        message = "无效的参数，请使用 'mjb.autogpwel on/off' 或 'mjb.autogpwel set wel 内容'"
        send.group(group_id, message)

    return message


def cmd_autogprecall(group_id, user_id, config_data, *args):
    """群消息字数限制命令

    开启或关闭群消息字数限制功能，超过限制自动撤回并转为合并消息
    """
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    is_bot_admin = str(user_id) in admin_list

    is_group_admin = True

    if not is_bot_admin and not is_group_admin:
        message = "权限不足，只有Bot管理员或群管理员可以使用此命令"
        send.group(group_id, message)
        return message

    autorecallgps_list = mjbutils.to_list(config_data.get("autorecallgps", [])) if config_data else []
    gprecall_configs = config_data.get("gprecall_configs", {}) if config_data else {}

    args = list(args)
    if not args:
        if str(group_id) in autorecallgps_list:
            group_config = gprecall_configs.get(str(group_id), {})
            limit_count = group_config.get('count', 300)
            whitelist = group_config.get('whitelist', [])

            message = f"当前群已开启消息字数限制功能\n限制字数：{limit_count}字"
            if whitelist:
                message += f"\n白名单用户：{', '.join(whitelist)}"
            else:
                message += "\n白名单用户：无"

            send.group(group_id, message)
            return message
        else:
            message = "当前群未开启消息字数限制功能，使用 'mjb.autogprecall on' 开启"
            send.group(group_id, message)
            return message

    command = args[0].lower()

    if command == 'on':
        if str(group_id) not in autorecallgps_list:
            autorecallgps_list.append(str(group_id))
            config_data["autorecallgps"] = autorecallgps_list
            if str(group_id) not in gprecall_configs:
                gprecall_configs[str(group_id)] = {'count': 300}
                config_data["gprecall_configs"] = gprecall_configs
            mjbconfig.save(config_data)
            mjbconfig.reload()
            message = "消息字数限制功能已开启，默认限制字数为300字"
            send.group(group_id, message)
            return message
        else:
            message = "消息字数限制功能已开启"
            send.group(group_id, message)
            return message

    elif command == 'off':
        if str(group_id) in autorecallgps_list:
            autorecallgps_list.remove(str(group_id))
            config_data["autorecallgps"] = autorecallgps_list
            if str(group_id) in gprecall_configs:
                del gprecall_configs[str(group_id)]
                config_data["gprecall_configs"] = gprecall_configs
            mjbconfig.save(config_data)
            mjbconfig.reload()
            message = "消息字数限制功能已关闭"
            send.group(group_id, message)
            return message
        else:
            message = "消息字数限制功能未开启"
            send.group(group_id, message)
            return message

    elif command == 'set':
        if len(args) < 3:
            message = "参数不足，请使用 'mjb.autogprecall set count 数字' 格式"
            send.group(group_id, message)
            return message

        param = args[1].lower()
        value = args[2]

        if param != 'count':
            message = "不支持的参数，仅支持 'count' 参数"
            send.group(group_id, message)
            return message

        try:
            count_value = int(value)
            if count_value <= 0:
                message = "限制字数必须为正整数"
                send.group(group_id, message)
                return message
        except ValueError:
            message = "请输入有效的数字"
            send.group(group_id, message)
            return message

        if str(group_id) not in autorecallgps_list:
            autorecallgps_list.append(str(group_id))
            config_data["autorecallgps"] = autorecallgps_list

        if str(group_id) not in gprecall_configs:
            gprecall_configs[str(group_id)] = {}
        gprecall_configs[str(group_id)]['count'] = count_value
        config_data["gprecall_configs"] = gprecall_configs

        mjbconfig.save(config_data)
        mjbconfig.reload()
        message = f"已设置本群消息字数限制为 {count_value} 字"
        send.group(group_id, message)
        return message

    elif command == 'add' or command == 'del':
        if len(args) < 3:
            message = "参数不足，请使用 'mjb.autogprecall add white qq号' 或 'mjb.autogprecall del white qq号' 格式"
            send.group(group_id, message)
            return message

        subcommand = args[1].lower()
        if subcommand != 'white':
            message = "不支持的参数，仅支持 'white' 参数"
            send.group(group_id, message)
            return message

        qq_arg = args[2]
        extracted = mjbutils.extract_qq_from_at(qq_arg)
        try:
            qq_number = str(int(extracted))
        except ValueError:
            message = "请输入有效的QQ号或使用@功能"
            send.group(group_id, message)
            return message

        if str(group_id) not in gprecall_configs:
            gprecall_configs[str(group_id)] = {}

        if 'whitelist' not in gprecall_configs[str(group_id)]:
            gprecall_configs[str(group_id)]['whitelist'] = []

        whitelist = gprecall_configs[str(group_id)]['whitelist']

        if command == 'add':
            if qq_number in whitelist:
                message = f"用户 {qq_number} 已在白名单中"
            else:
                whitelist.append(qq_number)
                config_data["gprecall_configs"] = gprecall_configs
                mjbconfig.save(config_data)
                mjbconfig.reload()
                message = f"已添加用户 {qq_number} 到本群字数限制白名单"
        else:
            if qq_number in whitelist:
                whitelist.remove(qq_number)
                config_data["gprecall_configs"] = gprecall_configs
                mjbconfig.save(config_data)
                mjbconfig.reload()
                message = f"已从本群字数限制白名单中删除用户 {qq_number}"
            else:
                message = f"用户 {qq_number} 不在白名单中"

        send.group(group_id, message)
        return message

    else:
        message = "未知命令，请使用 'mjb.autogprecall [on/off]' 或 'mjb.autogprecall set count 数字' 或 'mjb.autogprecall add/del white qq号'"
        send.group(group_id, message)
        return message


# ---- 运维类业务命令（Bot 管理员专用，经 _builtin_commands 手动注册）----
def cmd_peekserver(group_id, user_id, config_data, *args):
    """截图服务器画面

    用法：peekserver
    功能：截图当前设备画面并发送到群，仅Bot管理员可用
    """
    # 检查管理员权限
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    is_bot_admin = str(user_id) in admin_list

    if not is_bot_admin:
        send.group(group_id, "权限不足，只有Bot管理员可以使用此命令")
        return "权限不足"

    try:
        import pyautogui

        # 生成临时图片文件
        file_path = mjbtmpfile.create(suffix=".png", prefix="peekserver_")
        if not file_path:
            send.group(group_id, "截图失败：无法创建临时文件")
            return "截图失败"

        logger.info(f"开始截图服务器画面，保存路径: {file_path}")

        # 使用pyautogui截图当前屏幕
        screenshot = pyautogui.screenshot()
        screenshot.save(file_path)
        logger.info(f"截图已保存为: {file_path}")

        # 发送图片到群
        success = send.group_image(group_id, file_path)

        # 清理临时图片文件
        mjbtmpfile.cleanup_now(file_path)

        if success:
            send.group(group_id, "服务器画面截图已完成")
            return "服务器画面截图已发送"
        else:
            send.group(group_id, "截图发送失败")
            return "截图发送失败"

    except ImportError as e:
        error_msg = f"截图功能依赖库缺失: {str(e)}"
        logger.error(error_msg)
        send.group(group_id, error_msg)
        return "依赖库缺失"
    except Exception as e:
        error_msg = f"截图失败: {str(e)}"
        logger.error(error_msg)
        send.group(group_id, error_msg)
        return "截图失败"


# 注册内置命令
_builtin_commands = {
    "help": cmd_help,
    "test": cmd_test,
    "echo": cmd_echo,
    "status": cmd_status,
    "notice": cmd_notice,
    "reboot": cmd_reboot,
    "reload": cmd_reload,
    "mod": cmd_mod,
    "botban": cmd_botban,
    "botunban": cmd_botunban,
    "event": cmd_event,
    # mjb.* 扩展命令
    "likeme": cmd_likeme,
    "poke": cmd_poke,
    "autogpsing": cmd_autogpsing,
    "closeingp": cmd_closeingp,
    "allowingp": cmd_allowingp,
    "fk": cmd_fk,
    "setnotice": cmd_setnotice,
    "allownotice": cmd_allownotice,
    "closenotice": cmd_closenotice,
    "autogpauth": cmd_autogpauth,
    "setauthok": cmd_setauthok,
    "autogpwel": cmd_autogpwel,
    "autogprecall": cmd_autogprecall,
    # 运维类业务命令（Bot 管理员专用）
    "peekserver": cmd_peekserver,
}
register_commands(_builtin_commands)
