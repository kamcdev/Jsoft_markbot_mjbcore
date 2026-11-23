# -*- coding: utf-8 -*-
import os
import re
import time
import string
import shutil
import secrets
import threading

from pymjb import logger, mjbconfig, worker

#: WebUI 目录下的存放子目录（同时也是路由前缀）
SUBDIR = "official_mode_file"

#: 文件有效期（秒）：12 小时，到期后由清理线程 / 取件路由删除
EXPIRE_SECONDS = 12 * 3600

#: 清理线程扫描间隔（秒）
CLEAN_INTERVAL = 10 * 60

#: 文件 id 长度与字符集（小写字母 + 数字，保证至少各一位）
ID_LENGTH = 10
_ID_ALPHABET = string.ascii_lowercase + string.digits
_ID_RE = re.compile(r"^[0-9a-z]{%d}$" % ID_LENGTH)

#: 原始文件名记录（与文件同目录的小文本，不是文件列表配置）
NAME_FILE = "name.txt"

_CLEAN_THREAD_NAME = "official-file-cleanup"


def base_dir():
    """外链文件根目录：<webui_dir>/official_mode_file（webui_path 未配置时返回 None）"""
    webui_dir = mjbconfig.get_webui_dir()
    if not webui_dir:
        return None
    return os.path.join(webui_dir, SUBDIR)


def public_base_url():
    """对外访问根地址：coreset 的 webui_url + webui_https 决定协议

    ``webui_url`` 只填主机部分（可带端口），不要带协议和末尾斜杠；
    用户误带协议时这里会自动去掉，避免拼出 ``http://https://...``。
    未配置返回空串，调用方据此回错误提示。
    """
    raw = str(mjbconfig.get_webui_url() or "").strip().rstrip("/")
    if not raw:
        return ""
    low = raw.lower()
    if low.startswith("https://"):
        raw = raw[8:]
    elif low.startswith("http://"):
        raw = raw[7:]
    raw = raw.strip().rstrip("/")
    if not raw:
        return ""
    scheme = "https" if mjbconfig.get_webui_https() else "http"
    return "%s://%s" % (scheme, raw)


def new_file_id(base=None):
    """生成 10 位字母数字混合 id（至少一位字母、一位数字），并避开已存在的目录"""
    base = base if base is not None else base_dir()
    for _ in range(50):
        fid = "".join(secrets.choice(_ID_ALPHABET) for _ in range(ID_LENGTH))
        if not any(c.isalpha() for c in fid) or not any(c.isdigit() for c in fid):
            continue
        if base is None or not os.path.exists(os.path.join(base, fid)):
            return fid
    # 极端情况下退化成纯随机（10 位碰撞概率可忽略）
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(ID_LENGTH))


def file_url(file_id):
    """拼出取件直链（webui_url 未配置时返回空串）"""
    base = public_base_url()
    if not base:
        return ""
    return "%s/%s/%s" % (base, SUBDIR, file_id)


def export_file(file_path, file_name=None):
    """把文件复制到外链目录，返回 ``(file_id, 错误信息)``

    复制是同步的：模块拿到返回值后通常会立刻删除自己的临时文件，
    若改成异步复制会读到已被删除的源文件。耗时与一次本地拷贝相同。
    """
    if not os.path.exists(file_path):
        return None, "文件不存在"
    base = base_dir()
    if not base:
        return None, "WebUI 目录未配置（coreset.webui_path）"

    name = file_name or os.path.basename(file_path)
    ext = os.path.splitext(name)[1] or os.path.splitext(file_path)[1]

    try:
        os.makedirs(base, exist_ok=True)
    except OSError as e:
        return None, "创建外链目录失败: %s" % e

    fid = new_file_id(base)
    folder = os.path.join(base, fid)
    target = os.path.join(folder, "official_%s%s" % (fid, ext))
    try:
        os.makedirs(folder, exist_ok=True)
        shutil.copyfile(file_path, target)
    except OSError as e:
        _remove_tree(folder)
        return None, "复制文件失败: %s" % e

    try:
        with open(os.path.join(folder, NAME_FILE), "w", encoding="utf-8") as fp:
            fp.write(name)
    except OSError as e:
        logger.debug(f"写入原始文件名失败（不影响下载）: {e}")

    logger.info(f"官机模式文件已导出: {fid} <- {name}"
                f"（{_human_size(os.path.getsize(target))}）")
    return fid, ""


