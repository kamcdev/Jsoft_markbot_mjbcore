# -*- coding: utf-8 -*-
import html as _html
import os
import time

from pymjb import logger, mjbtmpfile


def html_to_image(html_content, width=760, wait=1.0):
    """渲染 HTML 并全页截图

    Args:
        html_content: 完整 HTML 文档字符串
        width: 视口宽度（像素），高度按内容实际高度延伸
        wait: 页面加载后额外等待秒数（等字体与布局就绪）

    Returns:
        str | None: PNG 临时文件路径（调用方负责清理），失败返回 None
    """
    html_path = None
    png_path = None
    driver = None
    success = False
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options

        html_path = mjbtmpfile.create(suffix=".html", prefix="render_")
        png_path = mjbtmpfile.create(suffix=".png", prefix="render_")
        if not html_path or not png_path:
            logger.error("渲染图片失败：无法创建临时文件")
            return None

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        # file:// URI（Windows 路径反斜杠需转为正斜杠）
        file_uri = "file:///" + os.path.abspath(html_path).replace(os.sep, "/")

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--window-size={width},1080")

        driver = webdriver.Edge(options=options)
        driver.set_page_load_timeout(15)
        driver.get(file_uri)

        time.sleep(wait)

        # 无头 Edge 的视口比窗口小（浏览器边框/工具栏占位），先算出偏移量，
        # 否则按内容高度设窗口会把页面底部截掉
        try:
            vp_w, vp_h = driver.execute_script(
                "return [window.innerWidth, window.innerHeight]")
            win = driver.get_window_size()
            offset_w = win["width"] - vp_w
            offset_h = win["height"] - vp_h
        except Exception as e:
            logger.warning(f"获取视口尺寸失败: {e}")
            offset_w, offset_h = 0, 0

        # 全页截图：让视口高度等于内容实际高度（图片加载可能引起高度变化，测两次收敛）
        for _ in range(2):
            try:
                total_height = driver.execute_script(
                    "return Math.max(document.body.scrollHeight, "
                    "document.documentElement.scrollHeight);")
                driver.set_window_size(width + offset_w, int(total_height) + offset_h)
                time.sleep(0.4)
            except Exception as e:
                logger.warning(f"获取页面高度失败: {e}")
                break

        driver.save_screenshot(png_path)
        success = True
        return png_path
    except Exception as e:
        logger.error(f"渲染 HTML 截图失败: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        if html_path and os.path.exists(html_path):
            mjbtmpfile.cleanup_now(html_path)
        # 渲染失败时不留空/残图给调用方
        if not success and png_path and os.path.exists(png_path):
            mjbtmpfile.cleanup_now(png_path)


def _image_src(file):
    """把图片节点的 file 值转换成可用于 <img src> 的地址，无法嵌入时返回 None"""
    if not isinstance(file, str) or not file:
        return None
    value = file.strip()
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("base64://") or value.startswith("data:"):
        return None
    if os.path.exists(value):
        return "file:///" + os.path.abspath(value).replace(os.sep, "/")
    return None


def _content_to_html(content):
    """把节点 content 列表转换成气泡内部的 HTML"""
    parts = []
    for item in content or []:
        if not isinstance(item, dict):
            parts.append(f'<div class="text">{_html.escape(str(item))}</div>')
            continue
        itype = item.get("type")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if itype == "text":
            text = str(data.get("text", ""))
            parts.append('<div class="text">' +
                         _html.escape(text).replace("\n", "<br>") + "</div>")
        elif itype == "image":
            src = _image_src(data.get("file"))
            if src:
                parts.append(
                    f'<div class="pic"><img src="{_html.escape(src, quote=True)}"></div>')
            else:
                parts.append('<div class="text muted">[图片]</div>')
        else:
            label = itype or "未知"
            parts.append(f'<div class="text muted">[{_html.escape(str(label))}]</div>')
    if not parts:
        parts.append('<div class="text muted">（空消息）</div>')
    return "".join(parts)


_CHAT_STYLE = """
* { margin: 0; padding: 0; box-sizing: border-box; }
::-webkit-scrollbar { width: 0; height: 0; display: none; }
body {
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
    background: #f0f2f5;
    padding: 20px;
    width: 100%;
}
.wrap {
    background: #ededed;
    border-radius: 14px;
    overflow: hidden;
}
.head {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: #fff;
    padding: 18px 24px;
}
.head .title { font-size: 18px; font-weight: 700; }
.head .sub { font-size: 12px; opacity: 0.85; margin-top: 4px; }
.chat { padding: 18px 18px 6px; }
.msg { display: flex; margin-bottom: 14px; }
.avatar {
    width: 36px; height: 36px; border-radius: 8px;
    background: linear-gradient(135deg, #a8b8ff 0%, #8f7bd8 100%);
    color: #fff; font-size: 15px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; margin-right: 10px;
}
.body { max-width: 560px; }
.meta { font-size: 11px; color: #999; margin-bottom: 4px; }
.meta .uin { margin-left: 6px; color: #bbb; }
.bubble {
    background: #fff;
    border-radius: 4px 12px 12px 12px;
    padding: 10px 14px;
    font-size: 14px;
    color: #333;
    line-height: 1.65;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.bubble .text + .text { margin-top: 6px; }
.bubble .pic { margin-top: 6px; }
.bubble img {
    max-width: 100%;
    max-height: 420px;
    width: auto;
    height: auto;
    object-fit: contain;
    border-radius: 6px;
    display: block;
}
.muted { color: #aaa; }
.foot {
    text-align: center;
    font-size: 11px;
    color: #bbb;
    padding: 8px 0 14px;
}
"""


def build_forward_html(nodes, fake_name=None, fake_qq=None):
    """把合并转发节点渲染成仿聊天记录的 HTML 文档

    Args:
        nodes: 合并转发节点列表，元素形如
            {"type": "node", "data": {"name": 昵称, "uin": QQ, "content": [消息段...]}}
        fake_name/fake_qq: 节点未显式提供昵称/QQ 时的兜底显示值

    Returns:
        str: 完整 HTML 文档
    """
    rows = []
    total = 0
    for node in nodes or []:
        data = node.get("data") if isinstance(node, dict) else None
        if not isinstance(data, dict):
            continue
        total += 1
        name = str(data.get("name") or fake_name or "消息")
        uin = data.get("uin") or fake_qq
        uin_html = f'<span class="uin">{_html.escape(str(uin))}</span>' if uin else ""
        avatar_text = name[:1] if name else "?"
        rows.append(
            '<div class="msg">'
            f'<div class="avatar">{_html.escape(avatar_text)}</div>'
            '<div class="body">'
            f'<div class="meta"><span class="name">{_html.escape(name)}</span>{uin_html}</div>'
            f'<div class="bubble">{_content_to_html(data.get("content"))}</div>'
            '</div>'
            '</div>'
        )

    if not rows:
        rows.append(
            '<div class="msg">'
            '<div class="avatar">?</div>'
            '<div class="body"><div class="meta"><span class="name">消息</span></div>'
            '<div class="bubble"><div class="text muted">（空消息）</div></div>'
            '</div></div>'
        )

    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
        f'<style>{_CHAT_STYLE}</style>\n</head>\n<body>\n'
        '<div class="wrap">\n'
        '<div class="head">'
        '<div class="title">合并转发</div>'
        f'<div class="sub">共 {total} 条消息</div>'
        '</div>\n'
        '<div class="chat">\n'
        + "".join(rows) +
        '\n</div>\n'
        '<div class="foot">Powered by mjbcore</div>\n'
        '</div>\n</body>\n</html>'
    )


def render_forward_chat(nodes, fake_name=None, fake_qq=None, width=780):
    """把合并转发节点渲染成聊天记录图片

    Returns:
        str | None: PNG 临时文件路径（调用方负责清理），失败返回 None
    """
    html_content = build_forward_html(nodes, fake_name=fake_name, fake_qq=fake_qq)
    return html_to_image(html_content, width=width)
