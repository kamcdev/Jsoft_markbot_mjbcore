# -*- coding: utf-8 -*-
import re
import random
import threading
import time
from datetime import datetime

from bin import logger, mjbconfig, send, mjbc, mjbutils, worker
from bin import onebot_parse

# 钩子注册表
_interceptors = []          # 消息拦截器（有序）：fn(ctx) -> bool(是否已处理)
_at_bot_handlers = []       # @bot 处理器：fn(ctx) -> bool(是否已处理)
_notice_handlers = {}       # notice_type -> [fn(ctx)]

# QQ 心跳信息（多账号：bot_id -> info；None 键保留给无账号旧场景）
_heartbeat_lock = threading.Lock()
_heartbeat_info = {}  # bot_id -> {"online":bool,"interval":int,"timestamp":float,"raw_status":{}}


def _get_heartbeat_key(data):
    """从心跳事件数据中解析账号 key（优先 self_id，回退当前线程上下文）"""
    self_id = data.get("self_id")
    if self_id is not None:
        return str(self_id)
    cur = mjbconfig.get_current_bot_id()
    return cur  # 可能为 None（旧场景）


def register_interceptor(fn):
    """注册消息拦截器（按注册顺序执行；返回 True 表示已处理，终止管线）"""
    _interceptors.append(fn)


def register_at_bot_handler(fn):
    _at_bot_handlers.append(fn)


def register_notice_handler(notice_type, fn):
    _notice_handlers.setdefault(notice_type, []).append(fn)


def clear_handlers():
    """清空所有已注册的拦截器与通知处理器（供 mjb.reload 使用）"""
    _interceptors.clear()
    _at_bot_handlers.clear()
    _notice_handlers.clear()


def check_keyword_reply(group_id, message_content):
    """检测消息是否包含关键词并返回回复内容

    Returns:
        (是否回复, 回复内容)
    """
    gpevent_config = mjbconfig.load_gpevent()
    group_events = gpevent_config.get(str(group_id), {})
    if not group_events:
        return False, None

    matched_keywords = []
    for keyword, reply_content in group_events.items():
        if keyword in message_content:
            matched_keywords.append((keyword, reply_content))

    # 匹配多个关键词则不回复
    if len(matched_keywords) != 1:
        return False, None

    _, reply_content = matched_keywords[0]
    if isinstance(reply_content, list):
        return True, random.choice(reply_content)
    if isinstance(reply_content, str) and "//" in reply_content:
        replies = [r.strip() for r in reply_content.split("//") if r.strip()]
        return True, random.choice(replies) if replies else (True, reply_content)
    return True, str(reply_content)


def _handle_recall(ctx):
    """处理引用消息撤回功能: [CQ:reply,id=数字消息id]recall

    引用某条消息并发送"recall"时，Bot管理员或群管理员（群主/群管理员）
    可撤回被引用的消息。返回 True 表示已处理，终止后续管线。
    """
    raw_message = ctx.get("raw_message", "")
    if not raw_message:
        return False

    recall_pattern = r'\[CQ:reply,id=(-?\d+)\]recall'
    recall_match = re.search(recall_pattern, raw_message)
    if not recall_match:
        return False

    group_id = ctx["group_id"]
    user_id = ctx["user_id"]
    target_message_id = recall_match.group(1)

    # 权限检查：Bot管理员或群管理员（群主/群管理员）
    config_data = ctx.get("config_data") or {}
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    is_bot_admin = str(user_id) in admin_list
    is_group_admin = False
    try:
        member_role = send.get_group_member_role(group_id, user_id)
        is_group_admin = member_role in ('owner', 'admin')
    except Exception as e:
        logger.error(f"获取群成员身份时出错: {e}")

    if not (is_bot_admin or is_group_admin):
        logger.info(f"用户{user_id}非管理员，无权撤回消息，忽略")
        return True

    try:
        if send.delete_msg(target_message_id):
            send.group_at(group_id, user_id, f" 已撤回消息{target_message_id}")
            logger.info(f"用户{user_id}成功撤回群{group_id}中的消息{target_message_id}")
        else:
            send.group_at(group_id, user_id, " 撤回失败")
    except Exception as e:
        logger.error(f"撤回消息时出错: {e}")
        send.group_at(group_id, user_id, f" 撤回消息时出错: {str(e)}")
    return True


