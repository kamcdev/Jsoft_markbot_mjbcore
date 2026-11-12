"""kadset配置格式的读写模块"""

import re

__version__ = "1.0.0"

__all__ = [
    "dump", "dumps", "load", "loads",
    "KadsetError", "KadsetDecodeError",
]

class KadsetError(ValueError):
    """kadset 模块的基础异常。"""


class KadsetDecodeError(KadsetError):
    """解析失败时抛出，含出错位置信息（对齐 json.JSONDecodeError）。"""

    def __init__(self, msg, doc, pos):
        # 计算 pos 所在的行列号
        lineno = doc.count("\n", 0, pos) + 1
        colno = pos - doc.rfind("\n", 0, pos)
        errmsg = "%s: line %d column %d (char %d)" % (msg, lineno, colno, pos)
        super().__init__(errmsg)
        self.msg = msg
        self.doc = doc
        self.pos = pos
        self.lineno = lineno
        self.colno = colno

# 名称（键名/块名）：不包含保留字符和空白
_NAME_RE = re.compile(r"[^&%:{}\[\]\s]+")
# 块开始/结束标记
_BLOCK_START_RE = re.compile(r"%([^%]+)_start%")
_BLOCK_END_RE = re.compile(r"%([^%]+)_end%")
# 数字（不含 NaN/Infinity，单独处理）
_NUMBER_RE = re.compile(
    r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
)
# 数字字面量起点：数字或负号
_NUMBER_START = "-0123456789"

# 字符串转义表
_ESCAPES = {
    '"': '"', "\\": "\\", "/": "/",
    "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
}

# 键名/块名保留字符（序列化时校验）
_RESERVED = set("&%:{}[] \t\r\n")


def _decode_4hex(s, i):
    """把 s[i:i+4] 当作 4 位十六进制解码，失败抛异常。"""
    try:
        return int(s[i:i + 4], 16)
    except ValueError:
        raise


