# -*- coding: utf-8 -*-
import re
import time
import random
from datetime import datetime

from bin import send, logger, mjbconfig, mjbutils, worker, message

# 模块级状态：待验证用户信息
# {"group_id": {"user_id": {"answer": 答案, "question": "问题", "attempts": 尝试次数, "timeout": 超时时间戳}}}
authusers = {}

# 是否自动接受好友申请
auto_accept_friend_requests = True


def modcfg():
    return {"version": "1.0.3.4", "author": "JsoftStudio", "description": "入群欢迎、消息字数限制、入群验证、好友申请自动处理"}


# ===================== 验证信息持久化 =====================
def _save_auth_info():
    """保存验证信息到 gpauths.json"""
    try:
        mjbconfig.save_gpauths({"authusers": authusers})
    except Exception as e:
        logger.error(f"保存验证信息失败: {e}")


def _load_auth_info():
    """从 gpauths.json 加载验证信息"""
    global authusers
    try:
        data = mjbconfig.load_gpauths()
        authusers = data.get('authusers', {}) if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"加载验证信息失败: {e}")
        authusers = {}


def generate_math_question():
    """生成随机口算题"""
    operations = ['+', '-', '*', '/']
    operation = random.choice(operations)

    if operation in ['+', '-']:
        # 加减法使用较大的数字
        a = random.randint(10, 999)
        b = random.randint(10, 999)
        if operation == '-':
            # 确保减法结果非负
            if a < b:
                a, b = b, a
        question = f"{a} {operation} {b} = ?"
        answer = a + b if operation == '+' else a - b
    else:
        # 乘除法使用较小的数字，确保结果为整数
        if operation == '*':
            a = random.randint(1, 99)
            b = random.randint(1, 99)
            question = f"{a} {operation} {b} = ?"
            answer = a * b
        else:
            # 除法确保结果为整数
            answer = random.randint(1, 99)
            b = random.randint(1, 99)
            a = answer * b
            question = f"{a} {operation} {b} = ?"

    return question, answer


# ===================== 后台线程类 =====================
class AuthThread:
    """群验证超时检查线程（可调用对象，通过 worker.start_background 启动）"""

    def __init__(self):
        self.running = True

    def __call__(self):
        logger.info("AuthThread: 开始运行验证超时检查")
        while self.running:
            current_time = time.time()
            try:
                # 检查并处理超时的验证
                for group_id in list(authusers.keys()):
                    for user_id in list(authusers[group_id].keys()):
                        try:
                            user_info = authusers[group_id][user_id]
                            if current_time > user_info['timeout']:
                                logger.info(f"AuthThread: 检测到用户{user_id}在群{group_id}中验证超时")
                                # 超时处理 - 发送提示并踢出用户
                                del authusers[group_id][user_id]
                                _save_auth_info()
                                # @用户并提示验证超时
                                send.group_at(int(group_id), int(user_id), " 验证超时，请重新加群进行验证")
                                # 踢人功能
                                try:
                                    send.set_group_kick(int(group_id), int(user_id), False)
                                    logger.info(f"AuthThread: 已将用户{user_id}从群{group_id}踢出，原因：验证超时")
                                except Exception as e:
                                    logger.error(f"AuthThread: 踢人失败: {e}")
                        except Exception as e:
                            logger.error(f"AuthThread: 处理用户{user_id}时出错: {e}")
                # 定期保存验证信息
                _save_auth_info()
            except Exception as e:
                logger.error(f"AuthThread: 主循环错误: {e}")
            time.sleep(5)  # 每5秒检查一次

    def stop(self):
        self.running = False
        logger.info("AuthThread: 已停止运行")


class FriendRequestThread:
    """好友申请自动处理线程（可调用对象，通过 worker.start_background 启动）"""

    def __init__(self):
        self.running = True

    def __call__(self):
        logger.info("好友申请自动处理线程已启动")
        while self.running:
            # 这里我们主要依靠HTTP回调来处理实时的好友申请
            # 此线程可以用于检查被过滤的好友请求（如果API支持）
            try:
                # 如果auto_accept_friend_requests为True且API支持获取被过滤的好友请求
                if auto_accept_friend_requests:
                    # 尝试获取被过滤的好友请求（需要LLOneBot 6.2.0及以上版本）
                    result = send.api('get_friend_filtered_requests')
                    if isinstance(result, dict) and result.get('status') == 'ok' and 'data' in result:
                        filtered_requests = result['data']
                        for request in filtered_requests:
                            flag = request.get('flag')
                            user_id = request.get('user_id')
                            # 自动接受被过滤的好友请求
                            try:
                                send.api('set_friend_filtered_request', flag=flag, approve=True, remark='')
                                logger.info(f"已自动接受被过滤的好友申请：{user_id}")
                            except Exception as e:
                                logger.error(f"处理被过滤好友申请失败: {e}")
            except Exception:
                # 静默处理异常，避免线程崩溃
                pass
            time.sleep(30)  # 每30秒检查一次被过滤的好友请求

    def stop(self):
        self.running = False


