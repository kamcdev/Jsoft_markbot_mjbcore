# -*- coding: utf-8 -*-
import hashlib
import threading

# 固定种子：变更会让所有代理值失效（仅影响运行期映射，不落盘）
_SEED = "mjbc-core-official-id-v1"
_DIGITS = 30
_LOWER = 10 ** (_DIGITS - 1)          # 10^29，保证结果恒为 30 位
_SPAN = 10 ** _DIGITS - _LOWER

_lock = threading.Lock()
_to_proxy = {}   # 官方 ID -> 30 位数字
_to_raw = {}     # 30 位数字 -> 官方 ID

# 官方 ID 形如 32 位十六进制；这里放宽到「≥16 位的十六进制串」，避免把 "未知" 等文本误映射
_OFFICIAL_MIN_LEN = 16
_HEX_CHARS = set("0123456789abcdefABCDEF")


def is_proxy(value):
    """是否为 30 位数字代理值"""
    s = str(value)
    return len(s) == _DIGITS and s.isdigit()


def looks_official(value):
    """是否像官方版 OneBot 的十六进制 ID"""
    s = str(value)
    return len(s) >= _OFFICIAL_MIN_LEN and set(s) <= _HEX_CHARS


def to_proxy(raw_id):
    """官方字母 ID -> 30 位数字代理值（纯数字或非官方 ID 原样返回）"""
    s = str(raw_id)
    if s.isdigit() or not looks_official(s):
        return s
    with _lock:
        hit = _to_proxy.get(s)
        if hit is not None:
            return hit
        digest = hashlib.blake2b((_SEED + s).encode("utf-8"), digest_size=16).digest()
        cand = _LOWER + int.from_bytes(digest, "big") % _SPAN
        # 哈希碰撞时线性探测，保证映射可逆
        while str(cand) in _to_raw:
            cand += 1
            if cand >= _LOWER + _SPAN:
                cand = _LOWER
        num = str(cand)
        _to_proxy[s] = num
        _to_raw[num] = s
        return num


def to_raw(value):
    """30 位数字代理值 -> 官方字母 ID（非代理值原样返回）"""
    if not is_proxy(value):
        return value
    with _lock:
        return _to_raw.get(str(value), value)


def wire_id(value):
    """框架内部 ID -> OneBot 接口参数

    30 位代理值还原成官方 ID；纯数字转 int（与原有行为一致）；官方字母 ID 原样透传。
    """
    if value is None or isinstance(value, bool):
        return value
    s = str(value)
    if is_proxy(s):
        return to_raw(s)
    if s.isdigit():
        return int(s)
    return value


def module_id(value):
    """OneBot 事件 ID -> 交给模块的 ID

    纯数字 ID 返回 int（与普通版行为一致），官方字母 ID 返回 30 位数字代理值（字符串）。
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    s = str(value)
    if s.isdigit():
        return int(s)
    return to_proxy(s)


def module_data(data, keys=("group_id", "user_id", "message_id", "target_id", "operator_id")):
    """返回给模块的数据副本：把指定键的 ID 换成模块可见形式

    只做浅拷贝，不修改框架自己的数据（框架内继续用官方原始 ID 与配置比对）。
    """
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for key in keys:
        if key in out and out[key] is not None:
            out[key] = module_id(out[key])
    return out