class _Parser:
    """递归下降解析器。

    entry     := (block | pair)*            顶层：多个条目合并为一个 dict
    block     := '%' NAME '_start%' (block | pair)* '%' NAME '_end%'
    pair      := '&' NAME ':' value
    inline    := '{' (pair (','? pair)*)? '}'
    array     := '[' (value (','? value)*)? ']'
    value     := string | number | true | false | null | inline | array
    """

    def __init__(self, doc, object_hook=None, object_pairs_hook=None,
                 parse_int=None, parse_float=None, parse_constant=None):
        self.doc = doc
        self.n = len(doc)
        self.i = 0
        self.object_hook = object_hook
        self.object_pairs_hook = object_pairs_hook
        self.parse_int = parse_int or int
        self.parse_float = parse_float or float
        self.parse_constant = parse_constant

    def error(self, msg, pos=None):
        """抛出带位置的解码错误。"""
        if pos is None:
            pos = self.i
        raise KadsetDecodeError(msg, self.doc, min(pos, self.n))

    def skip_ws(self):
        """跳过空白（含换行）与多余逗号外的所有空白字符。"""
        while self.i < self.n and self.doc[self.i] in " \t\r\n":
            self.i += 1

    def skip_ws_comma(self):
        """跳过空白，并吞掉一个可选的条目分隔逗号。"""
        self.skip_ws()
        if self.i < self.n and self.doc[self.i] == ",":
            self.i += 1
            self.skip_ws()

    def expect(self, ch, what):
        """断言当前字符是 ch，否则报错。"""
        if self.i >= self.n or self.doc[self.i] != ch:
            self.error("期望 '%s'%s" % (ch, what))
        self.i += 1

    def parse_entry(self):
        """解析顶层文档，返回合并后的 dict。"""
        result = {}
        self.skip_ws()
        while self.i < self.n:
            ch = self.doc[self.i]
            if ch == "%":
                name, obj = self.parse_block()
                result[name] = obj
            elif ch == "&":
                key, value = self.parse_pair()
                result[key] = value
            else:
                self.error("顶层只允许块(%%名_start%%)或键值对(&键: 值)，"
                           "遇到 %r" % ch)
            self.skip_ws_comma()
        return self._hook(result)

    def parse_block(self):
        """解析 %名_start% ... %名_end%，返回 (块名, dict)。"""
        start_pos = self.i
        m = _BLOCK_START_RE.match(self.doc, self.i)
        if not m:
            self.error("非法的块开始标记", start_pos)
        name = m.group(1)
        if _RESERVED & set(name) or not name.strip() or name != name.strip():
            self.error("块名 %r 含非法字符" % name, start_pos)
        self.i = m.end()
        obj = {}
        self.skip_ws_comma()
        while True:
            if self.i >= self.n:
                self.error("块 '%s' 缺少结束标记 %%%%%s_end%%%%" % (name, name))
            m = _BLOCK_END_RE.match(self.doc, self.i)
            if m:
                if m.group(1) != name:
                    self.error("块结束标记名 %r 与开始标记名 %r 不一致"
                               % (m.group(1), name))
                self.i = m.end()
                break
            ch = self.doc[self.i]
            if ch == "%":
                sub_name, sub_obj = self.parse_block()
                obj[sub_name] = sub_obj
            elif ch == "&":
                key, value = self.parse_pair()
                obj[key] = value
            else:
                self.error("块内只允许嵌套块或键值对，遇到 %r" % ch)
            self.skip_ws_comma()
        return name, self._hook(obj)

    def parse_pair(self):
        """解析 &键: 值，返回 (键, 值)。"""
        start_pos = self.i
        self.i += 1  # 跳过 '&'
        m = _NAME_RE.match(self.doc, self.i)
        if not m:
            self.error("非法的键名", start_pos)
        key = m.group(0)
        self.i = m.end()
        self.skip_ws()  # 键与冒号之间允许空白
        self.expect(":", "（键 %r 后）" % key)
        self.skip_ws()
        value = self.parse_value()
        return key, value

    def parse_inline(self):
        """解析 { &键: 值 ... } 内联对象，返回 dict。"""
        self.i += 1  # 跳过 '{'
        obj = {}
        self.skip_ws_comma()
        while self.i < self.n and self.doc[self.i] != "}":
            if self.doc[self.i] != "&":
                self.error("内联对象内只允许键值对(&键: 值)")
            key, value = self.parse_pair()
            obj[key] = value
            self.skip_ws_comma()
        self.expect("}", "（内联对象末尾）")
        return self._hook(obj)

    def parse_array(self):
        """解析 [ ... ] 数组，返回 list。"""
        self.i += 1  # 跳过 '['
        arr = []
        self.skip_ws_comma()
        while self.i < self.n and self.doc[self.i] != "]":
            arr.append(self.parse_value())
            self.skip_ws_comma()
        self.expect("]", "（数组末尾）")
        return arr

    def parse_value(self):
        """解析一个值：字符串/数字/布尔/null/内联对象/数组。"""
        if self.i >= self.n:
            self.error("意外的文档结束，期望一个值")
        ch = self.doc[self.i]
        if ch == '"':
            return self.parse_string()
        # -Infinity 以负号开头，需先于数字分支处理
        if self.doc.startswith("-Infinity", self.i):
            self.i += len("-Infinity")
            if self.parse_constant is not None:
                return self.parse_constant("-Infinity")
            return float("-inf")
        if ch in _NUMBER_START:
            return self.parse_number()
        if ch == "{":
            return self.parse_inline()
        if ch == "[":
            return self.parse_array()
        # true / false / null / NaN / Infinity / -Infinity
        for token, value in (
            ("true", True), ("false", False), ("null", None),
        ):
            if self.doc.startswith(token, self.i):
                self.i += len(token)
                return value
        for token, const in (
            ("NaN", "NaN"), ("Infinity", "Infinity"), ("-Infinity", "-Infinity"),
        ):
            if self.doc.startswith(token, self.i):
                self.i += len(token)
                if self.parse_constant is not None:
                    return self.parse_constant(const)
                return float(const)
        self.error("非法的值，遇到 %r" % ch)

    def parse_string(self):
        """解析双引号字符串（含 JSON 转义）。"""
        start_pos = self.i
        self.i += 1  # 跳过 '"'
        buf = []
        doc = self.doc
        while True:
            if self.i >= self.n:
                self.error("字符串未闭合", start_pos)
            ch = doc[self.i]
            if ch == '"':
                self.i += 1
                return "".join(buf)
            if ch == "\\":
                self.i += 1
                if self.i >= self.n:
                    self.error("非法的转义序列", self.i)
                esc = doc[self.i]
                if esc in _ESCAPES:
                    buf.append(_ESCAPES[esc])
                    self.i += 1
                elif esc == "u":
                    try:
                        cp = int(doc[self.i + 1:self.i + 5], 16)
                    except ValueError:
                        self.error("非法的 \\u 转义", self.i)
                    if self.i + 5 > self.n:
                        self.error("非法的 \\u 转义", self.i)
                    self.i += 5
                    # 代理对处理
                    if 0xD800 <= cp <= 0xDBFF and doc.startswith("\\u", self.i):
                        try:
                            lo = int(doc[self.i + 2:self.i + 6], 16)
                        except ValueError:
                            lo = -1
                        if 0xDC00 <= lo <= 0xDFFF:
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00)
                            self.i += 6
                    buf.append(chr(cp))
                else:
                    self.error("非法的转义字符 %r" % ("\\" + esc), self.i)
            else:
                if ch in "\n\r":
                    self.error("字符串中不允许裸换行")
                buf.append(ch)
                self.i += 1

    def parse_number(self):
        """解析数字：含 . e E 的用 parse_float，否则用 parse_int。"""
        m = _NUMBER_RE.match(self.doc, self.i)
        if not m or m.end() == self.i:
            self.error("非法的数字")
        text = m.group(0)
        self.i = m.end()
        if "." in text or "e" in text or "E" in text:
            return self.parse_float(text)
        return self.parse_int(text)

    def _hook(self, obj):
        """对解析出的 dict 应用 object_pairs_hook / object_hook。"""
        if self.object_pairs_hook is not None:
            return self.object_pairs_hook(list(obj.items()))
        if self.object_hook is not None:
            return self.object_hook(obj)
        return obj

