# -*- coding: utf-8 -*-
import os
import requests

from bin import logger, mjbconfig


def _resolve_bot(bot_id=None, group_id=None):
    """解析目标账号：显式 bot_id > （无线程上下文时）group_id 映射 > 当前上下文/默认账号

    bot_id 为 None 且当前线程无账号上下文时，根据 group_id 查找关联账号
    （用于后台线程/定时任务中模块无上下文调用 send.group 的回退场景）
    """
    if bot_id is None and group_id is not None:
        if mjbconfig.get_current_bot_id() is None:
            mapped = mjbconfig.get_bot_id_by_group(str(group_id))
            if mapped:
                bot_id = mapped
    return bot_id


def _url(action, bot_id=None, group_id=None):
    """构造 OnebotQQ API 地址"""
    return f"{mjbconfig.get_Onebot_url(_resolve_bot(bot_id, group_id))}/{action}"


def _headers(bot_id=None, group_id=None):
    """按账号配置构造请求头：设置了 send_token 时附带 Authorization: Bearer <token>"""
    token = mjbconfig.get_send_token(_resolve_bot(bot_id, group_id))
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _post(action, bot_id=None, group_id=None, **kwargs):
    """统一 API POST 入口：按账号自动附带 token（未配置 token 时不携带）"""
    return requests.post(
        _url(action, bot_id, group_id),
        headers=_headers(bot_id, group_id),
        **kwargs
    )


def api(action, bot_id=None, **payload):
    """通用 OnebotQQ HTTP API 调用"""
    try:
        group_id = payload.get("group_id")
        response = _post(action, bot_id, group_id, json=payload, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"调用 API {action} 失败: {e}")
        return {"status": "error", "message": str(e)}


def group(group_id, message, bot_id=None):
    """发送群文本消息"""
    try:
        if group_id == 0:
            return
        _post("send_group_msg", bot_id, group_id, json={
            "group_id": group_id,
            "message": [{"type": "text", "data": {"text": message}}],
        }, timeout=15)
    except Exception as e:
        logger.error(f"发送群消息时出错: {e}")


def group_at(group_id, qq_number, text_message="", bot_id=None):
    """发送群聊 @ 消息"""
    try:
        if group_id == 0:
            return
        message_content = [{"type": "at", "data": {"qq": str(qq_number)}}]
        if text_message:
            message_content.append({"type": "text", "data": {"text": text_message}})
        _post("send_group_msg", bot_id, group_id, json={
            "group_id": group_id,
            "message": message_content,
        }, timeout=15)
    except Exception as e:
        logger.error(f"发送群@消息时出错: {e}")


def group_reply(group_id, user_id, message_id, content, bot_id=None):
    """发送引用消息回复"""
    try:
        message_content = [
            {"type": "reply", "data": {"id": int(message_id)}},
            {"type": "at", "data": {"qq": int(user_id)}},
            {"type": "text", "data": {"text": f" {content}"}},
        ]
        _post("send_group_msg", bot_id, group_id, json={
            "group_id": int(group_id),
            "message": message_content,
        }, timeout=15)
        logger.info(f"引用消息发送成功: 群{group_id}，回复消息{message_id}")
        return True
    except Exception as e:
        logger.error(f"发送引用消息失败: {e}")
        return False


def group_image(group_id, image_path, bot_id=None):
    """发送群聊图片消息"""
    try:
        if not group_id or group_id == 0:
            logger.error(f"错误：无效的群号: {group_id}")
            return False
        if not os.path.exists(image_path):
            logger.error(f"错误：图片文件不存在: {image_path}")
            return False
        if os.path.getsize(image_path) == 0:
            logger.error(f"错误：图片文件为空: {image_path}")
            return False

        abs_image_path = os.path.abspath(image_path)
        payload = {
            "group_id": int(group_id) if isinstance(group_id, str) else group_id,
            "message": [{"type": "image", "data": {"file": abs_image_path}}],
        }
        response = _post("send_group_msg", bot_id, group_id, json=payload, timeout=15)
        response.raise_for_status()
        logger.debug(f"图片消息发送成功，响应: {response.json()}")
        return True
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP错误: {e}")
        return False
    except requests.exceptions.ConnectionError:
        logger.error("连接错误，可能是bot服务未运行或端口错误")
        return False
    except requests.exceptions.Timeout:
        logger.error("请求超时")
        return False
    except Exception as e:
        logger.error(f"发送群图片消息时出错: {e}")
        return False


def private(user_id, content, bot_id=None):
    """发送私聊文本消息"""
    try:
        payload = {
            "user_id": int(user_id),
            "message": [{"type": "text", "data": {"text": content}}],
        }
        response = _post("send_private_msg", bot_id, json=payload, timeout=15)
        response.raise_for_status()
        logger.info(f"私聊消息发送成功: 用户{user_id}")
        return True
    except Exception as e:
        logger.error(f"发送私聊消息失败: {e}")
        return False


def send_group_forward_msg(group_id, messages, fake_qq=None, fake_name=None, bot_id=None):
    """发送群聊合并转发消息

    Args:
        messages: 字符串列表或含 text/file 的字典列表
        fake_qq/fake_name: 伪造的 QQ 号/昵称
    """
    try:
        content_items = []
        for msg in messages:
            if isinstance(msg, str):
                content_items.append({"type": "text", "data": {"text": msg}})
            elif isinstance(msg, dict) and "text" in msg:
                content_items.append({"type": "text", "data": {"text": msg["text"]}})
            elif isinstance(msg, dict) and "file" in msg:
                content_items.append({"type": "image", "data": {"file": msg["file"]}})
        node_data = {"type": "node", "data": {"content": content_items}}
        if fake_qq:
            node_data["data"]["uin"] = fake_qq
        if fake_name:
            node_data["data"]["name"] = fake_name
        response = _post("send_group_forward_msg", bot_id, group_id, json={
            "group_id": group_id,
            "messages": [node_data],
        }, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"发送合并转发消息时出错: {e}")
        return {"status": "error", "message": str(e)}


