# -*- coding: utf-8 -*-
import os
import requests

from bin import logger, mjbconfig


def _url(action):
    return f"{mjbconfig.get_LLbot_url()}/{action}"


def api(action, **payload):
    """通用 LLbotQQ HTTP API 调用"""
    try:
        response = requests.post(_url(action), json=payload, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"调用 API {action} 失败: {e}")
        return {"status": "error", "message": str(e)}


def group(group_id, message):
    """发送群文本消息"""
    try:
        if group_id == 0:
            return
        requests.post(_url("send_group_msg"), json={
            "group_id": group_id,
            "message": [{"type": "text", "data": {"text": message}}],
        }, timeout=15)
    except Exception as e:
        logger.error(f"发送群消息时出错: {e}")


def group_at(group_id, qq_number, text_message=""):
    """发送群聊 @ 消息"""
    try:
        if group_id == 0:
            return
        message_content = [{"type": "at", "data": {"qq": str(qq_number)}}]
        if text_message:
            message_content.append({"type": "text", "data": {"text": text_message}})
        requests.post(_url("send_group_msg"), json={
            "group_id": group_id,
            "message": message_content,
        }, timeout=15)
    except Exception as e:
        logger.error(f"发送群@消息时出错: {e}")


def group_reply(group_id, user_id, message_id, content):
    """发送引用消息回复"""
    try:
        message_content = [
            {"type": "reply", "data": {"id": int(message_id)}},
            {"type": "at", "data": {"qq": int(user_id)}},
            {"type": "text", "data": {"text": f" {content}"}},
        ]
        requests.post(_url("send_group_msg"), json={
            "group_id": int(group_id),
            "message": message_content,
        }, timeout=15)
        logger.info(f"引用消息发送成功: 群{group_id}，回复消息{message_id}")
        return True
    except Exception as e:
        logger.error(f"发送引用消息失败: {e}")
        return False


def group_image(group_id, image_path):
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
        response = requests.post(_url("send_group_msg"), json=payload, timeout=15)
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


def private(user_id, content):
    """发送私聊文本消息"""
    try:
        payload = {
            "user_id": int(user_id),
            "message": [{"type": "text", "data": {"text": content}}],
        }
        response = requests.post(_url("send_private_msg"), json=payload, timeout=15)
        response.raise_for_status()
        logger.info(f"私聊消息发送成功: 用户{user_id}")
        return True
    except Exception as e:
        logger.error(f"发送私聊消息失败: {e}")
        return False


def send_group_forward_msg(group_id, messages, fake_qq=None, fake_name=None):
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
        response = requests.post(_url("send_group_forward_msg"), json={
            "group_id": group_id,
            "messages": [node_data],
        }, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"发送合并转发消息时出错: {e}")
        return {"status": "error", "message": str(e)}


def send_group_file(group_id, file_path, file_name=None, folder_id=None):
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
        response = requests.post(_url("upload_group_file"), json=payload, timeout=30)
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


# ---- LLbotQQ API 封装 ----
def get_group_member_role(group_id, user_id):
    """获取群成员身份：owner/admin/member/unknown"""
    try:
        payload = {"group_id": int(group_id), "user_id": int(user_id), "no_cache": False}
        response = requests.post(_url("get_group_member_info"), json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok" and "data" in data:
                return data["data"].get("role", "unknown")
        return "unknown"
    except Exception as e:
        logger.error(f"获取群成员身份时出错: {e}")
        return "unknown"


def get_group_member_info(group_id, user_id):
    """获取群成员信息（完整 dict）"""
    try:
        response = requests.post(_url("get_group_member_info"), json={
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


def get_group_member_list(group_id):
    """获取群成员列表"""
    try:
        response = requests.post(_url("get_group_member_list"), json={
            "group_id": group_id,
        }, timeout=10)
        result = response.json()
        if result.get("status") == "ok" and "data" in result:
            return result["data"]
        return []
    except Exception as e:
        logger.error(f"获取群成员列表时发生异常: {e}")
        return []


def get_stranger_info(user_id):
    """获取用户信息"""
    try:
        response = requests.post(_url("get_stranger_info"), json={
            "user_id": user_id,
        }, timeout=10)
        result = response.json()
        if result.get("status") == "ok" and "data" in result:
            return result["data"]
        return {}
    except Exception as e:
        logger.error(f"获取用户信息时发生异常: {e}")
        return {}


def delete_msg(message_id):
    """撤回消息"""
    try:
        requests.post(_url("delete_msg"), json={"message_id": int(message_id)}, timeout=5)
        return True
    except Exception as e:
        logger.error(f"撤回消息失败: {e}")
        return False


def set_group_kick(group_id, user_id, reject_add_request=False):
    """踢出群成员"""
    try:
        requests.post(_url("set_group_kick"), json={
            "group_id": int(group_id), "user_id": int(user_id),
            "reject_add_request": reject_add_request,
        }, timeout=5)
        logger.info(f"已将用户{user_id}从群{group_id}踢出")
        return True
    except Exception as e:
        logger.error(f"踢人失败: {e}")
        return False


def set_group_ban(group_id, user_id, duration=0):
    """禁言成员（duration 秒，0 表示解除）"""
    try:
        requests.post(_url("set_group_ban"), json={
            "group_id": int(group_id), "user_id": int(user_id),
            "duration": int(duration),
        }, timeout=5)
        return True
    except Exception as e:
        logger.error(f"禁言失败: {e}")
        return False


def send_like(user_id, times=10):
    """点赞"""
    try:
        return api("send_like", user_id=int(user_id), times=times)
    except Exception as e:
        logger.error(f"点赞失败: {e}")
        return {"status": "error", "message": str(e)}


def send_poke(group_id, user_id):
    """戳一戳"""
    try:
        return api("group_poke", group_id=int(group_id), user_id=int(user_id))
    except Exception as e:
        logger.error(f"戳一戳失败: {e}")
        return {"status": "error", "message": str(e)}


def set_group_special_title(group_id, user_id, special_title=""):
    """设置群成员专属头衔（special_title 为空字符串表示去掉群头衔）"""
    try:
        return api("set_group_special_title", group_id=int(group_id),
                   user_id=int(user_id), special_title=special_title)
    except Exception as e:
        logger.error(f"设置群头衔失败: {e}")
        return {"status": "error", "message": str(e)}


def set_essence_msg(message_id):
    """设置群精华消息"""
    try:
        return api("set_essence_msg", message_id=int(message_id))
    except Exception as e:
        logger.error(f"设置群精华消息失败: {e}")
        return {"status": "error", "message": str(e)}
