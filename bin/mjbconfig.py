# -*- coding: utf-8 -*-
import os
import json

from bin import logger

# ---- 路径常量 ----
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录
_GROUP_FILE = os.path.join(_ROOT, "group.json")
_CONFIG_FILE = os.path.join(_ROOT, "config.json")
_MODULES_CONFIG_DIR = os.path.join(_ROOT, "modules", "config")

# 核心配置文件（保留在根目录）
_BANUSER_FILE = os.path.join(_ROOT, "banuser.json")
_GPEVENT_FILE = os.path.join(_ROOT, "gpevent.json")
_GPAUTHS_FILE = os.path.join(_ROOT, "gpauths.json")

# LLbotQQ HTTP API 地址（send_port 从 group.json 读取，默认 3002）
_send_port = 3002
_webhook_port = 9762
_LLbot_URL = "http://127.0.0.1:3002"

# ---- 运行时状态 ----
_config = {}                 # group.json 完整 dict
_config_config = {}          # config.json dict
_config_last_modified = 0.0

# 归一化后的派生状态
botid = ""
botname = ""
version = ""
listening_qq_list = []
target_group = ""
commands_map = {}
admin_list = []
bangroup_list = []
autosinggps_list = []
banrepgroup_list = []
testgp = ""
testmode = False
commandshidden = []
onimagehelp = True
gpauthgroups = []
gpauthfrequency = 3
gpauthtime = 300
gpauth_configs = {}
autowelgps_list = []
gpwel_configs = {}
autorecallgps_list = []
gprecall_configs = {}
fkgps_list = []
gpfk_configs = {}
group_admin_commands = []
bot_admin_commands = []
webui_dir = None

# 模块重载钩子
_reload_hooks = []


def _to_list(data):
    """将 str（逗号分隔）或 list 归一化为字符串列表"""
    if isinstance(data, str):
        return [x.strip() for x in data.split(',')] if data else []
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


def register_reload_hook(fn):
    """模块注册重载钩子，reload() 时会调用以重载模块自身数据"""
    if fn not in _reload_hooks:
        _reload_hooks.append(fn)


def get_LLbot_url():
    return _LLbot_URL


def get_webhook_port():
    return _webhook_port


def get_send_port():
    return _send_port


def get_config():
    """返回 group.json 实时 dict（live，随 reload 更新）"""
    return _config


def get(key, default=None):
    return _config.get(key, default)


def get_config_config():
    return _config_config


def get_botid():
    return botid


def get_botname():
    return botname


def get_version():
    return version


def get_listening_qq_list():
    return listening_qq_list


def get_target_group():
    return target_group


def get_commands_map():
    return commands_map


def get_admin_list():
    return admin_list


def get_bangroup_list():
    return bangroup_list


def get_autosinggps_list():
    return autosinggps_list


def get_testgp():
    return testgp


def get_testmode():
    return testmode


def get_commandshidden():
    return commandshidden


def get_onimagehelp():
    return onimagehelp


def get_gpauthgroups():
    return gpauthgroups


def get_gpauthfrequency():
    return gpauthfrequency


def get_gpauthtime():
    return gpauthtime


def get_gpauth_configs():
    return gpauth_configs


def get_autowelgps_list():
    return autowelgps_list


def get_gpwel_configs():
    return gpwel_configs


def get_autorecallgps_list():
    return autorecallgps_list


def get_gprecall_configs():
    return gprecall_configs


def get_fkgps_list():
    return fkgps_list


def get_gpfk_configs():
    return gpfk_configs


def get_group_admin_commands():
    return group_admin_commands


def get_bot_admin_commands():
    return bot_admin_commands


def get_webui_dir():
    return webui_dir


def check_modified():
    """检查 group.json 是否被外部修改（mtime 变化）"""
    try:
        if os.path.exists(_GROUP_FILE):
            return os.path.getmtime(_GROUP_FILE) > _config_last_modified
    except Exception as e:
        logger.error(f"检查配置文件修改状态时出错: {e}")
    return False