def send_group_file(group_id, file_path, file_name=None, folder_id=None, bot_id=None):
    """发送群文件

    Returns:
        tuple: (是否成功, 错误信息或"成功")
    """
    try:
        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return (False, "文件不存在")
        if not file_name:
            file_name = os.path.basename(file_path)
        payload = {"group_id": int(group_id), "file": file_path, "name": file_name}
        if folder_id:
            payload["folder_id"] = folder_id
        response = _post("upload_group_file", bot_id, group_id, json=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            if result.get("status") == "ok":
                logger.info(f"群文件发送成功到群{group_id}: {file_name}")
                return (True, "成功")
            return (False, result.get("message", "未知错误"))
        return (False, f"HTTP状态码: {response.status_code}")
    except requests.exceptions.Timeout:
        logger.error("群文件发送超时")
        return (False, "请求超时")
    except Exception as e:
        logger.error(f"发送群文件失败: {e}")
        return (False, str(e))


# ---- OnebotQQ API 封装 ----
def get_group_member_role(group_id, user_id, bot_id=None):
    """获取群成员身份：owner/admin/member/unknown"""
    try:
        payload = {"group_id": int(group_id), "user_id": int(user_id), "no_cache": False}
        response = _post("get_group_member_info", bot_id, group_id, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok" and "data" in data:
                return data["data"].get("role", "unknown")
        return "unknown"
    except Exception as e:
        logger.error(f"获取群成员身份时出错: {e}")
        return "unknown"


def get_group_member_info(group_id, user_id, bot_id=None):
    """获取群成员信息（完整 dict）"""
    try:
        response = _post("get_group_member_info", bot_id, group_id, json={
            "group_id": int(group_id), "user_id": int(user_id),
        }, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok" and "data" in data:
                return data["data"]
        return {}
    except Exception as e:
        logger.error(f"获取群成员信息失败: {e}")
        return {}


def get_group_member_list(group_id, bot_id=None):
    """获取群成员列表"""
    try:
        response = _post("get_group_member_list", bot_id, group_id, json={
            "group_id": group_id,
        }, timeout=10)
        result = response.json()
        if result.get("status") == "ok" and "data" in result:
            return result["data"]
        return []
    except Exception as e:
        logger.error(f"获取群成员列表时发生异常: {e}")
        return []


def get_stranger_info(user_id, bot_id=None):
    """获取用户信息"""
    try:
        response = _post("get_stranger_info", bot_id, json={
            "user_id": user_id,
        }, timeout=10)
        result = response.json()
        if result.get("status") == "ok" and "data" in result:
            return result["data"]
        return {}
    except Exception as e:
        logger.error(f"获取用户信息时发生异常: {e}")
        return {}


def delete_msg(message_id, bot_id=None):
    """撤回消息"""
    try:
        _post("delete_msg", bot_id, json={"message_id": int(message_id)}, timeout=5)
        return True
    except Exception as e:
        logger.error(f"撤回消息失败: {e}")
        return False


def set_group_kick(group_id, user_id, reject_add_request=False, bot_id=None):
    """踢出群成员"""
    try:
        _post("set_group_kick", bot_id, group_id, json={
            "group_id": int(group_id), "user_id": int(user_id),
            "reject_add_request": reject_add_request,
        }, timeout=5)
        logger.info(f"已将用户{user_id}从群{group_id}踢出")
        return True
    except Exception as e:
        logger.error(f"踢人失败: {e}")
        return False


def set_group_ban(group_id, user_id, duration=0, bot_id=None):
    """禁言成员（duration 秒，0 表示解除）"""
    try:
        _post("set_group_ban", bot_id, group_id, json={
            "group_id": int(group_id), "user_id": int(user_id),
            "duration": int(duration),
        }, timeout=5)
        return True
    except Exception as e:
        logger.error(f"禁言失败: {e}")
        return False


def send_like(user_id, times=10, bot_id=None):
    """点赞"""
    try:
        return api("send_like", user_id=int(user_id), times=times, bot_id=bot_id)
    except Exception as e:
        logger.error(f"点赞失败: {e}")
        return {"status": "error", "message": str(e)}


def send_poke(group_id, user_id, bot_id=None):
    """戳一戳"""
    try:
        return api("group_poke", bot_id=bot_id, group_id=int(group_id), user_id=int(user_id))
    except Exception as e:
        logger.error(f"戳一戳失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_special_title(group_id, user_id, special_title="", bot_id=None):
    """设置群成员专属头衔（special_title 为空字符串表示去掉群头衔）"""
    try:
        return api("set_group_special_title", group_id=int(group_id),
                   user_id=int(user_id), special_title=special_title, bot_id=bot_id)
    except Exception as e:
        logger.error(f"设置群头衔失败: {e}")
        return {"status": "error", "message": str(e)}


def set_essence_msg(message_id, bot_id=None):
    """设置群精华消息"""
    try:
        return api("set_essence_msg", message_id=int(message_id), bot_id=bot_id)
    except Exception as e:
        logger.error(f"设置群精华消息失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- Onebot11 消息：通用段式发送 ----
def _normalize_message(message):
    """将字符串消息包装为文本消息段数组，消息段数组原样返回"""
    if isinstance(message, str):
        return [{"type": "text", "data": {"text": message}}]
    return message


def send_group_msg(group_id, message, bot_id=None):
    """发送群聊消息（message 支持字符串或消息段数组）"""
    try:
        return api("send_group_msg", bot_id=bot_id, group_id=group_id,
                   message=_normalize_message(message))
    except Exception as e:
        logger.error(f"发送群聊消息失败: {e}")
        return {"status": "error", "message": str(e)}


def send_private_msg(user_id, message, bot_id=None):
    """发送私聊消息（message 支持字符串或消息段数组）"""
    try:
        return api("send_private_msg", bot_id=bot_id, user_id=user_id,
                   message=_normalize_message(message))
    except Exception as e:
        logger.error(f"发送私聊消息失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- Onebot11 消息：语音/视频 ----
def group_record(group_id, file, bot_id=None):
    """发送群聊语音消息（file 支持本地路径/URL/base64://）"""
    try:
        return api("send_group_msg", bot_id=bot_id, group_id=group_id,
                   message=[{"type": "record", "data": {"file": file}}])
    except Exception as e:
        logger.error(f"发送群聊语音消息失败: {e}")
        return {"status": "error", "message": str(e)}


def private_record(user_id, file, bot_id=None):
    """发送私聊语音消息（file 支持本地路径/URL/base64://）"""
    try:
        return api("send_private_msg", bot_id=bot_id, user_id=user_id,
                   message=[{"type": "record", "data": {"file": file}}])
    except Exception as e:
        logger.error(f"发送私聊语音消息失败: {e}")
        return {"status": "error", "message": str(e)}


def group_video(group_id, file, bot_id=None):
    """发送群聊视频消息（视频不能超过 100M）"""
    try:
        return api("send_group_msg", bot_id=bot_id, group_id=group_id,
                   message=[{"type": "video", "data": {"file": file}}])
    except Exception as e:
        logger.error(f"发送群聊视频消息失败: {e}")
        return {"status": "error", "message": str(e)}


def private_video(user_id, file, bot_id=None):
    """发送私聊视频消息（视频不能超过 100M）"""
    try:
        return api("send_private_msg", bot_id=bot_id, user_id=user_id,
                   message=[{"type": "video", "data": {"file": file}}])
    except Exception as e:
        logger.error(f"发送私聊视频消息失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- Onebot11 消息：系统表情/超级表情/骰子/猜拳 ----
def group_face(group_id, face_id, bot_id=None):
    """发送群聊系统表情"""
    try:
        return api("send_group_msg", bot_id=bot_id, group_id=group_id,
                   message=[{"type": "face", "data": {"id": int(face_id)}}])
    except Exception as e:
        logger.error(f"发送群聊系统表情失败: {e}")
        return {"status": "error", "message": str(e)}


def private_face(user_id, face_id, bot_id=None):
    """发送私聊系统表情"""
    try:
        return api("send_private_msg", bot_id=bot_id, user_id=user_id,
                   message=[{"type": "face", "data": {"id": int(face_id)}}])
    except Exception as e:
        logger.error(f"发送私聊系统表情失败: {e}")
        return {"status": "error", "message": str(e)}


def group_mface(group_id, emoji_id, emoji_package_id=0, key="", summary="", bot_id=None):
    """发送群聊商城表情（超级表情）"""
    try:
        return api("send_group_msg", bot_id=bot_id, group_id=group_id, message=[{
            "type": "mface",
            "data": {"emoji_id": emoji_id, "emoji_package_id": emoji_package_id,
                     "key": key, "summary": summary},
        }])
    except Exception as e:
        logger.error(f"发送群聊商城表情失败: {e}")
        return {"status": "error", "message": str(e)}


def private_mface(user_id, emoji_id, emoji_package_id=0, key="", summary="", bot_id=None):
    """发送私聊商城表情（超级表情）"""
    try:
        return api("send_private_msg", bot_id=bot_id, user_id=user_id, message=[{
            "type": "mface",
            "data": {"emoji_id": emoji_id, "emoji_package_id": emoji_package_id,
                     "key": key, "summary": summary},
        }])
    except Exception as e:
        logger.error(f"发送私聊商城表情失败: {e}")
        return {"status": "error", "message": str(e)}


def group_dice(group_id, result=None, bot_id=None):
    """发送群聊骰子（result 指定点数 1-6，仅部分实现支持）"""
    try:
        data = {} if result is None else {"result": int(result)}
        return api("send_group_msg", bot_id=bot_id, group_id=group_id,
                   message=[{"type": "dice", "data": data}])
    except Exception as e:
        logger.error(f"发送群聊骰子失败: {e}")
        return {"status": "error", "message": str(e)}


def private_dice(user_id, result=None, bot_id=None):
    """发送私聊骰子（result 指定点数 1-6，仅部分实现支持）"""
    try:
        data = {} if result is None else {"result": int(result)}
        return api("send_private_msg", bot_id=bot_id, user_id=user_id,
                   message=[{"type": "dice", "data": data}])
    except Exception as e:
        logger.error(f"发送私聊骰子失败: {e}")
        return {"status": "error", "message": str(e)}


def group_rps(group_id, bot_id=None):
    """发送群聊猜拳"""
    try:
        return api("send_group_msg", bot_id=bot_id, group_id=group_id,
                   message=[{"type": "rps", "data": {}}])
    except Exception as e:
        logger.error(f"发送群聊猜拳失败: {e}")
        return {"status": "error", "message": str(e)}


def private_rps(user_id, bot_id=None):
    """发送私聊猜拳"""
    try:
        return api("send_private_msg", bot_id=bot_id, user_id=user_id,
                   message=[{"type": "rps", "data": {}}])
    except Exception as e:
        logger.error(f"发送私聊猜拳失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- Onebot11 消息：音乐卡片/JSON 卡片 ----
def group_music(group_id, music_type, music_id=None, url=None, audio=None,
                title=None, image=None, bot_id=None):
    """发送群聊音乐卡片（music_type: qq/163/custom；custom 需 url/audio/title/image）"""
    try:
        data = {"type": music_type}
        if music_id is not None:
            data["id"] = int(music_id)
        for name, value in (("url", url), ("audio", audio), ("title", title), ("image", image)):
            if value is not None:
                data[name] = value
        return api("send_group_msg", bot_id=bot_id, group_id=group_id,
                   message=[{"type": "music", "data": data}])
    except Exception as e:
        logger.error(f"发送群聊音乐卡片失败: {e}")
        return {"status": "error", "message": str(e)}


def private_music(user_id, music_type, music_id=None, url=None, audio=None,
                  title=None, image=None, bot_id=None):
    """发送私聊音乐卡片（music_type: qq/163/custom；custom 需 url/audio/title/image）"""
    try:
        data = {"type": music_type}
        if music_id is not None:
            data["id"] = int(music_id)
        for name, value in (("url", url), ("audio", audio), ("title", title), ("image", image)):
            if value is not None:
                data[name] = value
        return api("send_private_msg", bot_id=bot_id, user_id=user_id,
                   message=[{"type": "music", "data": data}])
    except Exception as e:
        logger.error(f"发送私聊音乐卡片失败: {e}")
        return {"status": "error", "message": str(e)}


def group_json(group_id, data, bot_id=None):
    """发送群聊卡片(json)消息"""
    try:
        return api("send_group_msg", bot_id=bot_id, group_id=group_id,
                   message=[{"type": "json", "data": {"data": data}}])
    except Exception as e:
        logger.error(f"发送群聊卡片消息失败: {e}")
        return {"status": "error", "message": str(e)}


def private_json(user_id, data, bot_id=None):
    """发送私聊卡片(json)消息"""
    try:
        return api("send_private_msg", bot_id=bot_id, user_id=user_id,
                   message=[{"type": "json", "data": {"data": data}}])
    except Exception as e:
        logger.error(f"发送私聊卡片消息失败: {e}")
        return {"status": "error", "message": str(e)}


def send_private_forward_msg(user_id, messages, fake_qq=None, fake_name=None, bot_id=None):
    """发送私聊合并转发消息

    Args:
        messages: 字符串列表或含 text/file 的字典列表
        fake_qq/fake_name: 伪造的 QQ 号/昵称
    """
    try:
        content_items = []
        for msg in messages:
            if isinstance(msg, str):
                content_items.append({"type": "text", "data": {"text": msg}})
            elif isinstance(msg, dict) and "text" in msg:
                content_items.append({"type": "text", "data": {"text": msg["text"]}})
            elif isinstance(msg, dict) and "file" in msg:
                content_items.append({"type": "image", "data": {"file": msg["file"]}})
        node_data = {"type": "node", "data": {"content": content_items}}
        if fake_qq:
            node_data["data"]["uin"] = fake_qq
        if fake_name:
            node_data["data"]["name"] = fake_name
        return api("send_private_forward_msg", bot_id=bot_id, user_id=user_id,
                   messages=[node_data])
    except Exception as e:
        logger.error(f"发送私聊合并转发消息失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- Onebot11 消息：转发/查询/操作 ----
def forward_friend_single_msg(user_id, message_id, bot_id=None):
    """转发单条好友消息"""
    try:
        return api("forward_friend_single_msg", bot_id=bot_id,
                   user_id=int(user_id), message_id=int(message_id))
    except Exception as e:
        logger.error(f"转发单条好友消息失败: {e}")
        return {"status": "error", "message": str(e)}


def forward_group_single_msg(group_id, message_id, bot_id=None):
    """转发单条群消息"""
    try:
        return api("forward_group_single_msg", bot_id=bot_id,
                   group_id=int(group_id), message_id=int(message_id))
    except Exception as e:
        logger.error(f"转发单条群消息失败: {e}")
        return {"status": "error", "message": str(e)}


def get_msg(message_id, bot_id=None):
    """获取消息详情"""
    try:
        return api("get_msg", bot_id=bot_id, message_id=int(message_id))
    except Exception as e:
        logger.error(f"获取消息详情失败: {e}")
        return {"status": "error", "message": str(e)}


def get_file(file, download=True, bot_id=None):
    """获取消息文件详情（file 为收到的文件名）"""
    try:
        return api("get_file", bot_id=bot_id, file=file, download=download)
    except Exception as e:
        logger.error(f"获取消息文件详情失败: {e}")
        return {"status": "error", "message": str(e)}


def get_image(file, bot_id=None):
    """获取消息图片详情（file 为收到的图片文件名）"""
    try:
        return api("get_image", bot_id=bot_id, file=file)
    except Exception as e:
        logger.error(f"获取消息图片详情失败: {e}")
        return {"status": "error", "message": str(e)}


def get_record(file, out_format="mp3", bot_id=None):
    """获取消息语音详情（out_format 支持 mp3/amr/wma/m4a/spx/ogg/wav/flac）"""
    try:
        return api("get_record", bot_id=bot_id, file=file, out_format=out_format)
    except Exception as e:
        logger.error(f"获取消息语音详情失败: {e}")
        return {"status": "error", "message": str(e)}


def set_msg_emoji_like(message_id, emoji_id, set=True, bot_id=None):
    """表情回应消息（只支持群聊消息）"""
    try:
        return api("set_msg_emoji_like", bot_id=bot_id,
                   message_id=int(message_id), emoji_id=int(emoji_id), set=set)
    except Exception as e:
        logger.error(f"表情回应消息失败: {e}")
        return {"status": "error", "message": str(e)}


def fetch_emoji_like(message_id, emoji_id, count=20, cookie=None, bot_id=None):
    """获取表情回应详情"""
    try:
        payload = {"message_id": int(message_id), "emoji_id": int(emoji_id), "count": count}
        if cookie is not None:
            payload["cookie"] = cookie
        return api("fetch_emoji_like", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"获取表情回应详情失败: {e}")
        return {"status": "error", "message": str(e)}


def get_friend_msg_history(user_id, message_seq=0, count=20, reverse_order=False, bot_id=None):
    """获取好友历史消息记录"""
    try:
        return api("get_friend_msg_history", bot_id=bot_id, user_id=int(user_id),
                   message_seq=message_seq, count=count, reverseOrder=reverse_order)
    except Exception as e:
        logger.error(f"获取好友历史消息记录失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_msg_history(group_id, message_seq=0, count=20, reverse_order=False, bot_id=None):
    """获取群历史消息（message_seq 填 0 表示从最新开始）"""
    try:
        return api("get_group_msg_history", bot_id=bot_id, group_id=int(group_id),
                   message_seq=message_seq, count=count, reverseOrder=reverse_order)
    except Exception as e:
        logger.error(f"获取群历史消息失败: {e}")
        return {"status": "error", "message": str(e)}


def get_forward_msg(message_id, bot_id=None):
    """获取转发消息详情（message_id 为长 id）"""
    try:
        return api("get_forward_msg", bot_id=bot_id, message_id=message_id)
    except Exception as e:
        logger.error(f"获取转发消息详情失败: {e}")
        return {"status": "error", "message": str(e)}


def mark_msg_as_read(message_id, bot_id=None):
    """标记消息已读"""
    try:
        return api("mark_msg_as_read", bot_id=bot_id, message_id=int(message_id))
    except Exception as e:
        logger.error(f"标记消息已读失败: {e}")
        return {"status": "error", "message": str(e)}


def voice_msg_to_text(message_id, bot_id=None):
    """语音消息转文字（需要 LLOneBot 5.1 及以上版本）"""
    try:
        return api("voice_msg_to_text", bot_id=bot_id, message_id=int(message_id))
    except Exception as e:
        logger.error(f"语音消息转文字失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- Onebot11 消息：AI 语音与戳一戳 ----
def send_group_ai_record(group_id, character, text, bot_id=None):
    """发送群 AI 语音（character 为语音声色，需要 LLOneBot 5.6.1 及以上版本）"""
    try:
        return api("send_group_ai_record", bot_id=bot_id, group_id=int(group_id),
                   character=character, text=text)
    except Exception as e:
        logger.error(f"发送群AI语音失败: {e}")
        return {"status": "error", "message": str(e)}


def get_ai_characters(group_id=None, chat_type=1, bot_id=None):
    """获取群 AI 语音可用声色列表（需要 LLOneBot 5.6.1 及以上版本）"""
    try:
        payload = {"chat_type": chat_type}
        if group_id is not None:
            payload["group_id"] = int(group_id)
        return api("get_ai_characters", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"获取群AI语音声色列表失败: {e}")
        return {"status": "error", "message": str(e)}


def private_poke(user_id, target_id=None, bot_id=None):
    """私聊戳一戳（发送戳一戳，action send_poke，需 LLBot 7.11.3+；target_id 为目标 QQ 号，仅私聊生效）"""
    try:
        payload = {"user_id": int(user_id)}
        if target_id is not None:
            payload["target_id"] = int(target_id)
        return api("send_poke", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"私聊戳一戳失败: {e}")
        return {"status": "error", "message": str(e)}


def friend_poke(user_id, bot_id=None):
    """好友戳一戳（双击头像，action friend_poke）"""
    try:
        return api("friend_poke", bot_id=bot_id, user_id=int(user_id))
    except Exception as e:
        logger.error(f"好友戳一戳失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- OneBot11 用户：好友列表/资料 ----
def get_friend_list(no_cache=None, bot_id=None):
    """获取好友列表（no_cache=True 时强制刷新缓存）"""
    try:
        payload = {}
        if no_cache is not None:
            payload["no_cache"] = no_cache
        return api("get_friend_list", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"获取好友列表失败: {e}")
        return {"status": "error", "message": str(e)}


def get_friends_with_category(bot_id=None):
    """获取好友列表（带分组）"""
    try:
        return api("get_friends_with_category", bot_id=bot_id)
    except Exception as e:
        logger.error(f"获取带分组好友列表失败: {e}")
        return {"status": "error", "message": str(e)}


def delete_friend(user_id, bot_id=None):
    """删除好友"""
    try:
        return api("delete_friend", bot_id=bot_id, user_id=int(user_id))
    except Exception as e:
        logger.error(f"删除好友失败: {e}")
        return {"status": "error", "message": str(e)}


def set_friend_add_request(flag, approve=True, remark="", bot_id=None):
    """处理好友申请（flag 为请求 id，approve 是否同意，remark 为好友备注）"""
    try:
        return api("set_friend_add_request", bot_id=bot_id,
                   flag=flag, approve=approve, remark=remark)
    except Exception as e:
        logger.error(f"处理好友申请失败: {e}")
        return {"status": "error", "message": str(e)}


def set_friend_remark(user_id, remark, bot_id=None):
    """设置好友备注"""
    try:
        return api("set_friend_remark", bot_id=bot_id, user_id=int(user_id), remark=remark)
    except Exception as e:
        logger.error(f"设置好友备注失败: {e}")
        return {"status": "error", "message": str(e)}


def set_qq_avatar(file, bot_id=None):
    """设置个人头像（file 支持 file:// / http:// / base64:// 三种形式）"""
    try:
        return api("set_qq_avatar", bot_id=bot_id, file=file)
    except Exception as e:
        logger.error(f"设置个人头像失败: {e}")
        return {"status": "error", "message": str(e)}


def get_profile_like(start=0, count=20, bot_id=None):
    """获取我赞过谁列表（start 从 0 开始，-1 表示获取全部；count 最多 30）"""
    try:
        return api("get_profile_like", bot_id=bot_id, start=start, count=count)
    except Exception as e:
        logger.error(f"获取我赞过谁列表失败: {e}")
        return {"status": "error", "message": str(e)}


def get_profile_like_me(start=0, count=20, bot_id=None):
    """获取谁赞过我列表（start 从 0 开始，-1 表示获取全部；count 最多 30）"""
    try:
        return api("get_profile_like_me", bot_id=bot_id, start=start, count=count)
    except Exception as e:
        logger.error(f"获取谁赞过我列表失败: {e}")
        return {"status": "error", "message": str(e)}


def get_profile_like_count(user_id, bot_id=None):
    """获取名片赞数量（需要 LLBot 8.0.3 及以上版本）"""
    try:
        return api("get_profile_like_count", bot_id=bot_id, user_id=int(user_id))
    except Exception as e:
        logger.error(f"获取名片赞数量失败: {e}")
        return {"status": "error", "message": str(e)}


def get_robot_uin_range(bot_id=None):
    """获取官方机器人 QQ 号范围"""
    try:
        return api("get_robot_uin_range", bot_id=bot_id)
    except Exception as e:
        logger.error(f"获取官方机器人QQ号范围失败: {e}")
        return {"status": "error", "message": str(e)}


def set_friend_category(user_id, category_id, bot_id=None):
    """移动好友分组"""
    try:
        return api("set_friend_category", bot_id=bot_id,
                   user_id=int(user_id), category_id=int(category_id))
    except Exception as e:
        logger.error(f"移动好友分组失败: {e}")
        return {"status": "error", "message": str(e)}


def get_qq_avatar(user_id=None, group_id=None, bot_id=None):
    """获取 QQ 或 QQ 群头像（返回 data.url）"""
    try:
        payload = {}
        if user_id is not None:
            payload["user_id"] = int(user_id)
        if group_id is not None:
            payload["group_id"] = int(group_id)
        return api("get_qq_avatar", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"获取QQ头像失败: {e}")
        return {"status": "error", "message": str(e)}


def get_doubt_friends_add_request(count=50, bot_id=None):
    """获取被过滤好友请求（需要 LLOneBot 6.2.0 及以上版本）"""
    try:
        return api("get_doubt_friends_add_request", bot_id=bot_id, count=count)
    except Exception as e:
        logger.error(f"获取被过滤好友请求失败: {e}")
        return {"status": "error", "message": str(e)}


def set_doubt_friends_add_request(flag, bot_id=None):
    """处理被过滤好友请求（需要 LLOneBot 6.2.0 及以上版本）"""
    try:
        return api("set_doubt_friends_add_request", bot_id=bot_id, flag=flag)
    except Exception as e:
        logger.error(f"处理被过滤好友请求失败: {e}")
        return {"status": "error", "message": str(e)}


def set_qq_profile(nickname=None, personal_note=None, bot_id=None):
    """设置登录号资料（nickname 名称，personal_note 个人说明）"""
    try:
        payload = {}
        if nickname is not None:
            payload["nickname"] = nickname
        if personal_note is not None:
            payload["personal_note"] = personal_note
        return api("set_qq_profile", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"设置登录号资料失败: {e}")
        return {"status": "error", "message": str(e)}


def set_input_status(user_id, event_type, bot_id=None):
    """设置输入状态（event_type 为 0 表示「对方正在说话...」，为 1 表示「对方正在输入...」；需要 LLBot 7.12.3 及以上版本）"""
    try:
        return api("set_input_status", bot_id=bot_id,
                   user_id=int(user_id), event_type=int(event_type))
    except Exception as e:
        logger.error(f"设置输入状态失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- OneBot11 群组：群基础与成员管理 ----
def get_group_list(no_cache=False, bot_id=None):
    """获取群列表（no_cache=True 时强制刷新缓存）"""
    try:
        return api("get_group_list", bot_id=bot_id, no_cache=no_cache)
    except Exception as e:
        logger.error(f"获取群列表失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_info(group_id, bot_id=None):
    """获取群信息（owner_id/is_top/shut_up_* 等字段需要 LLBot 7.8 及以上版本）"""
    try:
        return api("get_group_info", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"获取群信息失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_system_msg(bot_id=None):
    """获取群系统消息（含邀请加群申请 invited_requests 与加群申请 join_requests）"""
    try:
        return api("get_group_system_msg", bot_id=bot_id)
    except Exception as e:
        logger.error(f"获取群系统消息失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_add_request(flag, sub_type, approve=True, reason="", bot_id=None):
    """处理加群请求（sub_type 为 add 表示加群、invite 表示邀请；approve 是否同意，reason 拒绝理由）"""
    try:
        return api("set_group_add_request", bot_id=bot_id,
                   flag=flag, sub_type=sub_type, approve=approve, reason=reason)
    except Exception as e:
        logger.error(f"处理加群请求失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_leave(group_id, is_dismiss=False, bot_id=None):
    """退出群聊（is_dismiss=True 时解散群，仅群主可用）"""
    try:
        return api("set_group_leave", bot_id=bot_id,
                   group_id=int(group_id), is_dismiss=is_dismiss)
    except Exception as e:
        logger.error(f"退出群聊失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_admin(group_id, user_id, enable=True, bot_id=None):
    """设置群管理员（enable=True 设为管理员，False 取消管理员）"""
    try:
        return api("set_group_admin", bot_id=bot_id,
                   group_id=int(group_id), user_id=int(user_id), enable=enable)
    except Exception as e:
        logger.error(f"设置群管理员失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_card(group_id, user_id, card, bot_id=None):
    """设置群成员名片（card 为空字符串表示取消群名片）"""
    try:
        return api("set_group_card", bot_id=bot_id,
                   group_id=int(group_id), user_id=int(user_id), card=card)
    except Exception as e:
        logger.error(f"设置群名片失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_whole_ban(group_id, enable=True, bot_id=None):
    """群全体禁言（enable=True 开启，False 解除）"""
    try:
        return api("set_group_whole_ban", bot_id=bot_id,
                   group_id=int(group_id), enable=enable)
    except Exception as e:
        logger.error(f"群全体禁言失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_shut_list(group_id, bot_id=None):
    """获取被禁言群员列表"""
    try:
        return api("get_group_shut_list", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"获取被禁言群员列表失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_name(group_id, group_name, bot_id=None):
    """设置群名"""
    try:
        return api("set_group_name", bot_id=bot_id,
                   group_id=int(group_id), group_name=group_name)
    except Exception as e:
        logger.error(f"设置群名失败: {e}")
        return {"status": "error", "message": str(e)}


def batch_delete_group_member(group_id, user_ids, bot_id=None):
    """批量踢出群成员（user_ids 为 QQ 号列表，需要 LLBot 5.6.0 及以上版本）"""
    try:
        return api("batch_delete_group_member", bot_id=bot_id,
                   group_id=int(group_id), user_ids=[int(uid) for uid in user_ids])
    except Exception as e:
        logger.error(f"批量踢出群成员失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_honor_info(group_id, type="all", bot_id=None):
    """获取群荣誉（type 可选 talkative/performer/legend/strong_newbie/emotion/all）"""
    try:
        return api("get_group_honor_info", bot_id=bot_id,
                   group_id=int(group_id), type=type)
    except Exception as e:
        logger.error(f"获取群荣誉失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- OneBot11 群组：精华/公告/打卡 ----
def get_essence_msg_list(group_id, bot_id=None):
    """获取群精华消息列表"""
    try:
        return api("get_essence_msg_list", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"获取群精华消息列表失败: {e}")
        return {"status": "error", "message": str(e)}


def delete_essence_msg(message_id, bot_id=None):
    """删除群精华消息"""
    try:
        return api("delete_essence_msg", bot_id=bot_id, message_id=int(message_id))
    except Exception as e:
        logger.error(f"删除群精华消息失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_at_all_remain(group_id, bot_id=None):
    """获取群 @全体成员 剩余次数"""
    try:
        return api("get_group_at_all_remain", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"获取群@全体成员剩余次数失败: {e}")
        return {"status": "error", "message": str(e)}


def send_group_notice(group_id, content, image=None, pinned=False, confirm_required=True,
                      is_show_edit_card=False, tip_window=False, send_new_member=False, bot_id=None):
    """发送群公告（image 支持 http:// / file:// / base64://）"""
    try:
        payload = {
            "group_id": int(group_id),
            "content": content,
            "pinned": pinned,
            "confirm_required": confirm_required,
            "is_show_edit_card": is_show_edit_card,
            "tip_window": tip_window,
            "send_new_member": send_new_member,
        }
        if image is not None:
            payload["image"] = image
        return api("_send_group_notice", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"发送群公告失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_notice(group_id, bot_id=None):
    """获取群公告列表"""
    try:
        return api("_get_group_notice", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"获取群公告失败: {e}")
        return {"status": "error", "message": str(e)}


def delete_group_notice(group_id, notice_id, bot_id=None):
    """删除群公告（需要 LLOneBot 7.0.0 及以上版本）"""
    try:
        return api("_delete_group_notice", bot_id=bot_id,
                   group_id=int(group_id), notice_id=str(notice_id))
    except Exception as e:
        logger.error(f"删除群公告失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_sign(group_id, bot_id=None):
    """群打卡"""
    try:
        return api("send_group_sign", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"群打卡失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_signed_list(group_id, bot_id=None):
    """获取群组今日打卡列表（需要 LLBot 8.0.0 及以上版本）"""
    try:
        return api("get_group_signed_list", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"获取群组今日打卡列表失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- OneBot11 群组：群设置与相册 ----
def set_group_msg_mask(group_id, mask, bot_id=None):
    """设置群消息接收方式（mask: 1 接收并提醒，2 收进群助手不提醒，3 屏蔽，4 接收不提醒）"""
    try:
        return api("set_group_msg_mask", bot_id=bot_id,
                   group_id=int(group_id), mask=int(mask))
    except Exception as e:
        logger.error(f"设置群消息接收方式失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_remark(group_id, remark="", bot_id=None):
    """设置群备注（remark 为空字符串表示取消备注）"""
    try:
        return api("set_group_remark", bot_id=bot_id,
                   group_id=int(group_id), remark=remark)
    except Exception as e:
        logger.error(f"设置群备注失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_ignore_add_request(group_id, bot_id=None):
    """获取已过滤的加群通知"""
    try:
        return api("get_group_ignore_add_request", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"获取已过滤的加群通知失败: {e}")
        return {"status": "error", "message": str(e)}


def upload_group_album(group_id, album_id, files, bot_id=None):
    """上传媒体到群相册（files 为字符串列表，元素支持 file:// / http:// / base64://）"""
    try:
        return api("upload_group_album", bot_id=bot_id,
                   group_id=int(group_id), album_id=album_id, files=list(files))
    except Exception as e:
        logger.error(f"上传群相册失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_album_list(group_id, bot_id=None):
    """获取群相册列表"""
    try:
        return api("get_group_album_list", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"获取群相册列表失败: {e}")
        return {"status": "error", "message": str(e)}


def create_group_album(group_id, name, desc="", bot_id=None):
    """创建群相册"""
    try:
        return api("create_group_album", bot_id=bot_id,
                   group_id=int(group_id), name=name, desc=desc)
    except Exception as e:
        logger.error(f"创建群相册失败: {e}")
        return {"status": "error", "message": str(e)}


def delete_group_album(group_id, album_id, bot_id=None):
    """删除群相册"""
    try:
        return api("delete_group_album", bot_id=bot_id,
                   group_id=int(group_id), album_id=album_id)
    except Exception as e:
        logger.error(f"删除群相册失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_album_media_list(group_id, album_id, attach_info=None, bot_id=None):
    """获取群相册媒体列表（attach_info 用于分页；需要 LLBot 7.12.3 及以上版本）"""
    try:
        payload = {"group_id": int(group_id), "album_id": album_id}
        if attach_info is not None:
            payload["attach_info"] = attach_info
        return api("get_group_album_media_list", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"获取群相册媒体列表失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_portrait(group_id, file, bot_id=None):
    """设置群头像（file 支持 file:// / http:// / base64://，也接受本地路径）"""
    try:
        if not isinstance(file, str) or not file:
            logger.error(f"错误：无效的头像文件: {file}")
            return {"status": "error", "message": "无效的头像文件"}
        if not file.startswith(("file://", "http://", "https://", "base64://")):
            if not os.path.exists(file):
                logger.error(f"错误：头像文件不存在: {file}")
                return {"status": "error", "message": "文件不存在"}
            file = "file:///" + os.path.abspath(file).replace("\\", "/")
        return api("set_group_portrait", bot_id=bot_id, group_id=int(group_id), file=file)
    except Exception as e:
        logger.error(f"设置群头像失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- OneBot11 文件：群文件操作 ----
def set_group_file_forever(group_id, file_id, bot_id=None):
    """群文件转永久（需要 LLOneBot 6.5.0 及以上版本）"""
    try:
        return api("set_group_file_forever", bot_id=bot_id,
                   group_id=int(group_id), file_id=file_id)
    except Exception as e:
        logger.error(f"群文件转永久失败: {e}")
        return {"status": "error", "message": str(e)}


def delete_group_file(group_id, file_id, bot_id=None):
    """删除群文件（file_id 可在上报的上传文件消息中获取）"""
    try:
        return api("delete_group_file", bot_id=bot_id,
                   group_id=int(group_id), file_id=file_id)
    except Exception as e:
        logger.error(f"删除群文件失败: {e}")
        return {"status": "error", "message": str(e)}


def move_group_file(group_id, file_id, parent_directory, target_directory, bot_id=None):
    """移动群文件（parent_directory 当前文件夹 ID，target_directory 目标文件夹 ID）"""
    try:
        return api("move_group_file", bot_id=bot_id, group_id=int(group_id), file_id=file_id,
                   parent_directory=parent_directory, target_directory=target_directory)
    except Exception as e:
        logger.error(f"移动群文件失败: {e}")
        return {"status": "error", "message": str(e)}


def create_group_file_folder(group_id, name, bot_id=None):
    """创建群文件文件夹（返回 data.folder_id）"""
    try:
        return api("create_group_file_folder", bot_id=bot_id,
                   group_id=int(group_id), name=name)
    except Exception as e:
        logger.error(f"创建群文件文件夹失败: {e}")
        return {"status": "error", "message": str(e)}


def delete_group_folder(group_id, folder_id, bot_id=None):
    """删除群文件文件夹"""
    try:
        return api("delete_group_folder", bot_id=bot_id,
                   group_id=int(group_id), folder_id=folder_id)
    except Exception as e:
        logger.error(f"删除群文件文件夹失败: {e}")
        return {"status": "error", "message": str(e)}


def rename_group_file_folder(group_id, folder_id, new_folder_name, bot_id=None):
    """重命名群文件文件夹名"""
    try:
        return api("rename_group_file_folder", bot_id=bot_id, group_id=int(group_id),
                   folder_id=folder_id, new_folder_name=new_folder_name)
    except Exception as e:
        logger.error(f"重命名群文件文件夹名失败: {e}")
        return {"status": "error", "message": str(e)}


def rename_group_file(group_id, file_id, current_parent_directory, new_name, bot_id=None):
    """重命名群文件名（需要 LLBot 7.10.1 及以上版本）"""
    try:
        return api("rename_group_file", bot_id=bot_id, group_id=int(group_id), file_id=file_id,
                   current_parent_directory=current_parent_directory, new_name=new_name)
    except Exception as e:
        logger.error(f"重命名群文件名失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- OneBot11 文件：群/私聊文件查询与上传 ----
def get_group_file_system_info(group_id, bot_id=None):
    """获取群文件系统信息（文件总数/上限、已用与总空间）"""
    try:
        return api("get_group_file_system_info", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"获取群文件系统信息失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_root_files(group_id, bot_id=None):
    """获取群根目录文件列表（返回 data.files 与 data.folders）"""
    try:
        return api("get_group_root_files", bot_id=bot_id, group_id=int(group_id))
    except Exception as e:
        logger.error(f"获取群根目录文件列表失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_files_by_folder(group_id, folder_id, bot_id=None):
    """获取群子目录文件列表（返回 data.files 与 data.folders）"""
    try:
        return api("get_group_files_by_folder", bot_id=bot_id,
                   group_id=int(group_id), folder_id=folder_id)
    except Exception as e:
        logger.error(f"获取群子目录文件列表失败: {e}")
        return {"status": "error", "message": str(e)}


def get_group_file_url(group_id, file_id, bot_id=None):
    """获取群文件资源链接（返回 data.url）"""
    try:
        return api("get_group_file_url", bot_id=bot_id,
                   group_id=int(group_id), file_id=file_id)
    except Exception as e:
        logger.error(f"获取群文件资源链接失败: {e}")
        return {"status": "error", "message": str(e)}


def get_private_file_url(file_id, bot_id=None):
    """获取私聊文件资源链接（需要 LLOneBot 5.9.0 及以上版本）"""
    try:
        return api("get_private_file_url", bot_id=bot_id, file_id=file_id)
    except Exception as e:
        logger.error(f"获取私聊文件资源链接失败: {e}")
        return {"status": "error", "message": str(e)}


def upload_private_file(user_id, file, name, bot_id=None):
    """上传私聊文件（file 支持本地路径、http://、file://、base64://）"""
    try:
        if not isinstance(file, str) or not file:
            logger.error(f"错误：无效的文件: {file}")
            return {"status": "error", "message": "无效的文件"}
        if not file.startswith(("file://", "http://", "https://", "base64://")):
            if not os.path.exists(file):
                logger.error(f"文件不存在: {file}")
                return {"status": "error", "message": "文件不存在"}
        return api("upload_private_file", bot_id=bot_id,
                   user_id=int(user_id), file=file, name=name)
    except Exception as e:
        logger.error(f"上传私聊文件失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- OneBot11 文件：闪传与缓存 ----
def upload_flash_file(paths, title=None, bot_id=None):
    """上传闪传文件（paths 为文件列表，支持 file://、http://、base64://；需要 LLOneBot 5.3.0 及以上版本）"""
    try:
        if not paths:
            logger.error("错误：闪传文件列表为空")
            return {"status": "error", "message": "闪传文件列表为空"}
        for path in paths:
            if isinstance(path, str) and not path.startswith(("file://", "http://", "https://", "base64://")):
                if not os.path.exists(path):
                    logger.error(f"文件不存在: {path}")
                    return {"status": "error", "message": "文件不存在"}
        payload = {"paths": list(paths)}
        if title is not None:
            payload["title"] = title
        return api("upload_flash_file", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"上传闪传文件失败: {e}")
        return {"status": "error", "message": str(e)}


def download_flash_file(share_link=None, file_set_id=None, bot_id=None):
    """下载闪传文件（share_link 与 file_set_id 二选一；需要 LLOneBot 5.3.0 及以上版本）"""
    try:
        payload = {}
        if share_link is not None:
            payload["share_link"] = share_link
        if file_set_id is not None:
            payload["file_set_id"] = file_set_id
        return api("download_flash_file", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"下载闪传文件失败: {e}")
        return {"status": "error", "message": str(e)}


def get_flash_file_info(share_link=None, file_set_id=None, bot_id=None):
    """获取闪传文件详情（share_link 与 file_set_id 二选一；需要 LLOneBot 5.3.0 及以上版本）"""
    try:
        payload = {}
        if share_link is not None:
            payload["share_link"] = share_link
        if file_set_id is not None:
            payload["file_set_id"] = file_set_id
        return api("get_flash_file_info", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"获取闪传文件详情失败: {e}")
        return {"status": "error", "message": str(e)}


def download_file(url=None, base64=None, name=None, headers=None, bot_id=None):
    """下载文件到缓存目录（url 与 base64 二选一，headers 为形如 "Key=Value" 的字符串列表）"""
    try:
        payload = {}
        if url is not None:
            payload["url"] = url
        if base64 is not None:
            payload["base64"] = base64
        if name is not None:
            payload["name"] = name
        if headers is not None:
            payload["headers"] = list(headers)
        return api("download_file", bot_id=bot_id, **payload)
    except Exception as e:
        logger.error(f"下载文件到缓存目录失败: {e}")
        return {"status": "error", "message": str(e)}


def reshare_flash_file(file_set_id, bot_id=None):
    """重新分享闪传文件（只能重新分享尚未过期的闪传；需要 LLBot 7.11.0 及以上版本）"""
    try:
        return api("reshare_flash_file", bot_id=bot_id, file_set_id=file_set_id)
    except Exception as e:
        logger.error(f"重新分享闪传文件失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- OneBot11 系统：登录信息与运行状态 ----
def get_login_info(bot_id=None):
    """获取登录号信息（返回 data.user_id 与 data.nickname）"""
    try:
        return api("get_login_info", bot_id=bot_id)
    except Exception as e:
        logger.error(f"获取登录号信息失败: {e}")
        return {"status": "error", "message": str(e)}


def get_version_info(bot_id=None):
    """获取版本信息（返回 app_name/protocol_version/app_version）"""
    try:
        return api("get_version_info", bot_id=bot_id)
    except Exception as e:
        logger.error(f"获取版本信息失败: {e}")
        return {"status": "error", "message": str(e)}


def get_status(bot_id=None):
    """获取 bot 状态（返回 online/good 及 stat 运行统计）"""
    try:
        return api("get_status", bot_id=bot_id)
    except Exception as e:
        logger.error(f"获取bot状态失败: {e}")
        return {"status": "error", "message": str(e)}


def clean_cache(bot_id=None):
    """清理缓存（该接口在 LLOneBot 5.0+ 之后已失效，仅兼容保留）"""
    try:
        return api("clean_cache", bot_id=bot_id)
    except Exception as e:
        logger.error(f"清理缓存失败: {e}")
        return {"status": "error", "message": str(e)}


def get_cookies(domain="qun.qq.com", bot_id=None):
    """获取指定域名的 cookies（返回 data.cookies 与 data.bkn）"""
    try:
        return api("get_cookies", bot_id=bot_id, domain=domain)
    except Exception as e:
        logger.error(f"获取cookies失败: {e}")
        return {"status": "error", "message": str(e)}


def set_online_status(status=10, ext_status=0, battery_status=0, bot_id=None):
    """设置在线状态（status: 10 在线，30 离开，40 隐身，50 忙碌，60 Q我吧，70 请勿打扰）"""
    try:
        return api("set_online_status", bot_id=bot_id, status=int(status),
                   ext_status=int(ext_status), battery_status=int(battery_status))
    except Exception as e:
        logger.error(f"设置在线状态失败: {e}")
        return {"status": "error", "message": str(e)}


def set_restart(bot_id=None):
    """重启（该接口在 LLOneBot 5.0+ 之后已失效，仅兼容保留）"""
    try:
        return api("set_restart", bot_id=bot_id)
    except Exception as e:
        logger.error(f"重启失败: {e}")
        return {"status": "error", "message": str(e)}


def scan_qrcode(file, bot_id=None):
    """扫描二维码（file 支持 http(s):// / file:// / base64://，也接受本地路径；需要 LLOneBot 7.2.0 及以上版本）"""
    try:
        if not isinstance(file, str) or not file:
            logger.error(f"错误：无效的二维码文件: {file}")
            return {"status": "error", "message": "无效的二维码文件"}
        if not file.startswith(("file://", "http://", "https://", "base64://")):
            if not os.path.exists(file):
                logger.error(f"错误：二维码文件不存在: {file}")
                return {"status": "error", "message": "文件不存在"}
            file = "file:///" + os.path.abspath(file).replace("\\", "/")
        return api("scan_qrcode", bot_id=bot_id, file=file)
    except Exception as e:
        logger.error(f"扫描二维码失败: {e}")
        return {"status": "error", "message": str(e)}


# ---- OneBot11 其他：图片识别与表情 ----
def ocr_image(image, bot_id=None):
    """图片 OCR（image 支持 http:// / file:// / base64://，返回 data.texts 与 data.language）"""
    try:
        return api("ocr_image", bot_id=bot_id, image=image)
    except Exception as e:
        logger.error(f"图片OCR失败: {e}")
        return {"status": "error", "message": str(e)}


def get_rkey(bot_id=None):
    """获取图片 rkey（返回 private_key/group_key/expired_time/updated_time）"""
    try:
        return api("get_rkey", bot_id=bot_id)
    except Exception as e:
        logger.error(f"获取图片rkey失败: {e}")
        return {"status": "error", "message": str(e)}


def get_recommend_face(word, bot_id=None):
    """获取推荐表情（word 为关键词，返回 data.url 列表；需要 LLOneBot 5.5.0 及以上版本）"""
    try:
        return api("get_recommend_face", bot_id=bot_id, word=word)
    except Exception as e:
        logger.error(f"获取推荐表情失败: {e}")
        return {"status": "error", "message": str(e)}


def fetch_custom_face(bot_id=None):
    """获取收藏表情（返回 data 为表情 URL 列表）"""
    try:
        return api("fetch_custom_face", bot_id=bot_id)
    except Exception as e:
        logger.error(f"获取收藏表情失败: {e}")
        return {"status": "error", "message": str(e)}


def send_pb(cmd, pb_hex, bot_id=None):
    """发送 Protobuf 数据包（cmd 为命令名，pb_hex 为 Protobuf 的 16 进制字符串；不保证该功能生效）"""
    try:
        return api("send_pb", bot_id=bot_id, cmd=cmd, hex=pb_hex)
    except Exception as e:
        logger.error(f"发送Protobuf数据包失败: {e}")
        return {"status": "error", "message": str(e)}