# ===================== 消息拦截器 =====================
def _auth_interceptor(ctx):
    """验证拦截器：fn(ctx) -> bool

    处理验证中用户的消息：超时踢出、命令拦截、答案校验、错误次数累计。
    返回 True 表示已处理（终止后续管线）。
    """
    group_id = ctx["group_id"]
    user_id = ctx["user_id"]
    raw_message = ctx["raw_message"]
    message_id = ctx["message_id"]
    group_id_str = str(group_id)

    # 用户不在验证中，放行
    if group_id_str not in authusers or str(user_id) not in authusers[group_id_str]:
        return False

    user_info = authusers[group_id_str][str(user_id)]

    # 检查是否超时
    if time.time() > user_info['timeout']:
        del authusers[group_id_str][str(user_id)]
        _save_auth_info()
        send.group_at(group_id, user_id, " 验证未通过，请按照验证方式进行验证，您已被自动踢出本群，请重新加群")
        # 踢人功能
        try:
            send.set_group_kick(int(group_id), int(user_id), False)
            logger.info(f"已将用户{user_id}从群{group_id}踢出，原因：验证超时")
        except Exception as e:
            logger.error(f"踢人失败: {e}")
        return True

    # 检查是否是命令
    if raw_message.startswith('mjb.') or raw_message.startswith('.'):
        # 撤回消息
        try:
            send.delete_msg(message_id)
        except Exception as e:
            logger.error(f"撤回消息失败: {e}")

        # 提示用户
        send.group_at(group_id, user_id, " 入群验证计算结果错误！题目：" + user_info['question'] + "，请重试，验证通过前无法发送其他消息，请直接发送答案，不要@机器人")
        return True

    # 检查答案
    try:
        user_answer = int(raw_message.strip())
        if user_answer == user_info['answer']:
            # 验证成功
            del authusers[group_id_str][str(user_id)]
            _save_auth_info()
            send.group_at(group_id, user_id, " 验证成功，新人请看公告，欢迎入群聊天")
        else:
            # 验证失败，首先撤回消息
            try:
                send.delete_msg(message_id)
            except Exception as e:
                logger.error(f"消息处理: 撤回错误答案消息失败: {e}")

            # 增加尝试次数
            user_info['attempts'] += 1
            # 获取群特定的验证参数
            group_frequency = mjbconfig.get_gpauthfrequency()
            gpauth_configs = mjbconfig.get_gpauth_configs()
            if group_id_str in gpauth_configs and "frequency" in gpauth_configs[group_id_str]:
                group_frequency = gpauth_configs[group_id_str]["frequency"]
            if user_info['attempts'] >= group_frequency:
                # 尝试次数过多
                del authusers[group_id_str][str(user_id)]
                _save_auth_info()
                # @用户并提示验证次数超限
                send.group_at(int(group_id), int(user_id), " 验证未通过，请按照验证方式进行验证，您已被自动踢出本群，请重新加群")
                # 踢人功能
                try:
                    send.set_group_kick(int(group_id), int(user_id), False)
                    logger.info(f"消息处理: 已将用户{user_id}从群{group_id}踢出，原因：验证次数超限")
                except Exception as e:
                    logger.error(f"消息处理: 踢人失败: {e}")
            else:
                # 继续验证
                _save_auth_info()
                remaining_attempts = group_frequency - user_info['attempts']
                send.group_at(int(group_id), int(user_id), f" 入群验证计算结果错误！题目：{user_info['question']}，请重试，剩余尝试次数：{remaining_attempts}，请直接发送答案，不要@机器人")
    except ValueError:
        # 撤回消息
        try:
            send.delete_msg(message_id)
        except Exception as e:
            logger.error(f"撤回消息失败: {e}")

        # 提示用户
        send.group_at(group_id, user_id, " 入群验证计算结果错误！题目：" + user_info['question'] + "，请重试，验证通过前无法发送其他消息，请直接发送答案，不要@机器人")

    return True


