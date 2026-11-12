# -*- coding: utf-8 -*-
"""crashhandler.py —— 主程序崩溃守护

工作方式（守护逻辑全部运行在主程序自身进程内，不额外拉起 crashhandler 进程）：

1. 主程序 `bot.py` 启动时导入本模块并调用 `bootstrap()`。
2. `bootstrap()` 读取 `coreset.use_crashhandler`：
   - 关闭：什么都不做，主程序照常运行。
   - 开启且当前进程已被守护（环境变量 `MJB_CRASHHANDLER_SUPERVISED=1`）：
     当前进程就是「被守护的工作进程」，只做 faulthandler 落盘准备。
   - 开启且当前进程未被守护：当前进程转为「守护父进程」，
     用 subprocess 拉起带守护标记的 `bot.py` 工作子进程。
3. 工作进程崩溃（退出码非 0）时，父进程把 try 块异常栈、faulthandler 崩溃栈、
   进程退出码写入 `log/crash_<时间>_<毫秒时间戳>.txt`，等待后重新拉起工作进程，
   继续捕获。无论 `coreset.log_output` 是否开启，报告都写入 `log/`。
4. 连续崩溃达到 `MAX_CONSECUTIVE_CRASH` 次后停止守护并退出，避免无限重启。
5. 本文件被单独执行（`python crashhandler.py`）时立即退出：只允许由主程序启动。

父子进程间的数据交接用「每次运行一个 run_id」的文件（log/.crash_*_<run_id>.txt），
不使用 pid：Windows 下 venv 的 python.exe 是启动器进程，Popen.pid 与解释器实际
pid 并不一致。
"""

import os
import sys
import time
import datetime
import subprocess
import traceback
import faulthandler

# ---- 环境变量标记 ----
ENV_SUPERVISED = "MJB_CRASHHANDLER_SUPERVISED"            # =1：本进程是被守护的工作进程
ENV_LAUNCHED_BY_BOT = "MJB_CRASHHANDLER_LAUNCHED_BY_BOT"  # =1：crashhandler.py 由主程序拉起
ENV_RUN_ID = "MJB_CRASHHANDLER_RUN_ID"                   # 本次运行的交接文件标识

# ---- 重启策略 ----
RESTART_DELAY = 3.0          # 崩溃后等待秒数再重启
MAX_CONSECUTIVE_CRASH = 5    # 连续崩溃多少次后停止守护
STABLE_SECONDS = 60.0        # 工作进程存活超过该秒数视为已稳定，连续崩溃计数清零

# 视为「正常退出」的退出码：0 / Ctrl+C 中断（130、Windows STATUS_CONTROL_C_EXIT）
NORMAL_EXIT_CODES = frozenset({0, 130, -1073741510, 3221225786})

_ROOT = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.path.join(_ROOT, "log")

# faulthandler 输出句柄：工作进程内保持引用，避免被 GC 关闭导致崩溃时写不出栈
_fh_handle = None


# ===================== 基础工具 =====================
def log_dir():
    """返回 log 目录并确保存在（crash 报告不受 log_output 开关影响，始终写入）"""
    os.makedirs(_LOG_DIR, exist_ok=True)
    return _LOG_DIR


def is_supervised():
    """当前进程是否处于 crashhandler 的守护之下"""
    return os.environ.get(ENV_SUPERVISED) == "1"


def _run_id():
    """本次运行的交接标识：优先用父进程下发的，单独运行时自行生成"""
    rid = os.environ.get(ENV_RUN_ID)
    if rid:
        return rid
    return f"{int(time.time() * 1000)}_{os.getpid()}"


def _try_file(run_id):
    return os.path.join(log_dir(), f".crash_try_{run_id}.txt")


def _fh_file(run_id):
    return os.path.join(log_dir(), f".crash_fh_{run_id}.txt")


def _meta_file(run_id):
    return os.path.join(log_dir(), f".crash_meta_{run_id}.txt")


