# -*- coding: utf-8 -*-
from __future__ import annotations

SEGMENT_TYPES = (
    "text", "image", "video", "record", "file", "flash",
    "at", "reply", "json", "xml", "face", "mface", "markdown",
    "node", "forward", "music", "poke", "dice", "rps",
    "contact", "shake", "keyboard", "button",
)

SEGMENT_TYPES_SET = frozenset(SEGMENT_TYPES)


def _segment_text(seg_type, data):
    """生成某个消息段的可读文本表示（text 段为原文，其余为简短说明）"""
    try:
        if seg_type == "text":
            return str(data.get("text", ""))
        if seg_type == "at":
            qq = str(data.get("qq", ""))
            name = data.get("name")
            base = "[@全体成员]" if qq == "all" else (f"[@{qq}]" if qq else "[@]")
            return f"{base}{(' ' + str(name)) if name else ''}"
        if seg_type == "image":
            url = data.get("url") or data.get("file") or data.get("path")
            return f"[图片:{url}]" if url else "[图片]"
        if seg_type == "video":
            url = data.get("url") or data.get("file") or data.get("path")
            return f"[视频:{url}]" if url else "[视频]"
        if seg_type == "record":
            url = data.get("url") or data.get("file") or data.get("path")
            return f"[语音:{url}]" if url else "[语音]"
        if seg_type == "file":
            url = data.get("name") or data.get("file") or data.get("url")
            return f"[文件:{url}]" if url else "[文件]"
        if seg_type == "flash":
            title = data.get("title")
            return f"[闪传:{title}]" if title else "[闪传]"
        if seg_type == "reply":
            return f"[回复 {data.get('id', '')}]"
        if seg_type == "json":
            return "[JSON卡片]"
        if seg_type == "xml":
            return "[XML卡片]"
        if seg_type == "face":
            return f"[表情:{data.get('id', '')}]"
        if seg_type == "mface":
            return f"[商城表情:{data.get('summary', '') or data.get('emoji_id', '')}]"
        if seg_type == "markdown":
            return "[Markdown]"
        if seg_type == "node":
            name = data.get("name") or data.get("nickname")
            return f"[合并节点:{name}]" if name else "[合并节点]"
        if seg_type == "forward":
            return f"[合并转发:{data.get('id', '')}]"
        if seg_type == "music":
            title = data.get("title")
            return f"[音乐:{title}]" if title else "[音乐]"
        if seg_type == "poke":
            return "[戳一戳]"
        if seg_type == "dice":
            return f"[骰子:{data.get('result', '')}]"
        if seg_type == "rps":
            return f"[猜拳:{data.get('result', '')}]"
        if seg_type == "contact":
            return f"[联系:{data.get('type', '')}]"
        if seg_type == "shake":
            return "[窗口抖动]"
        if seg_type == "keyboard":
            return "[键盘按钮]"
        if seg_type == "button":
            return f"[按钮:{data.get('id', '')}]"
    except Exception:
        pass
    # 未知段类型：给一个简短占位，保留原始 type/data 由调用方处理
    return f"[段:{seg_type}]" if seg_type else ""


def parse_segments(msg_array):
    """解析消息段数组为结构化段列表

    入参为 OneBot 消息段数组（list，每项形如 {"type":..., "data":{...}}）。
    每个段返回 {"type":..., "data":..., "text":...}，其中 text 为该段可读文本。
    非 list 入参（字符串/None 等）返回 []，绝不抛异常；未知段类型原样透传。
    """
    if not isinstance(msg_array, list):
        return []
    result = []
    for seg in msg_array:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type", "")
        data = seg.get("data")
        data = dict(data) if isinstance(data, dict) else {}
        result.append({
            "type": seg_type,
            "data": data,
            "text": _segment_text(seg_type, data),
        })
    return result


def extract_plain_text(segments):
    """拼接所有 text 段的内容为纯文本"""
    parts = []
    for seg in segments or []:
        if isinstance(seg, dict) and seg.get("type") == "text":
            t = seg.get("data", {}).get("text")
            if isinstance(t, str):
                parts.append(t)
    return "".join(parts)