def _build_ctx(data, message_type):
    """从 OneBot 事件构建上下文"""
    sender = data.get("sender", {})
    return {
        "data": data,
        "message_type": message_type,
        "group_id": data.get("group_id"),
        "user_id": str(sender.get("user_id", "未知")),
        "username": sender.get("nickname", "未知"),
        "raw_message": data.get("raw_message", ""),
        "message_id": data.get("message_id", "未知"),
        "config_data": mjbconfig.get_config(),
        "segments": onebot_parse.parse_segments(data.get("message")),
    }


def handle_event(data):
    """Webhook 事件总入口

    账号上下文由 socket._safe_handle -> worker.submit 的 _run 统一管理
    （提交时快照调用方账号，任务运行时恢复，finally 还原）。
    本函数不再 set/clear 线程上下文，避免在异步任务/后台线程启动前过早清除，
    也避免与 worker.submit 的上下文管理产生冲突。
    """
    post_type = data.get("post_type")
    if post_type == "message":
        message_type = data.get("message_type", "未知")
        if message_type == "group":
            handle_group_message(data)
        elif message_type == "private":
            handle_private_message(data)
    elif post_type == "notice":
        handle_notice(data)
    elif post_type == "request":
        handle_request(data)
    elif post_type == "meta_event":
        handle_meta_event(data)
    return {"status": "ok"}


def handle_meta_event(data):
    """处理元事件（心跳），按账号分别更新连接状态供 WebUI 判断"""
    meta_event_type = data.get("meta_event_type", "")
    # 统一归一化解析（lifecycle 与未知元事件也在此被记录）
    try:
        _ev = onebot_parse.normalize_event(data)
        logger.debug(f"[事件] {_ev['event_name']} | {_ev['fields']}")
    except Exception as e:
        logger.error(f"元事件解析失败: {e}")
    if meta_event_type == "heartbeat":
        status = data.get("status", {})
        interval = data.get("interval", 0)
        key = _get_heartbeat_key(data)
        info_entry = {
            "online": status.get("online", True),
            "interval": interval,
            "timestamp": datetime.now().timestamp(),
            "raw_status": status,
        }
        with _heartbeat_lock:
            _heartbeat_info[key] = info_entry
        logger.debug(f"心跳事件[{key}]: online={info_entry['online']}, interval={interval}ms")


def _expire_heartbeat(info, bot_id=None):
    """对单个账号在线状态副本做超时检测

    heartbeat 模式：等待心跳包的超时时间由全局 heart_patience 决定；
    api 模式：不依赖心跳包，由轮询线程主动更新（此处不做超时判定）。
    """
    if bot_id is not None and mjbconfig.get_online_check(bot_id) == "api":
        return info  # api 轮询方案由轮询线程维护，超时由请求本身体现
    ts = info.get("timestamp", 0)
    if ts <= 0:
        info["online"] = False
        return info
    threshold = mjbconfig.get_heart_patience()
    if (datetime.now().timestamp() - ts) >= threshold:
        info["online"] = False
    return info


def get_heartbeat_info(bot_id=None):
    """获取指定账号在线状态副本（带超时检测）

    bot_id 为 None 时返回默认账号状态（向后兼容旧调用）。
    """
    if bot_id is None:
        bot_id = mjbconfig.get_default_bot_id()
    with _heartbeat_lock:
        info = _heartbeat_info.get(str(bot_id)) if bot_id is not None else None
        info = info.copy() if info else {}
    if not info:
        info = {"online": False, "interval": 0, "timestamp": 0, "raw_status": {}}
    return _expire_heartbeat(info, bot_id)


def get_all_heartbeat_info():
    """获取所有账号在线状态副本（带超时检测）

    Returns:
        dict: {bot_id: info}（key 为字符串账号 ID；可能包含 None 键）
    """
    with _heartbeat_lock:
        snapshot = {k: v.copy() for k, v in _heartbeat_info.items()}
    for k, v in snapshot.items():
        _expire_heartbeat(v, k)
    return snapshot