def _word_limit_interceptor(ctx):
    """消息字数限制拦截器：fn(ctx) -> bool

    超长消息撤回并以合并转发形式重新发送（伪造发送者）。
    管理员与白名单用户豁免。返回 True 表示已处理。
    """
    group_id = ctx["group_id"]
    user_id = ctx["user_id"]
    raw_message = ctx["raw_message"]
    message_id = ctx["message_id"]
    config_data = ctx["config_data"]
    group_id_str = str(group_id)

    # 仅在启用字数限制的群检查
    if group_id_str not in mjbconfig.get_autorecallgps_list():
        return False

    # 检查是否为Bot管理员
    admin_list = mjbutils.get_admin_list_from_config(config_data)
    is_bot_admin = str(user_id) in admin_list

    # 检查是否为群管理员或群主
    member_role = send.get_group_member_role(group_id, user_id)
    is_group_admin_or_owner = member_role in ['owner', 'admin']

    # 检查是否在白名单中
    gprecall_configs = mjbconfig.get_gprecall_configs()
    is_in_whitelist = False
    if group_id_str in gprecall_configs:
        whitelist = gprecall_configs[group_id_str].get('whitelist', [])
        is_in_whitelist = str(user_id) in whitelist

    # 只有非管理员用户且不在白名单中才需要检查字数限制
    if is_bot_admin or is_group_admin_or_owner or is_in_whitelist:
        return False

    # 获取群特定的字数限制配置
    max_words = 300  # 默认限制300字
    if group_id_str in gprecall_configs and "count" in gprecall_configs[group_id_str]:
        max_words = gprecall_configs[group_id_str]["count"]

    # 计算消息字数（去除cq码并将每个cq码计算为10个字）
    cq_pattern = r'\[CQ:[^\]]+\]'
    cq_codes = re.findall(cq_pattern, raw_message)
    cq_count = len(cq_codes)
    text_without_cq = re.sub(cq_pattern, '', raw_message)
    text_length = len(text_without_cq)
    # 总长度 = 纯文本长度 + cq码数量 * 10
    message_length = text_length + (cq_count * 10)

    # 未超限，放行
    if message_length <= max_words:
        return False

    # 如果消息字数超过限制，自动撤回并以合并转发形式重新发送
    try:
        # 获取用户昵称
        user_nickname = "未知用户"
        try:
            # 尝试获取用户的群昵称
            member_info = send.get_group_member_info(group_id, user_id)
            if member_info:
                # 优先使用群昵称，如果没有则使用昵称
                user_nickname = member_info.get('card', member_info.get('nickname', "未知用户"))
        except Exception:
            # 获取昵称失败时不影响主要功能
            pass

        # 撤回原消息
        send.delete_msg(message_id)
        logger.info(f"已撤回用户{user_id}({user_nickname})在群{group_id}发送的超长消息，消息长度：{message_length}字，超过限制：{max_words}字")

        # 以合并转发形式重新发送消息，伪造发送者为原用户
        send.send_group_forward_msg(group_id, [raw_message], fake_qq=str(user_id), fake_name=user_nickname)

        # @原发送者并提示
        send.group_at(group_id, user_id, f" 你发送的消息超过本群字数限制（{max_words}字），已自动转为合并卡片，避免卡屏")
    except Exception as e:
        logger.error(f"处理超长消息失败: {e}")
    return True


# ===================== 通知事件处理器 =====================
def _on_group_increase(data):
    """入群通知：发送欢迎消息 + 初始化验证（如启用）"""
    group_id = data.get('group_id')
    user_id = data.get('user_id')
    group_id_str = str(group_id)

    autowelgps_list = mjbconfig.get_autowelgps_list()
    gpwel_configs = mjbconfig.get_gpwel_configs()

    # 群入群欢迎逻辑
    if group_id_str in autowelgps_list:
        # 在线程池中处理入群欢迎
        def welcome_thread():
            try:
                # 获取群名称和用户昵称
                group_name = "本群"
                username = "新成员"

                # 获取群信息
                try:
                    result = send.api('get_group_info', group_id=group_id)
                    if isinstance(result, dict) and result.get('status') == 'ok' and 'data' in result:
                        group_name = result['data'].get('group_name', '本群')
                except Exception as e:
                    logger.error(f"获取群信息失败: {e}")

                # 获取用户信息
                try:
                    member_info = send.get_group_member_info(group_id, user_id)
                    if member_info:
                        username = member_info.get('nickname', str(user_id))
                except Exception as e:
                    logger.error(f"获取用户信息失败: {e}")

                # 获取欢迎内容
                welcome_text = gpwel_configs.get(group_id_str, {}).get('welcome_text', '欢迎加入本群，请联系管理员使用"mjb.autogpwel set wel 内容"设置欢迎内容')

                # 获取当前时间
                join_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")

                # 构建欢迎消息
                message = f" {welcome_text}\n----------\n用户名：{username}\n入群时间：{join_time}"

                # 发送欢迎消息（@新人）
                time.sleep(1)  # 延迟1秒，确保用户信息已同步
                send.group_at(group_id, user_id, message)
            except Exception as e:
                logger.error(f"发送入群欢迎消息失败: {e}")

        # 启动欢迎线程
        worker.submit(welcome_thread)

    # 群验证逻辑
    gpauthgroups = mjbconfig.get_gpauthgroups()
    if group_id_str in gpauthgroups:
        # 生成随机口算题
        question, answer = generate_math_question()

        # 初始化待验证用户信息
        if group_id_str not in authusers:
            authusers[group_id_str] = {}

        # 获取群特定的验证参数
        gpauthtime = mjbconfig.get_gpauthtime()
        gpauth_configs = mjbconfig.get_gpauth_configs()
        group_timeout = gpauthtime
        if group_id_str in gpauth_configs and "timeout" in gpauth_configs[group_id_str]:
            group_timeout = gpauth_configs[group_id_str]["timeout"]
        authusers[group_id_str][str(user_id)] = {
            'answer': answer,
            'question': question,
            'attempts': 0,
            'timeout': time.time() + group_timeout
        }

        # 保存验证信息
        _save_auth_info()

        # 获取群名称
        group_name = "本群"
        try:
            result = send.api('get_group_info', group_id=group_id)
            if isinstance(result, dict) and result.get('status') == 'ok' and 'data' in result:
                group_name = result['data'].get('group_name', '本群')
        except Exception as e:
            logger.error(f"获取群信息失败: {e}")

        # 发送验证提示
        send.group_at(group_id, user_id, f" 欢迎加入{group_name}，请进行入群验证：{question}，验证通过前无法发送消息，剩余时间：{group_timeout}秒，请直接发送答案，不要@机器人")