def extract_image_urls(segments):
    """提取所有 image 段的 url/file/path（去重保序，缺字段跳过）"""
    urls = []
    for seg in segments or []:
        if isinstance(seg, dict) and seg.get("type") == "image":
            data = seg.get("data", {}) or {}
            for key in ("url", "file", "path"):
                v = data.get(key)
                if isinstance(v, str) and v and v not in urls:
                    urls.append(v)
    return urls


def extract_at_user_ids(segments):
    """提取所有 at 段的 qq（排除 'all' 全体成员），返回字符串列表"""
    ids = []
    for seg in segments or []:
        if isinstance(seg, dict) and seg.get("type") == "at":
            qq = seg.get("data", {}).get("qq")
            if qq is not None and str(qq) != "all":
                ids.append(str(qq))
    return ids

NOTICE_TYPES = (
    "poke", "poke_recall", "friend_recall", "friend_add", "group_upload",
    "group_dismiss", "group_increase", "group_decrease", "group_admin",
    "group_ban", "group_recall", "group_card", "group_title",
    "msg_emoji_like", "essence", "flash_file", "notify", "friend_request",
)
NOTICE_TYPES_SET = frozenset(NOTICE_TYPES)

REQUEST_TYPES = ("friend", "group")
REQUEST_TYPES_SET = frozenset(REQUEST_TYPES)

META_EVENT_TYPES = ("heartbeat", "lifecycle")
META_EVENT_TYPES_SET = frozenset(META_EVENT_TYPES)


def _pick(data, keys):
    """从 dict 中提取存在的键，返回新 dict"""
    return {k: data[k] for k in keys if k in data}


def _msg_emoji_like_fields(data):
    """抽取 msg_emoji_like 的相关键，并把 operators 归一化为实体列表"""
    fields = _pick(data, ("sub_type", "group_id", "group_real_id", "message_id",
                          "message_seq", "user_id", "like_count", "count"))
    ops = data.get("operators")
    if isinstance(ops, list):
        fields["operators"] = [parse_sender(op) if isinstance(op, dict) else op for op in ops]
    elif ops is not None:
        fields["operators"] = ops
    return fields

_NOTICE_FIELDS = {
    "poke": ("group_id", "user_id", "target_id", "sender_id", "sub_type"),
    "poke_recall": ("group_id", "user_id", "target_id", "sender_id", "sub_type"),
    "friend_recall": ("user_id", "operation_id", "operator_id", "message_id", "raw_id"),
    "friend_add": ("user_id", "message"),
    "group_dismiss": ("group_id", "operator_id", "sub_type"),
    "group_increase": ("group_id", "user_id", "operator_id", "sub_type"),
    "group_decrease": ("group_id", "user_id", "operator_id", "sub_type"),
    "group_admin": ("group_id", "user_id", "sub_type"),
    "group_ban": ("group_id", "user_id", "operator_id", "duration", "sub_type"),
    "group_recall": ("group_id", "user_id", "operator_id", "message_id", "sub_type"),
    "group_card": ("group_id", "user_id", "card_new", "card_old"),
    "group_title": ("group_id", "user_id", "title_new", "title_old"),
    "essence": ("sub_type", "group_id", "user_id", "sender_id", "operator_id",
                "message_id", "message_type"),
    "msg_emoji_like": _msg_emoji_like_fields,
    "notify": ("group_id", "user_id", "sub_type", "target_id"),
    "friend_request": ("user_id", "nickname", "comment", "flag", "request_type"),
    # group_upload / flash_file 单独处理（需归一化 file 实体）
}

def _notice_field_extract(notice_type, data):
    """按 notice_type 抽取关键字段；未知类型返回整份 data 副本"""
    if notice_type == "group_upload":
        fields = _pick(data, ("group_id", "user_id", "sub_type"))
        if "file" in data:
            fields["file"] = parse_file(data["file"])
        return fields
    if notice_type == "flash_file":
        fields = _pick(data, ("group_id", "user_id", "sub_type"))
        if "file" in data:
            fields["file"] = parse_file(data["file"])
        return fields
    spec = _NOTICE_FIELDS.get(notice_type)
    if spec is None:
        return dict(data)
    if callable(spec):
        return spec(data)
    return _pick(data, spec)