# ===================== api 轮询方案（online_check == "api"） =====================
_ONLINE_POLL_NAME = "online-check-poll"
_online_poll_stop = threading.Event()
_online_poll_thread = None
_online_poll_lock = threading.Lock()


def _poll_api_status(bot_id, patience):
    """对单个 api 模式账号请求 get_status 并写入在线状态副本（单次请求超时 = heart_patience）"""
    key = str(bot_id)
    try:
        resp = send._post("get_status", bot_id=bot_id, json={}, timeout=float(patience))
        try:
            result = resp.json()
        except Exception:
            result = None
    except Exception as e:
        logger.warning(f"账号[{key}] get_status 轮询失败: {e}")
        result = None
    data = result.get("data", {}) if isinstance(result, dict) else {}
    online = bool(data.get("online", False)) if isinstance(data, dict) else False
    info_entry = {
        "online": online,
        "interval": 0,
        "timestamp": time.time(),
        "raw_status": result if isinstance(result, dict) else {},
        "api": True,
    }
    with _heartbeat_lock:
        _heartbeat_info[key] = info_entry


def _online_check_poll_loop(stop_event):
    """api 模式账号在线状态轮询线程主循环

    每隔 heart_patience 秒，对所有 online_check == "api" 的账号请求 get_status，
    单次请求超时时间同为 heart_patience。
    """
    while not stop_event.is_set():
        patience = mjbconfig.get_heart_patience()
        for bid in mjbconfig.get_account_list():
            if stop_event.is_set():
                return
            if mjbconfig.get_online_check(bid) == "api":
                _poll_api_status(bid, patience)
        stop_event.wait(patience)


def start_online_check_polling():
    """启动 api 模式账号的在线状态轮询线程（若存在任一 api 账号）

    幂等：已在运行则跳过；无 api 账号则不启动。供程序启动与 mjb.reload 后调用。
    """
    global _online_poll_thread
    needs_poll = any(mjbconfig.get_online_check(b) == "api" for b in mjbconfig.get_account_list())
    if not needs_poll:
        return False
    with _online_poll_lock:
        if _online_poll_thread is not None and _online_poll_thread.is_alive():
            return False
        _online_poll_stop.clear()
        t = worker.start_background(
            _ONLINE_POLL_NAME, _online_check_poll_loop,
            stop_event=_online_poll_stop, stop_attr=None,
        )
        _online_poll_thread = t
    logger.info("已启动 api 在线状态轮询线程")
    return True


def stop_online_check_polling():
    """停止 api 在线状态轮询线程"""
    global _online_poll_thread
    with _online_poll_lock:
        _online_poll_stop.set()
        _online_poll_thread = None
    worker.stop_background(_ONLINE_POLL_NAME)


def handle_request(data):
    """处理请求事件（好友申请/加群申请），复用 notice 处理器机制"""
    request_type = data.get("request_type", "未知")
    logger.debug(f"请求事件 {request_type}")
    # 统一归一化解析并记录（零侵入，仅新增日志）
    try:
        _ev = onebot_parse.normalize_event(data)
        logger.info(f"[事件] {_ev['event_name']} | {_ev['fields']}")
    except Exception as e:
        logger.error(f"请求解析失败: {e}")
    # 双键查找：request_type(friend/group) 或 f"{request_type}_request"(friend_request/group_request)
    handlers = _notice_handlers.get(request_type, []) or _notice_handlers.get(f"{request_type}_request", [])
    for fn in handlers:
        try:
            fn(data)
        except Exception as e:
            logger.error(f"请求处理器({request_type})执行失败: {e}")


