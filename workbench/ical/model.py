"""解析结果的数据模型与可追溯行表示。

每条内容行保留：
* 物理行号区间（CRLF 折行后一条逻辑行可能对应多个物理行）；
* 折行还原并去转义前的原始 VALUE 文本；
* 去转义后的文本值与属性参数。

实例导出与 UID 级失败报告都通过 :class:`ContentLine` 回溯到原始属性行，
属性顺序与折行方式不进入语义比较。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContentLine:
    """一条折行还原后的原始内容行。"""

    name: str
    value: str                      # 未去转义的原始值
    params: dict                    # 参数名大写 -> 参数值
    physical_lines: tuple           # 1 起始的物理行号元组

    @property
    def line_no(self):
        """该逻辑行首个物理行号，用于报告定位。"""
        return self.physical_lines[0]

    def decode_text(self):
        """按 TEXT 值规则去转义（\\, \\; \\n \\\\）。"""
        out = []
        i = 0
        while i < len(self.value):
            ch = self.value[i]
            if ch == '\\' and i + 1 < len(self.value):
                nxt = self.value[i + 1]
                out.append({'n': '\n', 'N': '\n',
                            ',': ',', ';': ';', '\\': '\\'}.get(nxt, nxt))
                i += 2
            else:
                out.append(ch)
                i += 1
        return ''.join(out)

    def canonical(self):
        """语义指纹片段：名称、参数与原始值，忽略行号与组件顺序。"""
        return (self.name,
                tuple(sorted((k, v) for k, v in self.params.items())),
                self.value)


@dataclass
class RawComponent:
    """一个 BEGIN:VEVENT ... END:VEVENT 组件的原始行集合。"""

    name: str
    lines: list = field(default_factory=list)          # ContentLine 列表
    line_span: tuple = (0, 0)
    children: list = field(default_factory=list)       # 嵌套组件

    def all(self, name):
        return [ln for ln in self.lines if ln.name == name]

    def first(self, name):
        for ln in self.lines:
            if ln.name == name:
                return ln
        return None


@dataclass
class ParsedCalendar:
    """解析产物：全部 VEVENT 组件（保持输入顺序）与文档来源标识。"""

    events: list
    source: str = ''

    def grouped(self):
        """按 UID 汇集，返回 {uid: [RawComponent, ...]}，保持首次出现顺序。"""
        groups = {}
        for comp in self.events:
            uid_line = comp.first('UID')
            uid = uid_line.value if uid_line is not None else None
            groups.setdefault(uid, []).append(comp)
        return groups