def normalize_event(data):
    """归一化完整 OneBot 事件 dict

    返回 {"event_key": str, "event_name": str, "message_type": str, "fields": {...}}。
    - event_key：按 post_type 取 notice_type/request_type/message_type/meta_event_type。
    - event_name：带事件命名空间前缀，未知类型会在后标注 "(unknown)"。
    - fields：已知 notice 类型抽取关键字段，未知类型返回整份 data 副本。
    入参非 dict 或解析失败一律不抛异常。
    """
    if not isinstance(data, dict):
        return {"event_key": "", "event_name": "", "message_type": "", "fields": {}}

    post_type = data.get("post_type", "")
    result = {
        "event_key": "",
        "event_name": "",
        "message_type": str(data.get("message_type", "") or ""),
        "fields": {},
    }

    if post_type == "message":
        event_key = data.get("message_type", "")
        result["event_key"] = event_key
        result["event_name"] = f"message:{event_key}" if event_key else "message:(unknown)"
        result["message_type"] = event_key
        result["fields"] = dict(data)
    elif post_type == "notice":
        event_key = data.get("notice_type", "")
        result["event_key"] = event_key
        known = event_key in NOTICE_TYPES_SET
        label = event_key if event_key else "unknown"
        result["event_name"] = f"notice:{label}" + ("" if known else " (unknown)")
        result["message_type"] = "group" if data.get("group_id") is not None else ""
        result["fields"] = _notice_field_extract(event_key, data)
    elif post_type == "request":
        event_key = data.get("request_type", "")
        result["event_key"] = event_key
        known = event_key in REQUEST_TYPES_SET
        label = event_key if event_key else "unknown"
        result["event_name"] = f"request:{label}" + ("" if known else " (unknown)")
        result["message_type"] = "group" if data.get("group_id") is not None else ""
        result["fields"] = dict(data)
    elif post_type == "meta_event":
        event_key = data.get("meta_event_type", "")
        result["event_key"] = event_key
        known = event_key in META_EVENT_TYPES_SET
        label = event_key if event_key else "unknown"
        result["event_name"] = f"meta_event:{label}" + ("" if known else " (unknown)")
        result["fields"] = dict(data)
    else:
        result["event_name"] = f"unknown:{str(post_type) or ''}"
        result["fields"] = dict(data)

    return result

def parse_sender(sender):
    """规范化 MessageSender 实体（user_id/nickname/card/sex/age/role/title/level 等）

    非 dict 入参返回 {}；缺字段直接跳过。
    """
    if not isinstance(sender, dict):
        return {}
    keys = ("user_id", "nickname", "card", "sex", "age", "role", "title",
            "level", "area", "join_time", "last_sent_time", "unfriendly")
    return _pick(sender, keys)

def parse_file(f):
    """规范化文件对象（GroupUploadFile/FlashFile 等：name/size/url/path/busid/file 等）

    非 dict 入参返回 {}；缺字段直接跳过。
    """
    if not isinstance(f, dict):
        return {}
    keys = ("name", "size", "url", "path", "busid", "file", "file_id", "id")
    return _pick(f, keys)

def parse_msg_emoji_like(fields):
    """规范化"表情回应"通知的负载字段

    字段以实际上报为准（subject_message_id/count/from_current_bot/operators 等），
    非 dict 入参返回 {}；缺字段跳过；operators 规整为 sender 实体列表。
    """
    if not isinstance(fields, dict):
        return {}
    result = {}
    for key in ("subject_message_id", "subject_message_seq", "count", "from_current_bot",
                "group_id", "user_id", "message_id", "like_count"):
        if key in fields:
            result[key] = fields[key]
    ops = fields.get("operators")
    if isinstance(ops, list):
        result["operators"] = [parse_sender(op) if isinstance(op, dict) else op for op in ops]
    elif ops is not None:
        result["operators"] = ops
    return result

def parse_file_entity(entity):
    """归一化文件实体（id/file_id/name/url/path/size/busid/file 等，缺字段跳过）"""
    if not isinstance(entity, dict):
        return {}
    keys = ("id", "file_id", "name", "url", "path", "size", "busid", "file", "parent", "own")
    return _pick(entity, keys)

def parse_folder_entity(entity):
    """归一化文件夹实体（group_id/folder_id/folder_name/total_count/current_page 等）"""
    if not isinstance(entity, dict):
        return {}
    keys = ("group_id", "folder_id", "folder_name", "total_count", "current_page", "files")
    return _pick(entity, keys)