def handle_group_message(data):
    ctx = _build_ctx(data, "group")
    group_id = ctx["group_id"]
    user_id = ctx["user_id"]
    raw_message = ctx["raw_message"]
    config_data = ctx["config_data"]

    # 记录群-账号映射，供后台线程/定时任务中 send.group 无上下文时回退查找账号
    self_id = data.get("self_id")
    if self_id is not None and group_id is not None:
        mjbconfig.register_group_account(group_id, self_id)

    testmode = mjbconfig.get_testmode()
    testgp = mjbconfig.get_testgp()
    if testmode and str(group_id) != str(testgp):
        return

    logger.debug(f"群消息 群{group_id} {ctx['username']}({user_id}): {raw_message}")

    # 1. 消息拦截器（验证/dg/recall 等），按注册顺序执行
    for fn in _interceptors:
        try:
            if fn(ctx):
                return
        except Exception as e:
            logger.error(f"消息拦截器执行失败: {e}")

    # 2. @bot 检测
    bot_qq_numbers = []
    if config_data and "bqq" in config_data:
        bqq = config_data["bqq"]
        if isinstance(bqq, str):
            bot_qq_numbers = [q.strip() for q in bqq.split(",") if q.strip()]
        elif isinstance(bqq, list):
            bot_qq_numbers = [str(q) for q in bqq]
        else:
            bot_qq_numbers = [str(bqq)]

    is_at_bot = False
    at_content = ""
    for bot_qq in bot_qq_numbers:
        at_pattern = rf"\[CQ:at,qq={bot_qq}(?:,name=[^\]]*)?\]"
        if re.search(at_pattern, raw_message):
            is_at_bot = True
            at_content = re.sub(rf"\[CQ:at,qq={bot_qq}(?:,name=[^\]]*)?\]\s*", "", raw_message).strip()
            break
    ctx["is_at_bot"] = is_at_bot
    ctx["at_content"] = at_content

    # 3. 命令解析（首词匹配）
    command_part = raw_message.strip()
    special_commands = ["mjb.setnotice", "python.corun"]
    parts = command_part.split(maxsplit=1)
    if parts:
        cmd_name = parts[0].strip()
        if cmd_name in special_commands and len(parts) > 1:
            cmd_args = [parts[1]]
        else:
            cmd_args = [arg.strip() for arg in parts[1].split()] if len(parts) > 1 else []
    else:
        cmd_name = ""
        cmd_args = []
    ctx["cmd_name"] = cmd_name
    ctx["cmd_args"] = cmd_args

    # 4. @bot 处理（AI 等）
    if is_at_bot and at_content:
        banned_users = mjbconfig.load_banuser()
        if user_id in banned_users:
            logger.info(f"用户{user_id}在bot黑名单中，忽略其@机器人消息")
            return
        for fn in _at_bot_handlers:
            try:
                if fn(ctx):
                    return
            except Exception as e:
                logger.error(f"@bot处理器执行失败: {e}")
        return  # @bot 消息不再进入命令/关键词流程

    # 5. 黑名单检查（在命令分发前执行，与原 _ref 逻辑保持一致）
    banned_users = mjbconfig.load_banuser()
    if str(user_id) in banned_users:
        logger.debug(f"用户{user_id}在bot黑名单中，拦截命令分发")
        return

    # 6. 引用消息撤回（[CQ:reply,id=数字]recall，黑名单检查后执行，与原版顺序一致）
    if _handle_recall(ctx):
        return

    # 7. 命令分发
    if mjbc.dispatch(cmd_name, group_id, user_id, config_data, *cmd_args):
        return

    # 8. 关键词回复（未命中命令、未@bot）
    banned_users = mjbconfig.load_banuser()
    if user_id not in banned_users:
        should_reply, reply_content = check_keyword_reply(group_id, raw_message)
        if should_reply and reply_content:
            send.group(group_id, reply_content)


def handle_private_message(data):
    ctx = _build_ctx(data, "private")
    logger.debug(f"私聊消息 {ctx['username']}({ctx['user_id']}): {ctx['raw_message']}")


def handle_notice(data):
    notice_type = data.get("notice_type", "未知")
    group_id = data.get("group_id")
    logger.debug(f"通知事件 {notice_type} 群{group_id}")
    # 统一归一化解析并记录（零侵入，仅新增日志；未注册事件也不再静默丢弃）
    try:
        _ev = onebot_parse.normalize_event(data)
        logger.info(f"[事件] {_ev['event_name']} 群{group_id} | {_ev['fields']}")
    except Exception as e:
        logger.error(f"通知解析失败: {e}")
    handlers = _notice_handlers.get(notice_type, [])
    for fn in handlers:
        try:
            fn(data)
        except Exception as e:
            logger.error(f"通知处理器({notice_type})执行失败: {e}")