def loads(s, *, cls=None, object_hook=None, parse_float=None, parse_int=None,
          parse_constant=None, object_pairs_hook=None, **kw):
    """把 kadset 格式的字符串反序列化为 Python 对象。

    参数语义对齐 json.loads：接受 str/bytes/bytearray（bytes 按 UTF-8 解码）。
    """
    if isinstance(s, (bytes, bytearray)):
        s = s.decode("utf-8")
    if not isinstance(s, str):
        raise TypeError("loads() 参数必须是 str、bytes 或 bytearray，"
                        "而不是 %s" % type(s).__name__)
    if kw:
        raise TypeError("loads() 收到未识别的关键字参数: %s"
                        % ", ".join(kw))
    return _Parser(s, object_hook=object_hook,
                   object_pairs_hook=object_pairs_hook,
                   parse_int=parse_int, parse_float=parse_float,
                   parse_constant=parse_constant).parse_entry()


def load(fp, *, cls=None, object_hook=None, parse_float=None, parse_int=None,
         parse_constant=None, object_pairs_hook=None, **kw):
    """从文本文件对象 fp 读取并解析 kadset 文档。"""
    return loads(fp.read(), cls=cls, object_hook=object_hook,
                 parse_float=parse_float, parse_int=parse_int,
                 parse_constant=parse_constant,
                 object_pairs_hook=object_pairs_hook, **kw)

_ESCAPE_DCT = {
    "\\": "\\\\", '"': '\\"',
    "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t",
}
for _i in range(0x20):
    _ESCAPE_DCT.setdefault(chr(_i), "\\u%04x" % _i)

_ASCII_ESCAPE_RE = re.compile(r'[\\"]|[^\ -~]')
_NON_ASCII_RE = re.compile(r'[^\x00-\x7F]')


def _encode_string(s, ensure_ascii=True):
    """把字符串编码为带双引号的字面量。"""
    if ensure_ascii:
        def repl(m):
            ch = m.group(0)
            try:
                return _ESCAPE_DCT[ch]
            except KeyError:
                return "\\u%04x" % ord(ch)
        return '"' + _ASCII_ESCAPE_RE.sub(repl, s) + '"'
    # 非 ASCII 模式：只转义引号、反斜杠和控制字符
    def repl_ctrl(m):
        ch = m.group(0)
        try:
            return _ESCAPE_DCT[ch]
        except KeyError:
            return "\\u%04x" % ord(ch)
    out = s.translate({ord("\\"): "\\\\", ord('"'): '\\"'})
    out = re.sub(r'[\x00-\x1f]', repl_ctrl, out)
    return '"' + out + '"'