def find_export(file_id):
    """按 id 找文件，返回 ``(路径, 下载文件名)``；不存在或已过期返回 ``(None, None)``

    有效期按文件 mtime 算，过期就顺手删掉整个目录（取件与清理共用同一套逻辑）。
    """
    fid = str(file_id or "")
    if not _ID_RE.match(fid):
        return None, None
    base = base_dir()
    if not base:
        return None, None
    folder = os.path.join(base, fid)
    if not os.path.isdir(folder):
        return None, None

    prefix = "official_%s" % fid
    try:
        names = [n for n in os.listdir(folder) if n.startswith(prefix)]
    except OSError:
        return None, None
    if not names:
        return None, None

    path = os.path.join(folder, names[0])
    if _expired(path):
        logger.info(f"官机模式文件已过期，删除: {fid}")
        _remove_tree(folder)
        return None, None

    name = names[0]
    try:
        with open(os.path.join(folder, NAME_FILE), "r", encoding="utf-8") as fp:
            recorded = fp.read().strip()
        if recorded:
            name = recorded
    except OSError:
        pass
    return path, name


def cleanup_once():
    """扫描并删除过期目录，返回删除数量"""
    base = base_dir()
    if not base or not os.path.isdir(base):
        return 0
    removed = 0
    try:
        entries = os.listdir(base)
    except OSError as e:
        logger.error(f"扫描官机模式文件目录失败: {e}")
        return 0
    for entry in entries:
        folder = os.path.join(base, entry)
        if not os.path.isdir(folder):
            continue
        if _folder_expired(folder):
            _remove_tree(folder)
            removed += 1
    if removed:
        logger.info(f"官机模式文件清理：删除 {removed} 个过期目录")
    return removed


def deliver(group_id, file_path, file_name=None, bot_id=None):
    """官机模式发送文件：导出并异步发出取件文案

    返回 ``(是否成功, 链接或错误原因)``。文案发送放后台线程，避免模块线程等网络；
    文件复制仍同步完成（原因见 :func:`export_file`）。
    """
    url_base = public_base_url()
    if not url_base:
        return False, "官机模式需在 coreset 配置 webui_url"

    fid, err = export_file(file_path, file_name)
    if not fid:
        return False, err or "导出文件失败"

    url = file_url(fid)
    worker.start_background(
        "official-file-%s" % fid, _send_link_notice,
        args=(group_id, url, fid), daemon=True,
    )
    return True, url


def _send_link_notice(group_id, url, fid):
    """后台线程：发取件文案（发送完成后自行注销线程）"""
    from pymjb import send  # 延迟导入，避免与 send -> filexfer 形成循环

    try:
        send.group(group_id,
                   "当前为官bot模式，请访问此链接提取文件\n"
                   "地址：%s\n"
                   "注：此链接无限次数访问，请在12小时内提取" % url)
        logger.info(f"官机模式取件链接已发送到群{group_id}: {url}")
    except Exception as e:
        logger.error(f"发送官机模式取件链接失败: {e}")
    finally:
        worker.unregister_thread("official-file-%s" % fid)


def _clean_loop(stop_event):
    """清理线程主体：启动先扫一次，之后每 CLEAN_INTERVAL 秒扫一次"""
    try:
        cleanup_once()
    except Exception as e:
        logger.error(f"官机模式文件清理失败: {e}")
    while not stop_event.wait(CLEAN_INTERVAL):
        try:
            cleanup_once()
        except Exception as e:
            logger.error(f"官机模式文件清理失败: {e}")


def ensure_cleanup_thread():
    """确保清理线程在跑（幂等），供启动与热重载后调用"""
    status = worker.get_status().get(_CLEAN_THREAD_NAME)
    if status and status.get("alive"):
        return False
    worker.start_background(_CLEAN_THREAD_NAME, _clean_loop,
                            stop_event=threading.Event(), daemon=True)
    return True


def _expired(path):
    """文件是否超过有效期（读不到 mtime 视为未过期，避免误删）"""
    try:
        return time.time() - os.path.getmtime(path) > EXPIRE_SECONDS
    except OSError:
        return False


def _folder_expired(folder):
    """目录内的正式文件是否已过期

    只看 ``official_*`` 本体：同一目录下的 ``name.txt`` 是导出时写的，
    按它算会把刚过期的文件又“续命”一次；目录里没有本体时退回目录 mtime。
    """
    newest = 0.0
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    for name in names:
        if not name.startswith("official_"):
            continue
        try:
            newest = max(newest, os.path.getmtime(os.path.join(folder, name)))
        except OSError:
            continue
    if newest <= 0:
        try:
            newest = os.path.getmtime(folder)
        except OSError:
            return False
    return time.time() - newest > EXPIRE_SECONDS


def _remove_tree(path):
    """删除目录树，失败只记日志（不抛给调用方）"""
    try:
        shutil.rmtree(path, ignore_errors=False)
    except OSError as e:
        logger.warning(f"删除官机模式文件目录失败: {path} -> {e}")


def _human_size(size):
    """人类可读体积"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "%.1f%s" % (value, unit)
        value /= 1024
    return "%.1fGB" % value