def _apply_group_data(group_data):
    """根据 group.json dict 更新全部运行时派生状态"""
    global botid, botname, version, listening_qq_list, target_group, commands_map
    global admin_list, bangroup_list, autosinggps_list, banrepgroup_list
    global testgp, testmode, commandshidden, onimagehelp
    global gpauthgroups, gpauthfrequency, gpauthtime, gpauth_configs
    global autowelgps_list, gpwel_configs, autorecallgps_list, gprecall_configs
    global fkgps_list, gpfk_configs, group_admin_commands, bot_admin_commands, webui_dir
    global _send_port, _webhook_port, _LLbot_URL

    botid = str(group_data.get("bqq", 0))
    botname = str(group_data.get("botname", "硫酸钠"))
    version = str(group_data.get("version", "1.0.0"))
    listening_qq_list = _to_list(group_data.get("listeningqq", ""))
    target_group = str(group_data.get("group", ""))

    commands_data = group_data.get("commands", {})
    if isinstance(commands_data, dict):
        commands_map.clear()
        commands_map.update(commands_data)

    onimagehelp = bool(group_data.get("onimagehelp", True))
    banrepgroup_list = _to_list(group_data.get("banrepgroup", []))
    admin_list = _to_list(group_data.get("admin", []))
    autosinggps_list = _to_list(group_data.get("autosinggps", []))
    bangroup_list = _to_list(group_data.get("bangroup", []))
    commandshidden = _to_list(group_data.get("commandshidden", []))
    testgp = str(group_data.get("testgp", ""))
    testmode = bool(group_data.get("testmode", False))

    gpauthgroups = _to_list(group_data.get("autoauthgps", []))
    gpauthfrequency = group_data.get("gpauthfrequency", 3)
    gpauthtime = group_data.get("gpauthtime", 300)
    gpauth_configs = group_data.get("gpauth_configs", {})

    autowelgps_list = _to_list(group_data.get("autowelgps", []))
    gpwel_configs = group_data.get("gpwel_configs", {})
    autorecallgps_list = _to_list(group_data.get("autorecallgps", []))
    gprecall_configs = group_data.get("gprecall_configs", {})

    fkgps_list = _to_list(group_data.get("fkgps", []))
    # 单群屏蔽词配置从模块配置目录读取
    gpfk_configs = load_module_config("gpfk_configs.json")

    group_admin_commands = _to_list(group_data.get("group_admin_commands", []))
    bot_admin_commands = _to_list(group_data.get("bot_admin_commands", []))

    # WebUI 目录
    webui_path = group_data.get("webui_path", None)
    webui_dir = None
    if webui_path:
        if not os.path.isabs(webui_path):
            webui_dir = os.path.join(_ROOT, webui_path)
        else:
            webui_dir = webui_path
        if not os.path.exists(webui_dir):
            webui_dir = None

    # Webhook 端口和 HTTP 发送端口（从 group.json 读取）
    _webhook_port = int(group_data.get("webhook_port", 9762))
    _send_port = int(group_data.get("send_port", 3002))
    _LLbot_URL = f"http://127.0.0.1:{_send_port}"


def load():
    """首次加载 group.json / config.json"""
    global _config, _config_config, _config_last_modified
    try:
        if os.path.exists(_GROUP_FILE):
            with open(_GROUP_FILE, "r", encoding="utf-8") as f:
                _config = json.load(f)
            _apply_group_data(_config)
            _config_last_modified = os.path.getmtime(_GROUP_FILE)
            logger.info(f"欢迎使用{botname} {version}")
            logger.info(f"已加载命令配置: {', '.join(commands_map.keys())}")
            logger.info("已读取本地配置文件，正在部署...")
            if testmode:
                logger.info(f"调试模式已开启，仅群{testgp}可触发bot")
        else:
            logger.error("未找到 group.json，请先配置")
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")

    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                _config_config = json.load(f)
    except Exception as e:
        logger.error(f"读取 config.json 失败: {e}")


def reload():
    """热重载 group.json 并触发模块重载钩子"""
    global _config, _config_last_modified
    try:
        if os.path.exists(_GROUP_FILE):
            with open(_GROUP_FILE, "r", encoding="utf-8") as f:
                _config = json.load(f)
            _apply_group_data(_config)
            _config_last_modified = os.path.getmtime(_GROUP_FILE)
            logger.info("配置文件已重载")
            logger.info(f"机器人名称: {botname} / 内核版本: {version} / 机器人QQ: {botid}")
            logger.info(f"监听QQ列表: {', '.join(listening_qq_list)}")
            logger.info(f"管理员列表: {', '.join(admin_list)}")
            logger.info(f"功能禁用群列表: {', '.join(bangroup_list)}")

            # 触发模块重载钩子（如拍砖数据、屏蔽词等）
            for hook in list(_reload_hooks):
                try:
                    hook()
                except Exception as e:
                    logger.error(f"执行重载钩子失败: {e}")

            # 在主群发送重载提示（延迟导入 send 避免循环依赖）
            try:
                from bin import send
                if target_group and target_group != "0":
                    send.group(target_group, "配置文件已自动重载")
            except Exception as e:
                logger.error(f"发送重载提示失败: {e}")
            return True
    except Exception as e:
        logger.error(f"重载配置文件失败: {e}")
        return False


