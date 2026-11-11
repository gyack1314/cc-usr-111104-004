"""iCalendar 采集组件的异常类型。

解析阶段的结构性错误抛出 :class:`IcalParseError`，它不属于任何 UID；
语义子集校验失败统一抛出 :class:`UidError`，由采集层转成 UID 级失败报告，
不影响同一文档中的其他 UID。
"""


class IcalParseError(Exception):
    """原始字节无法构成合法的 iCalendar 结构（折行、词法、组件嵌套等）。"""

    def __init__(self, message, line_no=None):
        super().__init__(message)
        self.line_no = line_no


class UidError(Exception):
    """某个 UID 违反仓库声明的支持子集，该 UID 必须整体隔离。"""

    def __init__(self, code, message, lines=()):
        super().__init__(f'[{code}] {message}')
        self.code = code
        self.message = message
        self.lines = tuple(lines)
