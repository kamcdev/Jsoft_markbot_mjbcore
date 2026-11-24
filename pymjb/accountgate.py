# -*- coding: utf-8 -*-
"""账号门闸：按 bind_official 绑定关系做「官机 ⇄ 普通账号」的离线替补判定

coreset.account.<QQ>.bind_official 指定该账号绑定的官机账号（写官机的账号键，数字即可）：
- 多个普通账号可以绑定同一个官机，也可以各自绑定不同官机；
- 每组独立判定：组内有任一普通账号在线 → 该官机判定为不存在；组内普通账号全部
  离线 → 官机接管，这组普通账号判定为不存在（双方互斥，避免两个 Bot 同时在线刷屏）；
- **没有绑定关系的官机与普通账号互不影响**，可以同时在线，不再做全量互斥；
- 只有「官机 + 绑定它的普通账号」这种组才做离线替补检测。

判定只改运行期状态（mjbconfig.set_inactive_accounts），绝不改写配置文件。
"""
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
    "signature": None,   # (账号列表, 绑定关系) 快照，reload 后变化则重置判定
}

# 绑定配置有误时的告警去重（否则每 2 秒刷一次）
_WARNED_BINDS = set()


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
    普通账号恢复后也无法切回（官机接管后再也回不来）。
    """
    try:
        return bool(message.get_heartbeat_info(bot_id, ignore_gate=True).get("online", False))
    except Exception:
        return False


def _classify():
    """账号分类 + 绑定分组

    Returns:
        accounts: 全部账号键（有序）
        officials: 开启 official_mode 的账号
        normals: 未开启 official_mode 的账号
        binds: {普通账号: 绑定的官机账号}（只保留官机确实存在的绑定）
        groups: {官机账号: [绑定的普通账号...]}（只含非空组）
    """
    accounts = [str(b) for b in mjbconfig.get_account_list()]
    officials, normals = [], []
    for bid in accounts:
        if mjbconfig.get_official_mode(bid):
            officials.append(bid)
        else:
            normals.append(bid)

    official_set = set(officials)
    binds, groups = {}, {}
    for normal in normals:
        target = mjbconfig.get_bind_official(normal)
        if not target or target == normal:
            continue
        if target not in official_set:
            key = (normal, target)
            if target in accounts:
                reason = f"账号{target} 未开启 official_mode"
            else:
                reason = f"账号{target} 不在 coreset.account 中"
            if key not in _WARNED_BINDS:
                _WARNED_BINDS.add(key)
                logger.warning(f"账号{normal} 的 bind_official={target} 无效（{reason}），该绑定被忽略")
            continue
        binds[normal] = target
        groups.setdefault(target, []).append(normal)
    return accounts, officials, normals, binds, groups


def _decisions():
    """按绑定组计算判定结果

    Returns:
        inactive: 目标"不存在"集合
        takeover: 本组普通账号全部离线、应当接管的官机集合
    """
    _accounts, _officials, _normals, _binds, groups = _classify()
    inactive, takeover = set(), set()
    for official, members in groups.items():
        if any(_is_online(m) for m in members):
            inactive.add(official)          # 组内有普通账号在线 → 官机不存在
        else:
            takeover.add(official)          # 全部离线 → 官机接管
            inactive.update(members)
    return inactive, takeover


def _desired_inactive():
    """目标"不存在"集合（不含接管信息，供调试/外部查询）"""
    return _decisions()[0]


def _apply(target):
    """应用新的判定集合（只改运行期状态，不动任何配置文件）"""
    current = set(_STATE["inactive"])
    if target == current:
        return False

    _accounts, _officials, _normals, _binds, groups = _classify()
    newly_inactive = target - current
    newly_active = current - target

    mjbconfig.set_inactive_accounts(target)
    _STATE["inactive"] = set(target)

    if newly_inactive:
        logger.info("账号门闸：%s 判定为不存在，其上报将被忽略" % ", ".join(sorted(newly_inactive)))
    if newly_active:
        logger.info("账号门闸：%s 判定为存在，恢复接入" % ", ".join(sorted(newly_active)))

    # 按绑定组补一条可读结论
    for official, members in groups.items():
        if official in newly_active:
            logger.info("账号门闸：绑定的普通账号 %s 均已离线，改由官机 %s 接管"
                        % ("/".join(members), official))
        elif official in newly_inactive:
            back = [m for m in members if m in newly_active] or members
            logger.info("账号门闸：绑定的普通账号 %s 已恢复在线，官机 %s 退回不存在状态"
                        % ("/".join(back), official))
    return True


def evaluate(force=False):
    """执行一次判定；返回是否发生了切换

    force=True 时跳过宽限期与去抖，立即按当前状态切换（供启动/重载内部使用）。
    """
    accounts, _officials, _normals, binds, _groups = _classify()
    signature = (tuple(accounts), tuple(sorted(binds.items())))

    with _LOCK:
        # 账号列表或绑定关系变化（配置重载）→ 重置去抖状态，避免用旧集合做判断
        if signature != _STATE["signature"]:
            _STATE["signature"] = signature
            _STATE["pending"] = None
            _STATE["pending_count"] = 0

        if not force and time.time() < _STATE["ready_at"]:
            return False

        desired, takeover = _decisions()
        if desired == _STATE["inactive"]:
            _STATE["pending"] = None
            _STATE["pending_count"] = 0
            return False

        if desired == _STATE["pending"]:
            _STATE["pending_count"] += 1
        else:
            _STATE["pending"] = set(desired)
            _STATE["pending_count"] = 1

        # 恢复方向（绑定的普通账号重新上线 → 官机退回不存在）是安全切换，立即生效；
        # 只有「官机要上线接管」这个方向才去抖，避免心跳抖动导致来回抢线。
        activating = {off for off in takeover if off in _STATE["inactive"]}
        if not force and activating and _STATE["pending_count"] < CONFIRM_TIMES:
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

    首次启动：有绑定组的官机立刻判定为不存在，随后进入宽限期（等绑定的普通账号
    上报首个心跳）；热重载后重启：保留现有判定集合与心跳历史，无需宽限，立即按现状判定。
    """
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False

        accounts, _officials, _normals, binds, groups = _classify()
        if _STATE["signature"] is None:
            # 首次启动：有绑定组的官机先不接入，优先走其绑定的普通账号
            target = set(groups.keys())
            mjbconfig.set_inactive_accounts(target)
            _STATE["inactive"] = set(target)
            _STATE["signature"] = (tuple(accounts), tuple(sorted(binds.items())))
            _STATE["ready_at"] = time.time() + _grace_seconds()
            if target:
                detail = ", ".join(f"{o}(绑定 {'/'.join(groups[o])})" for o in sorted(target))
                logger.info(f"账号门闸：官机 {detail} 暂不接入，优先使用其绑定的普通账号")
            else:
                logger.info("账号门闸：未配置 bind_official 绑定关系，全部账号正常接入")
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
        _accounts, _officials, _normals, binds, groups = _classify()
        return {
            "inactive": sorted(_STATE["inactive"]),
            "active": sorted(mjbconfig.get_active_account_list()),
            "officials": sorted(mjbconfig.get_official_accounts()),
            "non_officials": sorted(mjbconfig.get_non_official_accounts()),
            "binds": dict(sorted(binds.items())),
            "gate_groups": {k: sorted(v) for k, v in sorted(groups.items())},
        }