def _check_name(name):
    """校验键名/块名不含保留字符，返回原名称。"""
    if _RESERVED & set(name) or not name:
        raise ValueError(
            "键名/块名 %r 含保留字符（& %% : { } [ ] 或空白）或为空" % name)
    return name


class _Serializer:
    """kadset 序列化器。"""

    def __init__(self, skipkeys=False, ensure_ascii=True, check_circular=True,
                 allow_nan=True, indent=None, separators=None, default=None,
                 sort_keys=False):
        self.skipkeys = skipkeys
        self.ensure_ascii = ensure_ascii
        self.check_circular = check_circular
        self.allow_nan = allow_nan
        self.indent = indent if isinstance(indent, int) else None
        self.default = default
        self.sort_keys = sort_keys
        self._markers = {} if check_circular else None
        # 数组/内联对象的紧凑分隔
        self.item_sep = ", "

    def serialize(self, obj):
        """序列化顶层对象，返回字符串。"""
        chunks = []
        self._write_top(obj, 0, chunks)
        if chunks:
            chunks.append("\n")
        return "".join(chunks)

    def _write_top(self, obj, level, chunks):
        """顶层：dict 拆为块/键值对；其他类型视为单值文档不合法。"""
        obj = self._resolve(obj)
        if not isinstance(obj, dict):
            raise TypeError("顶层对象必须是 dict，而不是 %s"
                            % type(obj).__name__)
        self._enter(obj)
        try:
            items = self._sorted_items(obj)
            first = True
            for key, value in items:
                if not first:
                    chunks.append("\n")
                first = False
                self._write_member(key, value, level, chunks)
        finally:
            self._leave(obj)

    def _write_member(self, key, value, level, chunks):
        """写一个成员：值为 dict → 块；否则 → &键: 值。"""
        key = self._stringify_key(key)
        value = self._resolve(value)
        pad = " " * (self.indent * level) if self.indent else ""
        if isinstance(value, dict):
            chunks.append("%s%%" % pad)
            chunks.append(key)
            chunks.append("_start%\n")
            if value:
                self._write_block_body(value, level + 1, chunks)
                chunks.append("\n")
            chunks.append("%s%%" % pad)
            chunks.append(key)
            chunks.append("_end%")
        else:
            chunks.append("%s&" % pad)
            chunks.append(key)
            chunks.append(": ")
            self._write_inline_value(value, level, chunks)

    def _write_block_body(self, obj, level, chunks):
        """写块体（成员列表），每个成员一行。"""
        self._enter(obj)
        try:
            items = self._sorted_items(obj)
            first = True
            for key, value in items:
                if not first:
                    chunks.append("\n")
                first = False
                self._write_member(key, value, level, chunks)
        finally:
            self._leave(obj)

    def _write_inline_value(self, value, level, chunks):
        """写 &键: 后的值或数组元素：支持所有类型。"""
        value = self._resolve(value)
        if isinstance(value, dict):
            self._write_inline_obj(value, level, chunks)
        elif isinstance(value, list):
            self._write_array(value, level, chunks)
        else:
            chunks.append(self._scalar(value))

    def _write_inline_obj(self, obj, level, chunks):
        """内联对象 { ... }：indent=None 单行，否则多行。"""
        self._enter(obj)
        try:
            items = self._sorted_items(obj)
            if not items:
                chunks.append("{}")
                return
            if self.indent is None:
                entries = []
                for key, value in items:
                    key = self._stringify_key(key)
                    buf = ["&%s: " % key]
                    self._write_inline_value(value, 0, buf)
                    entries.append("".join(buf))
                chunks.append("{" + ", ".join(entries) + "}")
            else:
                pad = " " * (self.indent * level)
                inner = " " * (self.indent * (level + 1))
                chunks.append("{\n")
                first = True
                for key, value in items:
                    if not first:
                        chunks.append(",\n")
                    first = False
                    chunks.append(inner + "&" + key + ": ")
                    self._write_inline_value(value, level + 1, chunks)
                chunks.append("\n" + pad + "}")
        finally:
            self._leave(obj)

    def _write_array(self, arr, level, chunks):
        """数组 [ ... ]：indent=None 单行，否则多行。"""
        self._enter(arr)
        try:
            if not arr:
                chunks.append("[]")
                return
            if self.indent is None:
                parts = []
                for item in arr:
                    self._write_inline_value(item, 0, parts)
                chunks.append("[" + ", ".join(parts) + "]")
            else:
                pad = " " * (self.indent * level)
                inner = " " * (self.indent * (level + 1))
                chunks.append("[\n")
                first = True
                for item in arr:
                    if not first:
                        chunks.append(",\n")
                    first = False
                    chunks.append(inner)
                    self._write_inline_value(item, level + 1, chunks)
                chunks.append("\n" + pad + "]")
        finally:
            self._leave(arr)

    def _scalar(self, value):
        """标量 → 字面量字符串。"""
        if value is True:
            return "true"
        if value is False:
            return "false"
        if value is None:
            return "null"
        if isinstance(value, int):
            return repr(value)
        if isinstance(value, float):
            if value != value:  # NaN
                if not self.allow_nan:
                    raise ValueError("不允许输出 NaN（allow_nan=False）")
                return "NaN"
            if value == float("inf"):
                if not self.allow_nan:
                    raise ValueError("不允许输出 Infinity（allow_nan=False）")
                return "Infinity"
            if value == float("-inf"):
                if not self.allow_nan:
                    raise ValueError("不允许输出 -Infinity（allow_nan=False）")
                return "-Infinity"
            return repr(value)
        if isinstance(value, str):
            return _encode_string(value, self.ensure_ascii)
        raise TypeError("类型 %s 不可序列化" % type(value).__name__)

    def _resolve(self, obj):
        """处理 default 钩子，直到对象变为可序列化类型。"""
        while obj is not None and not isinstance(
                obj, (bool, int, float, str, dict, list)):
            if self.default is None:
                raise TypeError("类型 %s 不可序列化（可提供 default 钩子）"
                                % type(obj).__name__)
            obj = self.default(obj)
        return obj

    def _enter(self, obj):
        """进入容器序列化：登记 marker 以检测循环引用。"""
        if self._markers is not None:
            marker = id(obj)
            if marker in self._markers:
                raise ValueError("检测到循环引用")
            self._markers[marker] = obj

    def _leave(self, obj):
        """离开容器序列化：清除 marker（同一对象可再次出现）。"""
        if self._markers is not None:
            del self._markers[id(obj)]

    def _stringify_key(self, key):
        """键 → 合法名称：str 直接校验；bool/int/float/None 按 json 规则转换。"""
        if isinstance(key, str):
            return _check_name(key)
        if self.skipkeys:
            return None
        raise TypeError("键必须是 str，而不是 %s（可设 skipkeys=True 跳过）"
                        % type(key).__name__)

    def _sorted_items(self, obj):
        """dict → 排序/过滤后的 (键, 值) 列表。"""
        items = []
        for key, value in obj.items():
            if isinstance(key, str):
                items.append((key, value))
            elif self.skipkeys:
                continue
            else:
                raise TypeError("键必须是 str，而不是 %s（可设 skipkeys=True 跳过）"
                                % type(key).__name__)
        if self.sort_keys:
            items.sort(key=lambda kv: kv[0])
        return items

def dumps(obj, *, skipkeys=False, ensure_ascii=True, check_circular=True,
          allow_nan=True, cls=None, indent=None, separators=None,
          default=None, sort_keys=False, **kw):
    """把 Python 对象序列化为 kadset 格式的字符串。

    参数语义对齐 json.dumps。
    """
    if kw:
        raise TypeError("dumps() 收到未识别的关键字参数: %s" % ", ".join(kw))
    ser = _Serializer(skipkeys=skipkeys, ensure_ascii=ensure_ascii,
                      check_circular=check_circular, allow_nan=allow_nan,
                      indent=indent, separators=separators,
                      default=default, sort_keys=sort_keys)
    return ser.serialize(obj)


def dump(obj, fp, *, skipkeys=False, ensure_ascii=True, check_circular=True,
         allow_nan=True, cls=None, indent=None, separators=None,
         default=None, sort_keys=False, **kw):
    """把 Python 对象序列化为 kadset 格式并写入文本文件对象 fp。"""
    fp.write(dumps(obj, skipkeys=skipkeys, ensure_ascii=ensure_ascii,
                   check_circular=check_circular, allow_nan=allow_nan,
                   cls=cls, indent=indent, separators=separators,
                   default=default, sort_keys=sort_keys, **kw))
