# -*- coding: utf-8 -*-
import threading
import time

from pymjb import logger, mjbconfig, message, worker

GATE_NAME = "account-gate"
CHECK_INTERVAL = 2.0   # 判定周期（秒）
CONFIRM_TIMES = 2      # 连续多少次判定一致才执行切换（去抖）

_LOCK = threading.RLock()
_STOP = threading.Event()
_THREAD = None
_STATE = {
    "inactive": set(),   # 当前判定为不存在的账号
    "pending": None,     # 待确认的目标集合
    "pending_count": 0,
    "ready_at": 0.0,     # 启动宽限期截止时间
    "signature": None,   # 账号列表快照（reload 后变化则重置判定）
}


def _grace_seconds():
    """启动宽限期：留足时间让普通账号上报首个心跳，避免启动瞬间误判离线"""
    try:
        patience = float(mjbconfig.get_heart_patience())
    except Exception:
        patience = 5.0
    return max(8.0, patience * 2)


def _is_online(bot_id):
    """读取账号真实在线状态（heartbeat/api 两种方案统一走 message 的副本）

    必须 ignore_gate=True：本函数是门闸的判定输入，若走带门闸过滤的查询，
    一旦账号被判"不存在"就读到恒为离线的假状态，判定结果会锁死自己，
    普通账号恢复后也无法切回（官方接管后再也回不来）。
    """
    try:
        return bool(message.get_heartbeat_info(bot_id, ignore_gate=True).get("online", False))
    except Exception:
        return False


def _classify():
    """按当前配置把账号分成（官方列表, 非官方列表）"""
    accounts = [str(b) for b in mjbconfig.get_account_list()]
    officials, normals = [], []
    for bid in accounts:
        if mjbconfig.get_official_mode(bid):
            officials.append(bid)
        else:
            normals.append(bid)
    return accounts, officials, normals


def _desired_inactive():
    """计算目标"不存在"集合

    - 没有官方账号、或没有普通账号：互斥无意义，全部存在；
    - 普通账号全部离线：普通账号不存在，官方账号接管；
    - 否则：官方账号不存在，普通账号正常。
    """
    accounts, officials, normals = _classify()
    if not officials or not normals:
        return set()
    if all(not _is_online(b) for b in normals):
        return set(normals)
    return set(officials)


def _apply(target):
    """应用新的判定集合（只改运行期状态，不动任何配置文件）"""
    current = set(_STATE["inactive"])
    if target == current:
        return False

    _accounts, officials, _normals = _classify()
    official_set = set(officials)
    newly_inactive = target - current
    newly_active = current - target

    mjbconfig.set_inactive_accounts(target)
    _STATE["inactive"] = set(target)

    if newly_inactive:
        logger.info("账号门闸：%s 判定为不存在，其上报将被忽略" % ", ".join(sorted(newly_inactive)))
    if newly_active:
        logger.info("账号门闸：%s 判定为存在，恢复接入" % ", ".join(sorted(newly_active)))

    if newly_active & official_set:
        logger.info("账号门闸：未启用官方模式的账号均已离线，改由官方账号 %s 接管"
                    % ", ".join(sorted(newly_active & official_set)))
    if newly_inactive & official_set:
        logger.info("账号门闸：未启用官方模式的账号已恢复在线，官方账号 %s 退回不存在状态"
                    % ", ".join(sorted(newly_inactive & official_set)))
    return True


def evaluate(force=False):
    """执行一次判定；返回是否发生了切换

    force=True 时跳过宽限期与去抖，立即按当前状态切换（供启动/重载内部使用）。
    """
    accounts, officials, normals = _classify()
    signature = tuple(accounts)

    with _LOCK:
        # 账号列表变化（配置重载）→ 重置去抖状态，避免用旧集合做判断
        if signature != _STATE["signature"]:
            _STATE["signature"] = signature
            _STATE["pending"] = None
            _STATE["pending_count"] = 0

        if not force and time.time() < _STATE["ready_at"]:
            return False

        desired = _desired_inactive()
        if desired == _STATE["inactive"]:
            _STATE["pending"] = None
            _STATE["pending_count"] = 0
            return False

        if desired == _STATE["pending"]:
            _STATE["pending_count"] += 1
        else:
            _STATE["pending"] = set(desired)
            _STATE["pending_count"] = 1

        # 恢复方向（普通账号重新在线、官方退回不存在）是安全切换，立即生效；
        # 只有切向官方（普通账号全部离线）才去抖，避免心跳抖动导致来回抢线。
        safe_switch = bool(officials) and desired == set(officials)
        if not force and not safe_switch and _STATE["pending_count"] < CONFIRM_TIMES:
            return False

        return _apply(desired)


def _loop(stop_event):
    """后台判定线程：周期性检查在线状态并切换"""
    while not stop_event.is_set():
        try:
            evaluate()
        except Exception as e:
            logger.error(f"账号门闸判定失败: {e}")
        stop_event.wait(CHECK_INTERVAL)


def start():
    """启动账号门闸（幂等）

    首次启动：官方账号立刻判定为不存在，随后进入宽限期（等普通账号上报首个心跳）；
    热重载后重启：保留现有判定集合与心跳历史，无需宽限，立即按现状判定。
    """
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False

        accounts, officials, normals = _classify()
        if _STATE["signature"] is None:
            # 首次启动：有普通账号时官方账号先不接入
            target = set(officials) if (officials and normals) else set()
            mjbconfig.set_inactive_accounts(target)
            _STATE["inactive"] = set(target)
            _STATE["signature"] = tuple(accounts)
            _STATE["ready_at"] = time.time() + _grace_seconds()
            if target:
                logger.info("账号门闸：官方账号 %s 暂不接入，优先使用未启用官方模式的账号"
                            % ", ".join(sorted(target)))
            else:
                logger.info("账号门闸：未启用官方模式的账号不存在或未配置官方账号，全部账号正常接入")
        else:
            # 热重载：心跳数据仍在，立即允许判定，恢复即切
            _STATE["ready_at"] = 0.0

    _STOP.clear()
    _THREAD = worker.start_background(GATE_NAME, _loop, stop_event=_STOP)
    return True


def stop():
    """停止判定线程（不清空判定集合，重载后由 start 继续沿用）"""
    _STOP.set()
    worker.stop_background(GATE_NAME)


def get_state():
    """返回当前判定快照（供 WebUI / 调试查看）"""
    with _LOCK:
        return {
            "inactive": sorted(_STATE["inactive"]),
            "active": sorted(mjbconfig.get_active_account_list()),
            "officials": sorted(mjbconfig.get_official_accounts()),
            "non_officials": sorted(mjbconfig.get_non_official_accounts()),
        }