def _crash_enabled():
    """读取 coreset.use_crashhandler；读取失败按关闭处理，避免影响主程序启动"""
    try:
        from pymjb import mjbconfig
        return bool(mjbconfig.get_coreset("use_crashhandler", False))
    except Exception as e:
        print(f"[crashhandler] 读取 coreset.use_crashhandler 失败，按关闭处理: {e}",
              file=sys.stderr, flush=True)
        return False


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def _remove_quiet(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _format_exit_code(code):
    """退出码展示：负数（被信号/异常终止）同时给出 32 位无符号十六进制"""
    if code is None:
        return "未知"
    if code < 0:
        return f"{code} (0x{code & 0xFFFFFFFF:08X})"
    return str(code)


def _version():
    try:
        from pymjb import mjbconfig
        return mjbconfig.get_mjbcver_raw()
    except Exception:
        return "未知"


# ===================== 工作进程侧 =====================
def _prepare_child():
    """被守护的工作进程：把 faulthandler 输出定向到本次运行的交接文件，并记录真实 pid"""
    global _fh_handle
    rid = _run_id()
    try:
        _fh_handle = open(_fh_file(rid), "w", encoding="utf-8", errors="replace")
        faulthandler.enable(file=_fh_handle, all_threads=True)
    except Exception as e:
        print(f"[crashhandler] faulthandler 初始化失败: {e}", file=sys.stderr, flush=True)
    try:
        with open(_meta_file(rid), "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()}\n")
            f.write(f"python={sys.executable}\n")
    except Exception:
        pass


def run_guarded(fn):
    """在受守护的工作进程中执行主流程

    - 未处于守护状态：直接执行，行为与未启用崩溃守护时完全一致。
    - 处于守护状态：try 包住主流程，未捕获异常写入交接文件后以退出码 1 退出，
      由父进程统一汇总成 crash 报告。
    """
    if not is_supervised():
        return fn()
    try:
        return fn()
    except KeyboardInterrupt:
        print("[crashhandler] 收到中断信号，主程序退出", file=sys.stderr, flush=True)
        sys.exit(0)
    except SystemExit:
        raise
    except BaseException:
        detail = traceback.format_exc()
        try:
            with open(_try_file(_run_id()), "w", encoding="utf-8") as f:
                f.write(detail)
        except Exception:
            pass
        print(detail, file=sys.stderr, flush=True)
        sys.exit(1)


# ===================== 守护父进程侧 =====================
def _shutdown_child(proc, timeout=10.0):
    """父进程收到中断时，先等工作进程自行收尾，超时再强制结束"""
    try:
        proc.wait(timeout=timeout)
        return
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _child_pid(run_id, fallback=None):
    """从交接元数据中取工作进程真实 pid（取不到时回退父进程侧 pid）"""
    for line in _read_text(_meta_file(run_id)).splitlines():
        if line.startswith("pid="):
            pid = line.split("=", 1)[1].strip()
            if pid.isdigit():
                return int(pid)
    return fallback


def _write_crash_report(script, pid, exit_code, duration, consecutive, run_id):
    """写入单次崩溃报告：log/crash_<时间>_<毫秒时间戳>.txt"""
    now = datetime.datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    ms_stamp = int(time.time() * 1000)
    path = os.path.join(log_dir(), f"crash_{stamp}_{ms_stamp}.txt")

    try_detail = _read_text(_try_file(run_id))
    fh_detail = _read_text(_fh_file(run_id))

    lines = [
        "=" * 60,
        "主程序崩溃报告",
        "=" * 60,
        f"崩溃时间      : {now.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}",
        f"毫秒时间戳    : {ms_stamp}",
        f"主程序        : {script}",
        f"工作进程 PID  : {pid}",
        f"退出码        : {_format_exit_code(exit_code)}",
        f"运行时长      : {duration:.2f} 秒",
        f"连续崩溃次数  : {consecutive} / {MAX_CONSECUTIVE_CRASH}",
        f"内核版本      : {_version()}",
        "",
        "-" * 60,
        "[try 块捕获的异常]",
        "-" * 60,
        try_detail if try_detail else "<无：进程未经过异常路径（多为段错误/被强制终止）>",
        "",
        "-" * 60,
        "[faulthandler 捕获的崩溃栈]",
        "-" * 60,
        fh_detail if fh_detail else "<无：本次退出不是 faulthandler 可捕获的致命信号>",
        "",
        "=" * 60,
        "",
    ]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[crashhandler] 已记录崩溃报告: {path}", file=sys.stderr, flush=True)
        return path
    except Exception as e:
        print(f"[crashhandler] 写入崩溃报告失败: {e}", file=sys.stderr, flush=True)
        return None


def _cleanup_run_files(run_id):
    for path in (_try_file(run_id), _fh_file(run_id), _meta_file(run_id)):
        _remove_quiet(path)


def supervise(script, args=None):
    """守护循环：拉起工作进程 → 等待退出 → 崩溃则记录并重启

    返回主程序最终的退出码（正常退出为 0）。
    """
    script = os.path.abspath(script)
    root = os.path.dirname(script)
    args = list(args or [])
    consecutive = 0
    attempt = 0

    print(f"[crashhandler] 崩溃守护已启用，工作进程: {script}", file=sys.stderr, flush=True)

    while True:
        attempt += 1
        started = time.time()
        run_id = f"{int(started * 1000)}_{attempt}"
        env = dict(os.environ)
        env[ENV_SUPERVISED] = "1"
        env[ENV_RUN_ID] = run_id
        env.pop(ENV_LAUNCHED_BY_BOT, None)

        try:
            proc = subprocess.Popen([sys.executable, script, *args], cwd=root, env=env)
        except Exception as e:
            print(f"[crashhandler] 无法启动主程序: {e}", file=sys.stderr, flush=True)
            return 1

        try:
            exit_code = proc.wait()
        except KeyboardInterrupt:
            print("[crashhandler] 收到中断信号，停止守护", file=sys.stderr, flush=True)
            _shutdown_child(proc)
            _cleanup_run_files(run_id)
            return 0

        duration = time.time() - started
        pid = _child_pid(run_id, proc.pid)

        # 正常退出（sys.exit(0) / 优雅关闭 / 手动 Ctrl+C）：结束守护，不再重启
        if exit_code in NORMAL_EXIT_CODES:
            _cleanup_run_files(run_id)
            return 0

        # 连续崩溃计数：上次运行足够久则视为已恢复
        if duration >= STABLE_SECONDS:
            consecutive = 0
        consecutive += 1

        _write_crash_report(script, pid, exit_code, duration, consecutive, run_id)
        _cleanup_run_files(run_id)

        if consecutive >= MAX_CONSECUTIVE_CRASH:
            print(
                f"[crashhandler] 连续崩溃 {consecutive} 次，停止守护，请检查 log/ 下的崩溃报告",
                file=sys.stderr, flush=True,
            )
            return exit_code

        print(
            f"[crashhandler] {RESTART_DELAY:.0f} 秒后重启主程序（连续第 {consecutive} 次崩溃）",
            file=sys.stderr, flush=True,
        )
        try:
            time.sleep(RESTART_DELAY)
        except KeyboardInterrupt:
            print("[crashhandler] 收到中断信号，停止守护", file=sys.stderr, flush=True)
            return 0


# ===================== 对外入口 =====================
def bootstrap(script=None):
    """主程序启动时调用：按 coreset 配置决定是否进入崩溃守护

    - 返回 False：当前进程继续按主程序正常运行（守护关闭，或本进程就是工作进程）
    - 不返回：当前进程已转为守护父进程，主程序不应继续执行（内部会 sys.exit）
    """
    if is_supervised():
        # 本进程是被 crashhandler 拉起的工作进程
        _prepare_child()
        return False

    if not _crash_enabled():
        return False

    target = os.path.abspath(script or sys.argv[0])
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    args = sys.argv[1:] if argv0 == target else []

    sys.exit(supervise(target, args))


def main():
    """独立执行入口：只允许由主程序启动，单独运行立即退出"""
    if os.environ.get(ENV_LAUNCHED_BY_BOT) != "1":
        print("[crashhandler] 本程序由主程序启动，单独运行将直接退出。",
              file=sys.stderr, flush=True)
        sys.exit(0)

    script = os.path.join(_ROOT, "bot.py")
    sys.exit(supervise(script, sys.argv[1:]))


if __name__ == "__main__":
    main()