def apply_module_autoreg(autoreg, module_name=""):
    """将模块 modcfg().autoreg 中的命令注册表合并到运行时状态

    autoreg 作为模块自带的"默认注册表"，group.json 已有的项优先
    （用户可在 group.json 中覆盖模块默认：改名/改简介/禁用等）。

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
    """
    global commandshidden, group_admin_commands, bot_admin_commands

    if not isinstance(autoreg, dict):
        return

    prefix = f"[模块{module_name}]" if module_name else "[autoreg]"
    merged_keys = []

    # commands 合并到 commands_map 与 _config["commands"]
    autoreg_commands = autoreg.get("commands", {})
    if isinstance(autoreg_commands, dict):
        cfg_commands = _config.setdefault("commands", {})
        for key, value in autoreg_commands.items():
            if key not in cfg_commands and key not in commands_map:
                cfg_commands[key] = value
                commands_map[key] = value
        merged_keys.append(f"commands={list(autoreg_commands.keys())}")

    # commandsinfo 合并
    autoreg_info = autoreg.get("commandsinfo", {})
    if isinstance(autoreg_info, dict):
        cfg_info = _config.setdefault("commandsinfo", {})
        for key, value in autoreg_info.items():
            if key not in cfg_info:
                cfg_info[key] = value
        merged_keys.append(f"commandsinfo={len(autoreg_info)}项")

    # commandscategory 合并
    autoreg_cat = autoreg.get("commandscategory", {})
    if isinstance(autoreg_cat, dict):
        cfg_cat = _config.setdefault("commandscategory", {})
        for key, value in autoreg_cat.items():
            if key not in cfg_cat:
                cfg_cat[key] = value
        merged_keys.append(f"commandscategory={len(autoreg_cat)}项")

    # bot_admin_commands 合并
    autoreg_bot_admin = _to_list(autoreg.get("bot_admin_commands", []))
    if autoreg_bot_admin:
        cfg_bot_admin = _config.setdefault("bot_admin_commands", [])
        for cmd in autoreg_bot_admin:
            if cmd not in bot_admin_commands:
                bot_admin_commands.append(cmd)
            if cmd not in cfg_bot_admin:
                cfg_bot_admin.append(cmd)
        merged_keys.append(f"bot_admin_commands={autoreg_bot_admin}")

    # group_admin_commands 合并
    autoreg_group_admin = _to_list(autoreg.get("group_admin_commands", []))
    if autoreg_group_admin:
        cfg_group_admin = _config.setdefault("group_admin_commands", [])
        for cmd in autoreg_group_admin:
            if cmd not in group_admin_commands:
                group_admin_commands.append(cmd)
            if cmd not in cfg_group_admin:
                cfg_group_admin.append(cmd)
        merged_keys.append(f"group_admin_commands={autoreg_group_admin}")

    # commandshidden 合并
    autoreg_hidden = _to_list(autoreg.get("commandshidden", []))
    if autoreg_hidden:
        cfg_hidden = _config.setdefault("commandshidden", [])
        for cmd in autoreg_hidden:
            if cmd not in commandshidden:
                commandshidden.append(cmd)
            if cmd not in cfg_hidden:
                cfg_hidden.append(cmd)
        merged_keys.append(f"commandshidden={autoreg_hidden}")

    if merged_keys:
        logger.info(f"{prefix} 已自动注册: {', '.join(merged_keys)}")


def save(data=None):
    """写回 group.json"""
    global _config, _config_last_modified
    try:
        if data is not None:
            _config = data
        with open(_GROUP_FILE, "w", encoding="utf-8") as f:
            json.dump(_config, f, ensure_ascii=False, indent=4)
        _config_last_modified = os.path.getmtime(_GROUP_FILE)
        return True
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        return False


# ---- 模块配置（modules/config/）----
def load_module_config(name):
    """从 modules/config/ 读取模块配置；为兼容迁移，回退到根目录"""
    paths = [os.path.join(_MODULES_CONFIG_DIR, name), os.path.join(_ROOT, name)]
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载模块配置 {name} 失败: {e}")
                return {}
    return {}


def save_module_config(name, data):
    """写入模块配置到 modules/config/"""
    try:
        os.makedirs(_MODULES_CONFIG_DIR, exist_ok=True)
        path = os.path.join(_MODULES_CONFIG_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"保存模块配置 {name} 失败: {e}")
        return False


# ---- 核心配置（根目录）----
def load_banuser():
    """读取黑名单 banuser.json -> {"banned_users": {...}}"""
    if os.path.exists(_BANUSER_FILE):
        try:
            with open(_BANUSER_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("banned_users", {})
        except Exception as e:
            logger.error(f"读取banuser.json失败: {e}")
    return {}


def save_banuser(banned_users):
    try:
        with open(_BANUSER_FILE, "w", encoding="utf-8") as f:
            json.dump({"banned_users": banned_users}, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"保存banuser.json失败: {e}")
        return False


def load_gpevent():
    if os.path.exists(_GPEVENT_FILE):
        try:
            with open(_GPEVENT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取gpevent.json失败: {e}")
    return {}


def save_gpevent(data):
    try:
        with open(_GPEVENT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"保存gpevent.json失败: {e}")
        return False


def load_gpauths():
    if os.path.exists(_GPAUTHS_FILE):
        try:
            with open(_GPAUTHS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取gpauths.json失败: {e}")
    return {}


def save_gpauths(data):
    try:
        with open(_GPAUTHS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"保存gpauths.json失败: {e}")
        return False