def _on_group_decrease(data):
    """退群通知：清理验证信息 + 发送退出欢送（如启用）"""
    group_id = data.get('group_id')
    user_id = data.get('user_id')
    group_id_str = str(group_id)

    # 如果用户在验证中，清除验证信息
    if group_id_str in authusers and str(user_id) in authusers[group_id_str]:
        del authusers[group_id_str][str(user_id)]
        _save_auth_info()

    # 群退出欢送逻辑
    autowelgps_list = mjbconfig.get_autowelgps_list()
    if group_id_str in autowelgps_list:
        # 在线程池中处理退出欢送
        def farewell_thread():
            try:
                # 获取用户名
                username = "成员"

                # 获取用户信息
                try:
                    member_info = send.get_group_member_info(group_id, user_id)
                    if member_info:
                        username = member_info.get('nickname', str(user_id))
                except Exception as e:
                    logger.error(f"获取用户信息失败: {e}")

                # 获取当前时间
                leave_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")

                # 构建欢送消息
                message = f"{username}离开了本群，再见啦！\n----------\n退群时间：{leave_time}"

                # 发送欢送消息
                time.sleep(1)  # 延迟1秒，确保信息已同步
                send.group(group_id, message)
            except Exception as e:
                logger.error(f"发送退出欢送消息失败: {e}")

        # 启动欢送线程
        worker.submit(farewell_thread)


def _on_friend_request(data):
    """好友申请通知：自动接受好友申请（如启用）"""
    user_id = data.get('user_id')
    comment = data.get('comment', '')
    flag = data.get('flag')
    logger.info(f"好友请求: 请求者QQ={user_id}, 请求说明={comment}")

    # 自动接受好友申请
    if auto_accept_friend_requests:
        try:
            result = send.api('set_friend_add_request', flag=flag, approve=True, remark='')
            if isinstance(result, dict) and result.get('status') == 'ok':
                logger.info(f"已自动接受好友申请: {user_id}")
            else:
                logger.error(f"自动接受好友申请失败: {result}")
        except Exception as e:
            logger.error(f"处理好友申请异常: {e}")


# ===================== 初始化 =====================
def init():
    """编排层加载后调用：加载验证信息、启动后台线程、注册拦截器与通知处理器"""
    # 加载验证信息
    _load_auth_info()

    # 启动群验证超时检查线程
    auth_thread = AuthThread()
    worker.start_background("AuthThread", auth_thread, stop_attr="stop")

    # 启动好友申请自动处理线程
    friend_request_thread = FriendRequestThread()
    worker.start_background("FriendRequestThread", friend_request_thread, stop_attr="stop")

    # 注册消息拦截器（验证优先，其次字数限制）
    # 注：屏蔽词拦截器由 modules/filter.py 专门负责
    message.register_interceptor(_auth_interceptor)
    message.register_interceptor(_word_limit_interceptor)

    # 注册通知事件处理器
    message.register_notice_handler("group_increase", _on_group_increase)
    message.register_notice_handler("group_decrease", _on_group_decrease)
    message.register_notice_handler("friend_request", _on_friend_request)
