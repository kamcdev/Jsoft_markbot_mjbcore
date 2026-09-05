# -*- coding: utf-8 -*-
import os
import json
import re
import threading

from bin import logger, kadset

_MJBC_VER_RAW = "mjb-1.0.3.6(140)"


def get_mjbcver_raw():
    """获取原始版本号"""
    return _MJBC_VER_RAW


def get_mjbcver():
    """获取版本号字符串"""
    m = re.match(r"^[a-zA-Z]+-(.+)\(\d+\)$", _MJBC_VER_RAW)
    return m.group(1) if m else _MJBC_VER_RAW


def get_mjbcver_num():
    """获取数字版本号"""
    m = re.search(r"\((\d+)\)", _MJBC_VER_RAW)
    return m.group(1) if m else ""


# ---- 路径常量 ----
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录
_ACCOUNT_FILE = os.path.join(_ROOT, "account.json")   # 账号列表 {QQ: {webhook_port, send_port}}
_CONFIG_DIR = os.path.join(_ROOT, "config")           # 账号配置目录 config/<bot_id>/
_CONFIG_FILE = os.path.join(_ROOT, "config.json")     # 全局 config.json（保留在根目录）

# 默认端口（account.json / group.json 均未配置时使用）
_DEFAULT_WEBHOOK_PORT = 9762
_DEFAULT_SEND_PORT = 3002

# ---- 账号列表（account.json）----
_accounts = {}  # bot_id(str) -> {"webhook_port": int, "send_port": int}

def _kadset_file(path):
    """json 配置路径对应的 kadset 路径（group.json -> group.kadset）"""
    if path.endswith(".json"):
        return path[:-len(".json")] + ".kadset"
    return path + ".kadset"


def _read_config_file(path):
    """读取配置文件：优先 json，无 json 读对应位置 kadset，都不存在返回 None"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    kpath = _kadset_file(path)
    if os.path.exists(kpath):
        with open(kpath, "r", encoding="utf-8") as f:
            return kadset.load(f)
    return None


def _write_config_file(path, data):
    """写配置文件：kadset 存在且 json 不存在时写 kadset（保持原格式），否则写 json

    返回实际写入的路径。
    """
    kpath = _kadset_file(path)
    if not os.path.exists(path) and os.path.exists(kpath):
        with open(kpath, "w", encoding="utf-8") as f:
            kadset.dump(data, f, ensure_ascii=False, indent=4)
        return kpath
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return path


def _config_file_exists(path):
    """配置文件存在性判定：json 或对应 kadset 任一存在即存在"""
    return os.path.exists(path) or os.path.exists(_kadset_file(path))


def _config_file_mtime(path):
    """配置文件最新 mtime：json / kadset 取较新者（都不存在返回 0）"""
    mtimes = []
    for p in (path, _kadset_file(path)):
        try:
            if os.path.exists(p):
                mtimes.append(os.path.getmtime(p))
        except OSError:
            pass
    return max(mtimes) if mtimes else 0.0

# ---- 运行时状态：每账号独立（config/<bot_id>/group.json）----
_account_states = {}  # bot_id(str) -> state dict（见 _new_state）

# 模块重载钩子（全局共享）
_reload_hooks = []

# ---- 线程局部当前账号上下文 ----
_tls = threading.local()


def _new_state():
    """创建一份空白账号状态（全部派生字段的默认值）"""
    return {
        "config": {},                 # group.json 完整 dict（live，随 reload 更新）
        "config_config": {},          # 根目录 config.json dict
        "config_last_modified": 0.0,
        "botid": "",
        "botname": "",
        "listening_qq_list": [],
        "target_group": "",
        "commands_map": {},
        "runtime_commandsinfo": {},      # autoreg 临时注册的命令简介（不写入配置文件）
        "runtime_commandscategory": {},  # autoreg 临时注册的命令分类（不写入配置文件）
        "autoreg_commands": {},          # autoreg 合并缓存：热重载时重新应用
        "autoreg_commandsinfo": {},
        "autoreg_commandscategory": {},
        "autoreg_bot_admin": [],
        "autoreg_group_admin": [],
        "autoreg_hidden": [],
        "admin_list": [],
        "bangroup_list": [],
        "autosinggps_list": [],
        "banrepgroup_list": [],
        "testgp": "",
        "testmode": False,
        "commandshidden": [],
        "onimagehelp": True,
        "gpauthgroups": [],
        "gpauthfrequency": 3,
        "gpauthtime": 300,
        "gpauth_configs": {},
        "autowelgps_list": [],
        "gpwel_configs": {},
        "autorecallgps_list": [],
        "gprecall_configs": {},
        "fkgps_list": [],
        "gpfk_configs": {},
        "group_admin_commands": [],
        "bot_admin_commands": [],
        "webui_dir": None,
        "webhook_port": _DEFAULT_WEBHOOK_PORT,
        "send_port": _DEFAULT_SEND_PORT,
        "Onebot_URL": f"http://127.0.0.1:{_DEFAULT_SEND_PORT}",
    }


def _to_list(data):
    """将 str（逗号分隔）或 list 归一化为字符串列表"""
    if isinstance(data, str):
        return [x.strip() for x in data.split(',')] if data else []
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


# ===================== 账号列表（account.json） =====================
def load_accounts():
    """读取根目录 account.json（json 优先，无则读 account.kadset），格式 {QQ: {webhook_port, send_port}}"""
    global _accounts
    _accounts = {}
    if _config_file_exists(_ACCOUNT_FILE):
        try:
            raw = _read_config_file(_ACCOUNT_FILE)
            if isinstance(raw, dict):
                for bot_id, ports in raw.items():
                    if not isinstance(ports, dict):
                        ports = {}
                    _accounts[str(bot_id)] = {
                        "webhook_port": int(ports.get("webhook_port", _DEFAULT_WEBHOOK_PORT)),
                        "send_port": int(ports.get("send_port", _DEFAULT_SEND_PORT)),
                    }
        except Exception as e:
            logger.error(f"读取 account.json 失败: {e}")
    if not _accounts:
        # 回退：从 config/ 目录自动发现账号
        _discover_accounts_from_config_dir()
    return _accounts


def _discover_accounts_from_config_dir():
    """account.json 缺失/为空时，从 config/<QQ>/ 目录自动发现账号（端口用默认值）"""
    if not os.path.isdir(_CONFIG_DIR):
        return
    for name in sorted(os.listdir(_CONFIG_DIR)):
        full = os.path.join(_CONFIG_DIR, name)
        if os.path.isdir(full) and name != "modcfg" and _config_file_exists(os.path.join(full, "group.json")):
            if name not in _accounts:
                _accounts[name] = {
                    "webhook_port": _DEFAULT_WEBHOOK_PORT,
                    "send_port": _DEFAULT_SEND_PORT,
                }


def get_accounts():
    """返回账号列表 dict：{bot_id: {"webhook_port": int, "send_port": int}}"""
    if not _accounts:
        load_accounts()
    return dict(_accounts)


def get_account_list():
    """返回账号 bot_id 列表（有序）"""
    return list(get_accounts().keys())


def get_default_bot_id():
    """默认账号 = account.json 第一个账号；无账号时回退 config/ 目录第一个子目录"""
    accounts = get_accounts()
    if accounts:
        return next(iter(accounts))
    if os.path.isdir(_CONFIG_DIR):
        for name in sorted(os.listdir(_CONFIG_DIR)):
            full = os.path.join(_CONFIG_DIR, name)
            if os.path.isdir(full) and name != "modcfg":
                return name
    return None


# ===================== 当前账号上下文（线程局部） =====================
def set_current_bot_id(bot_id):
    """设置当前线程的账号上下文（收信处理 / worker 任务开始时调用）"""
    _tls.bot_id = str(bot_id)


def get_current_bot_id():
    """返回当前线程的账号上下文 bot_id（未设置返回 None）"""
    return getattr(_tls, "bot_id", None)


def clear_current_bot_id():
    """清除当前线程的账号上下文"""
    if hasattr(_tls, "bot_id"):
        del _tls.bot_id


def _resolve_bot_id(bot_id=None):
    """解析 bot_id：显式指定 > 当前线程上下文 > 默认账号"""
    if bot_id is not None:
        return str(bot_id)
    cur = get_current_bot_id()
    if cur:
        return cur
    return get_default_bot_id()


# ===================== 群-账号映射（后台线程/定时任务回退用） =====================
# 收到群消息时记录 group_id -> bot_id，供 send.group 等函数在无线程上下文时回退查找
_group_account_map = {}


def register_group_account(group_id, bot_id):
    """记录群-账号映射（收到群消息时调用）"""
    if group_id is None or bot_id is None:
        return
    _group_account_map[str(group_id)] = str(bot_id)


def get_bot_id_by_group(group_id):
    """根据群号查找关联的账号 bot_id（未找到返回 None）

    用于后台线程/定时任务中调用 send.group 时，无线程上下文回退查找。
    同一群若被多账号管理，映射会被后收到的消息覆盖；
    多账号同群场景应由调用方显式传 bot_id。
    """
    if group_id is None:
        return None
    return _group_account_map.get(str(group_id))


def resolve_bot_id_by_group(group_id, bot_id=None):
    """群号辅助解析 bot_id：显式参数 > 线程上下文 > 群号映射 > 默认账号

    供 send.py 的 _url 在 bot_id=None 且无线程上下文时回退使用。
    """
    if bot_id is not None:
        return str(bot_id)
    cur = get_current_bot_id()
    if cur:
        return cur
    if group_id is not None:
        mapped = get_bot_id_by_group(group_id)
        if mapped:
            return mapped
    return get_default_bot_id()


def _get_state(bot_id=None):
    """获取账号状态 dict（不存在时创建空白状态，保证 getter 可用）"""
    resolved = _resolve_bot_id(bot_id)
    if resolved is None:
        return None
    state = _account_states.get(resolved)
    if state is None:
        state = _new_state()
        _account_states[resolved] = state
    return state


# ===================== 账号文件路径 =====================
def _account_dir(bot_id):
    return os.path.join(_CONFIG_DIR, str(bot_id))


def _group_file(bot_id):
    return os.path.join(_account_dir(bot_id), "group.json")


def _modcfg_dir(bot_id):
    return os.path.join(_account_dir(bot_id), "modcfg")


def _core_config_file(bot_id, name):
    return os.path.join(_account_dir(bot_id), name)


# ===================== 端口与 API 地址 =====================
def _resolve_ports(bot_id):
    """按优先级计算账号端口：account.json > group.json > 默认值"""
    webhook_port = None
    send_port = None
    acc = get_accounts().get(str(bot_id), {})
    if isinstance(acc, dict):
        webhook_port = acc.get("webhook_port")
        send_port = acc.get("send_port")
    if webhook_port is None or send_port is None:
        gfile = _group_file(bot_id)
        if _config_file_exists(gfile):
            try:
                gdata = _read_config_file(gfile)
                if isinstance(gdata, dict):
                    if webhook_port is None:
                        webhook_port = gdata.get("webhook_port")
                    if send_port is None:
                        send_port = gdata.get("send_port")
            except Exception:
                pass
    if webhook_port is None:
        webhook_port = _DEFAULT_WEBHOOK_PORT
    if send_port is None:
        send_port = _DEFAULT_SEND_PORT
    return int(webhook_port), int(send_port)


def get_Onebot_url(bot_id=None):
    state = _get_state(bot_id)
    if state is not None and state["Onebot_URL"]:
        return state["Onebot_URL"]
    resolved = _resolve_bot_id(bot_id)
    if resolved:
        _, send_port = _resolve_ports(resolved)
        return f"http://127.0.0.1:{send_port}"
    return f"http://127.0.0.1:{_DEFAULT_SEND_PORT}"


def get_webhook_port(bot_id=None):
    state = _get_state(bot_id)
    if state is not None and state["webhook_port"]:
        return state["webhook_port"]
    resolved = _resolve_bot_id(bot_id)
    if resolved:
        webhook_port, _ = _resolve_ports(resolved)
        return webhook_port
    return _DEFAULT_WEBHOOK_PORT


def get_send_port(bot_id=None):
    state = _get_state(bot_id)
    if state is not None and state["send_port"]:
        return state["send_port"]
    resolved = _resolve_bot_id(bot_id)
    if resolved:
        _, send_port = _resolve_ports(resolved)
        return send_port
    return _DEFAULT_SEND_PORT


# ===================== 通用 getter（按当前账号状态读取，签名向后兼容） =====================
def get_config(bot_id=None):
    """返回当前账号 group.json 实时 dict（live，随 reload 更新）"""
    state = _get_state(bot_id)
    return state["config"] if state else {}


def get(key, default=None, bot_id=None):
    state = _get_state(bot_id)
    return state["config"].get(key, default) if state else default


def get_config_config(bot_id=None):
    state = _get_state(bot_id)
    return state["config_config"] if state else {}


def get_botid(bot_id=None):
    state = _get_state(bot_id)
    return state["botid"] if state else ""


def get_botname(bot_id=None):
    state = _get_state(bot_id)
    return state["botname"] if state else ""


def get_listening_qq_list(bot_id=None):
    state = _get_state(bot_id)
    return state["listening_qq_list"] if state else []


def get_target_group(bot_id=None):
    state = _get_state(bot_id)
    return state["target_group"] if state else ""


def get_commands_map(bot_id=None):
    state = _get_state(bot_id)
    return state["commands_map"] if state else {}


def get_commandsinfo(bot_id=None):
    """返回合并后的命令简介：group.json 优先，autoreg 补充"""
    state = _get_state(bot_id)
    if state is None:
        return {}
    result = dict(state["runtime_commandsinfo"])
    cfg_info = state["config"].get("commandsinfo", {})
    if isinstance(cfg_info, dict):
        result.update(cfg_info)
    return result


def get_commandscategory(bot_id=None):
    """返回合并后的命令分类：group.json 优先，autoreg 补充"""
    state = _get_state(bot_id)
    if state is None:
        return {}
    result = dict(state["runtime_commandscategory"])
    cfg_cat = state["config"].get("commandscategory", {})
    if isinstance(cfg_cat, dict):
        result.update(cfg_cat)
    return result


def get_admin_list(bot_id=None):
    state = _get_state(bot_id)
    return state["admin_list"] if state else []


def get_bangroup_list(bot_id=None):
    state = _get_state(bot_id)
    return state["bangroup_list"] if state else []


def get_autosinggps_list(bot_id=None):
    state = _get_state(bot_id)
    return state["autosinggps_list"] if state else []


def get_testgp(bot_id=None):
    state = _get_state(bot_id)
    return state["testgp"] if state else ""


def get_testmode(bot_id=None):
    state = _get_state(bot_id)
    return state["testmode"] if state else False


def get_commandshidden(bot_id=None):
    state = _get_state(bot_id)
    return state["commandshidden"] if state else []


def get_onimagehelp(bot_id=None):
    state = _get_state(bot_id)
    return state["onimagehelp"] if state else True


def get_gpauthgroups(bot_id=None):
    state = _get_state(bot_id)
    return state["gpauthgroups"] if state else []


def get_gpauthfrequency(bot_id=None):
    state = _get_state(bot_id)
    return state["gpauthfrequency"] if state else 3


def get_gpauthtime(bot_id=None):
    state = _get_state(bot_id)
    return state["gpauthtime"] if state else 300


def get_gpauth_configs(bot_id=None):
    state = _get_state(bot_id)
    return state["gpauth_configs"] if state else {}


def get_autowelgps_list(bot_id=None):
    state = _get_state(bot_id)
    return state["autowelgps_list"] if state else []


def get_gpwel_configs(bot_id=None):
    state = _get_state(bot_id)
    return state["gpwel_configs"] if state else {}


def get_autorecallgps_list(bot_id=None):
    state = _get_state(bot_id)
    return state["autorecallgps_list"] if state else []


def get_gprecall_configs(bot_id=None):
    state = _get_state(bot_id)
    return state["gprecall_configs"] if state else {}


def get_fkgps_list(bot_id=None):
    state = _get_state(bot_id)
    return state["fkgps_list"] if state else []


def get_gpfk_configs(bot_id=None):
    state = _get_state(bot_id)
    return state["gpfk_configs"] if state else {}


def get_group_admin_commands(bot_id=None):
    state = _get_state(bot_id)
    return state["group_admin_commands"] if state else []


def get_bot_admin_commands(bot_id=None):
    state = _get_state(bot_id)
    return state["bot_admin_commands"] if state else []


def get_webui_dir(bot_id=None):
    state = _get_state(bot_id)
    return state["webui_dir"] if state else None


# ===================== 配置修改检测 / 保存 =====================
def check_modified(bot_id=None):
    """检查当前账号 group.json（或 group.kadset）是否被外部修改（mtime 变化）"""
    resolved = _resolve_bot_id(bot_id)
    if not resolved:
        return False
    state = _get_state(resolved)
    try:
        gfile = _group_file(resolved)
        mtime = _config_file_mtime(gfile)
        if mtime > 0:
            return mtime > state["config_last_modified"]
    except Exception as e:
        logger.error(f"检查配置文件修改状态时出错: {e}")
    return False


def save(data=None, bot_id=None):
    """写回当前账号 group.json（原 kadset 文件保持 kadset 格式）"""
    resolved = _resolve_bot_id(bot_id)
    if not resolved:
        return False
    state = _get_state(resolved)
    try:
        if data is not None:
            state["config"] = data
        gfile = _group_file(resolved)
        os.makedirs(os.path.dirname(gfile), exist_ok=True)
        written = _write_config_file(gfile, state["config"])
        state["config_last_modified"] = os.path.getmtime(written)
        return True
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        return False


# ===================== 加载 / 重载 =====================
def load():
    """首次加载：遍历所有账号加载 group.json / config.json"""
    load_accounts()
    for bot_id in get_account_list():
        _load_account(bot_id)


def _load_account(bot_id):
    """加载单个账号的 group.json / config.json（json 优先，kadset 兜底）到账号状态"""
    state = _get_state(bot_id)
    gfile = _group_file(bot_id)
    try:
        if _config_file_exists(gfile):
            state["config"] = _read_config_file(gfile)
            _apply_group_data(state, state["config"])
            state["config_last_modified"] = _config_file_mtime(gfile)
            logger.info(f"账号{bot_id} 欢迎使用{state['botname']} {get_mjbcver_raw()}")
            logger.info(f"账号{bot_id} 已加载命令配置: {', '.join(state['commands_map'].keys())}")
            logger.info(f"账号{bot_id} 已读取本地配置文件，正在部署...")
            if state["testmode"]:
                logger.info(f"账号{bot_id} 调试模式已开启，仅群{state['testgp']}可触发bot")
        else:
            logger.error(f"账号{bot_id} 未找到 group.json/group.kadset，请先配置: {gfile}")
    except Exception as e:
        logger.error(f"账号{bot_id} 读取配置文件失败: {e}")

    try:
        if _config_file_exists(_CONFIG_FILE):
            state["config_config"] = _read_config_file(_CONFIG_FILE)
    except Exception as e:
        logger.error(f"读取 config.json 失败: {e}")


def reload(bot_id=None):
    """热重载 group.json 并触发模块重载钩子

    bot_id=None 时默认重载全部账号（保留各账号已有 autoreg 缓存并重放）。
    """
    if bot_id is None:
        target_ids = get_account_list()
        if not target_ids:
            default = get_default_bot_id()
            target_ids = [default] if default else []
    else:
        target_ids = [str(bot_id)]
    if not target_ids:
        return False
    results = [_reload_account(bid) for bid in target_ids]
    return all(results)


def _reload_account(bot_id):
    """重载单个账号配置并触发模块重载钩子（在账号上下文中执行）"""
    state = _get_state(bot_id)
    gfile = _group_file(bot_id)
    try:
        if not _config_file_exists(gfile):
            logger.error(f"账号{bot_id} 未找到 group.json/group.kadset，无法重载")
            return False
        state["config"] = _read_config_file(gfile)
        _apply_group_data(state, state["config"])
        state["config_last_modified"] = _config_file_mtime(gfile)
        logger.info(f"账号{bot_id} 配置文件已重载")
        logger.info(
            f"账号{bot_id} 机器人名称: {state['botname']} / 内核版本: {get_mjbcver_raw()} / 机器人QQ: {state['botid']}"
        )
        logger.info(f"账号{bot_id} 监听QQ列表: {', '.join(state['listening_qq_list'])}")
        logger.info(f"账号{bot_id} 管理员列表: {', '.join(state['admin_list'])}")
        logger.info(f"账号{bot_id} 功能禁用群列表: {', '.join(state['bangroup_list'])}")

        # 设置账号上下文执行模块重载钩子与主群提示（send 无参调用路由到该账号）
        prev = get_current_bot_id()
        set_current_bot_id(bot_id)
        try:
            for hook in list(_reload_hooks):
                try:
                    hook()
                except Exception as e:
                    logger.error(f"执行重载钩子失败: {e}")

            # 在主群发送重载提示（延迟导入 send 避免循环依赖）
            try:
                from bin import send
                if state["target_group"] and state["target_group"] != "0":
                    send.group(state["target_group"], "配置文件已自动重载")
            except Exception as e:
                logger.error(f"发送重载提示失败: {e}")
        finally:
            if prev is None:
                clear_current_bot_id()
            else:
                set_current_bot_id(prev)
        return True
    except Exception as e:
        logger.error(f"重载配置文件失败: {e}")
        return False


# ===================== 模块重载钩子（全局） =====================
def register_reload_hook(fn):
    """模块注册重载钩子，reload() 时会调用以重载模块自身数据"""
    if fn not in _reload_hooks:
        _reload_hooks.append(fn)


# ===================== autoreg 缓存（按账号状态） =====================
def _apply_autoreg_cache(state):
    """将账号状态中的 autoreg 缓存重新应用到运行时变量（热重载后保持模块注册的命令）"""
    for key, value in state["autoreg_commands"].items():
        if key not in state["commands_map"]:
            state["commands_map"][key] = value
    for key, value in state["autoreg_commandsinfo"].items():
        if key not in state["runtime_commandsinfo"]:
            state["runtime_commandsinfo"][key] = value
    for key, value in state["autoreg_commandscategory"].items():
        if key not in state["runtime_commandscategory"]:
            state["runtime_commandscategory"][key] = value
    for cmd in state["autoreg_bot_admin"]:
        if cmd not in state["bot_admin_commands"]:
            state["bot_admin_commands"].append(cmd)
    for cmd in state["autoreg_group_admin"]:
        if cmd not in state["group_admin_commands"]:
            state["group_admin_commands"].append(cmd)
    for cmd in state["autoreg_hidden"]:
        if cmd not in state["commandshidden"]:
            state["commandshidden"].append(cmd)


def _apply_group_data(state, group_data):
    """根据当前账号 group.json dict 更新该账号全部运行时派生状态"""
    state["botid"] = str(group_data.get("bqq", 0))
    state["botname"] = str(group_data.get("botname", "mjbcore"))
    state["listening_qq_list"] = _to_list(group_data.get("listeningqq", ""))
    state["target_group"] = str(group_data.get("group", ""))

    commands_data = group_data.get("commands", {})
    if isinstance(commands_data, dict):
        state["commands_map"].clear()
        state["commands_map"].update(commands_data)

    # 重置 autoreg 临时注册的运行时变量（reload 时清空旧值，稍后由缓存重放）
    state["runtime_commandsinfo"].clear()
    state["runtime_commandscategory"].clear()

    state["onimagehelp"] = bool(group_data.get("onimagehelp", True))
    state["banrepgroup_list"] = _to_list(group_data.get("banrepgroup", []))
    state["admin_list"] = _to_list(group_data.get("admin", []))
    state["autosinggps_list"] = _to_list(group_data.get("autosinggps", []))
    state["bangroup_list"] = _to_list(group_data.get("bangroup", []))
    state["commandshidden"] = _to_list(group_data.get("commandshidden", []))
    state["testgp"] = str(group_data.get("testgp", ""))
    state["testmode"] = bool(group_data.get("testmode", False))

    state["gpauthgroups"] = _to_list(group_data.get("autoauthgps", []))
    state["gpauthfrequency"] = group_data.get("gpauthfrequency", 3)
    state["gpauthtime"] = group_data.get("gpauthtime", 300)
    state["gpauth_configs"] = group_data.get("gpauth_configs", {})

    state["autowelgps_list"] = _to_list(group_data.get("autowelgps", []))
    state["gpwel_configs"] = group_data.get("gpwel_configs", {})
    state["autorecallgps_list"] = _to_list(group_data.get("autorecallgps", []))
    state["gprecall_configs"] = group_data.get("gprecall_configs", {})

    state["fkgps_list"] = _to_list(group_data.get("fkgps", []))
    # 单群屏蔽词配置从当前账号模块配置目录读取
    state["gpfk_configs"] = load_module_config("gpfk_configs.json", bot_id=state["botid"])

    state["group_admin_commands"] = _to_list(group_data.get("group_admin_commands", []))
    state["bot_admin_commands"] = _to_list(group_data.get("bot_admin_commands", []))

    # WebUI 目录
    webui_path = group_data.get("webui_path", None)
    state["webui_dir"] = None
    if webui_path:
        if not os.path.isabs(webui_path):
            state["webui_dir"] = os.path.join(_ROOT, webui_path)
        else:
            state["webui_dir"] = webui_path
        if not os.path.exists(state["webui_dir"]):
            state["webui_dir"] = None

    # Webhook 端口和 HTTP 发送端口：account.json 优先、回退 group.json、再回退默认值
    webhook_port = None
    send_port = None
    acc = get_accounts().get(state["botid"], {})
    if isinstance(acc, dict):
        webhook_port = acc.get("webhook_port")
        send_port = acc.get("send_port")
    if webhook_port is None:
        webhook_port = group_data.get("webhook_port", _DEFAULT_WEBHOOK_PORT)
    if send_port is None:
        send_port = group_data.get("send_port", _DEFAULT_SEND_PORT)
    state["webhook_port"] = int(webhook_port)
    state["send_port"] = int(send_port)
    state["Onebot_URL"] = f"http://127.0.0.1:{state['send_port']}"

    # 重新应用 autoreg 缓存（热重载后保持模块注册的命令）
    _apply_autoreg_cache(state)


def _apply_module_autoreg_to(autoreg, bot_id):
    """将模块 autoreg 合并到指定账号的运行时状态，返回合并项描述列表"""
    state = _get_state(bot_id)
    merged_keys = []

    # commands 合并到运行时 commands_map 并写入缓存（不写 _config）
    autoreg_commands = autoreg.get("commands", {})
    if isinstance(autoreg_commands, dict):
        for key, value in autoreg_commands.items():
            state["autoreg_commands"][key] = value
            if key not in state["commands_map"]:
                state["commands_map"][key] = value
        merged_keys.append(f"commands={list(autoreg_commands.keys())}")

    # commandsinfo 合并到运行时变量并写入缓存（group.json 中已有的项优先）
    autoreg_info = autoreg.get("commandsinfo", {})
    if isinstance(autoreg_info, dict):
        for key, value in autoreg_info.items():
            state["autoreg_commandsinfo"][key] = value
            if key not in state["runtime_commandsinfo"]:
                state["runtime_commandsinfo"][key] = value
        merged_keys.append(f"commandsinfo={len(autoreg_info)}项")

    # commandscategory 合并到运行时变量并写入缓存（group.json 中已有的项优先）
    autoreg_cat = autoreg.get("commandscategory", {})
    if isinstance(autoreg_cat, dict):
        for key, value in autoreg_cat.items():
            state["autoreg_commandscategory"][key] = value
            if key not in state["runtime_commandscategory"]:
                state["runtime_commandscategory"][key] = value
        merged_keys.append(f"commandscategory={len(autoreg_cat)}项")

    # bot_admin_commands 合并到运行时变量并写入缓存（不写 _config）
    autoreg_bot_admin = _to_list(autoreg.get("bot_admin_commands", []))
    if autoreg_bot_admin:
        for cmd in autoreg_bot_admin:
            if cmd not in state["autoreg_bot_admin"]:
                state["autoreg_bot_admin"].append(cmd)
            if cmd not in state["bot_admin_commands"]:
                state["bot_admin_commands"].append(cmd)
        merged_keys.append(f"bot_admin_commands={autoreg_bot_admin}")

    # group_admin_commands 合并到运行时变量并写入缓存（不写 _config）
    autoreg_group_admin = _to_list(autoreg.get("group_admin_commands", []))
    if autoreg_group_admin:
        for cmd in autoreg_group_admin:
            if cmd not in state["autoreg_group_admin"]:
                state["autoreg_group_admin"].append(cmd)
            if cmd not in state["group_admin_commands"]:
                state["group_admin_commands"].append(cmd)
        merged_keys.append(f"group_admin_commands={autoreg_group_admin}")

    # commandshidden 合并到运行时变量并写入缓存（不写 _config）
    autoreg_hidden = _to_list(autoreg.get("commandshidden", []))
    if autoreg_hidden:
        for cmd in autoreg_hidden:
            if cmd not in state["autoreg_hidden"]:
                state["autoreg_hidden"].append(cmd)
            if cmd not in state["commandshidden"]:
                state["commandshidden"].append(cmd)
        merged_keys.append(f"commandshidden={autoreg_hidden}")

    return merged_keys


def apply_module_autoreg(autoreg, module_name="", bot_id=None):
    """将模块 modcfg().autoreg 中的命令注册表合并到运行时状态（不写入配置文件）

    autoreg 作为模块自带的"默认注册表"，仅在运行时变量中临时注册，
    group.json 已有的项优先（用户可在 group.json 中覆盖模块默认：改名/改简介/禁用等）。

    支持的字段（与 group.json 同名同结构）：
        commands              dict   触发词 -> 函数名
        commandsinfo          dict   触发词 -> 简介
        commandscategory      dict   触发词 -> 分类
        bot_admin_commands    list   Bot管理员命令触发词
        group_admin_commands  list   群管理员命令触发词
        commandshidden        list   隐藏命令触发词

    Args:
        autoreg: dict，模块 modcfg() 返回的 autoreg 字段
        module_name: 模块名（仅用于日志）
        bot_id: 目标账号；None 表示应用到所有账号
    """
    if not isinstance(autoreg, dict):
        return
    if bot_id is None:
        target_ids = get_account_list()
        if not target_ids:
            default = get_default_bot_id()
            target_ids = [default] if default else []
    else:
        target_ids = [str(bot_id)]
    if not target_ids:
        return

    prefix = f"[模块{module_name}]" if module_name else "[autoreg]"
    for bid in target_ids:
        merged_keys = _apply_module_autoreg_to(autoreg, bid)
        if merged_keys:
            logger.info(f"账号{bid} {prefix} 已自动注册: {', '.join(merged_keys)}")


# ===================== 模块配置（config/<bot_id>/modcfg/） =====================
def load_module_config(name, bot_id=None):
    """从 config/<bot_id>/modcfg/ 读取模块配置（json 优先，kadset 兜底）；不存在返回 {}"""
    resolved = _resolve_bot_id(bot_id)
    if not resolved:
        return {}
    paths = [os.path.join(_modcfg_dir(resolved), name), _core_config_file(resolved, name)]
    for path in paths:
        if _config_file_exists(path):
            try:
                return _read_config_file(path)
            except Exception as e:
                logger.error(f"加载模块配置 {name} 失败: {e}")
                return {}
    return {}


def save_module_config(name, data, bot_id=None):
    """写入模块配置到 config/<bot_id>/modcfg/（原 kadset 文件保持 kadset 格式）"""
    resolved = _resolve_bot_id(bot_id)
    if not resolved:
        return False
    try:
        mdir = _modcfg_dir(resolved)
        os.makedirs(mdir, exist_ok=True)
        path = os.path.join(mdir, name)
        _write_config_file(path, data)
        return True
    except Exception as e:
        logger.error(f"保存模块配置 {name} 失败: {e}")
        return False


# ===================== 核心配置（config/<bot_id>/） =====================
def load_banuser(bot_id=None):
    """读取黑名单 banuser.json（json 优先，kadset 兜底）-> {"banned_users": {...}}"""
    resolved = _resolve_bot_id(bot_id)
    if not resolved:
        return {}
    path = _core_config_file(resolved, "banuser.json")
    if _config_file_exists(path):
        try:
            data = _read_config_file(path)
            if isinstance(data, dict):
                return data.get("banned_users", {})
        except Exception as e:
            logger.error(f"读取banuser.json失败: {e}")
    return {}


def save_banuser(banned_users, bot_id=None):
    resolved = _resolve_bot_id(bot_id)
    if not resolved:
        return False
    try:
        path = _core_config_file(resolved, "banuser.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write_config_file(path, {"banned_users": banned_users})
        return True
    except Exception as e:
        logger.error(f"保存banuser.json失败: {e}")
        return False


def load_gpevent(bot_id=None):
    resolved = _resolve_bot_id(bot_id)
    if not resolved:
        return {}
    path = _core_config_file(resolved, "gpevent.json")
    if _config_file_exists(path):
        try:
            data = _read_config_file(path)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.error(f"读取gpevent.json失败: {e}")
    return {}


def save_gpevent(data, bot_id=None):
    resolved = _resolve_bot_id(bot_id)
    if not resolved:
        return False
    try:
        path = _core_config_file(resolved, "gpevent.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write_config_file(path, data)
        return True
    except Exception as e:
        logger.error(f"保存gpevent.json失败: {e}")
        return False


def load_gpauths(bot_id=None):
    resolved = _resolve_bot_id(bot_id)
    if not resolved:
        return {}
    path = _core_config_file(resolved, "gpauths.json")
    if _config_file_exists(path):
        try:
            data = _read_config_file(path)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.error(f"读取gpauths.json失败: {e}")
    return {}


def save_gpauths(data, bot_id=None):
    resolved = _resolve_bot_id(bot_id)
    if not resolved:
        return False
    try:
        path = _core_config_file(resolved, "gpauths.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write_config_file(path, data)
        return True
    except Exception as e:
        logger.error(f"保存gpauths.json失败: {e}")
        return False
