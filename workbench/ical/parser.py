"""iCalendar 词法解析：CRLF 折行、转义、属性参数与组件结构。

本模块只负责把原始字节还原为 :class:`ParsedCalendar`，不解释任何日期语义，
因此请求层可以单独调用它核对折行与参数处理。换行方式（CRLF / LF）与
组件先后顺序不影响解析结果；折行还原在去转义之前完成。
"""
from dataclasses import replace

from .errors import IcalParseError
from .model import ContentLine, ParsedCalendar, RawComponent

_TOKEN_CHARS = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-')


def unfold_lines(raw):
    """把字节流切分为物理行并还原 RFC 5545 折行。

    CRLF、LF 均接受；行首为空格或制表符时并入上一逻辑行（去掉该空白）。
    返回 [(首个物理行号, 还原后的文本行)]，物理行号从 1 开始。
    """
    if isinstance(raw, str):
        text = raw
    else:
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise IcalParseError(f'资料不是合法 UTF-8 文本: {exc}') from exc
    physical = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    if physical and physical[-1] == '':
        physical.pop()
    logical = []
    for idx, line in enumerate(physical, start=1):
        is_continuation = line[:1] in (' ', '\t')
        if not line and not is_continuation:
            raise IcalParseError('出现空内容行', idx)
        if is_continuation:
            if not logical:
                raise IcalParseError('文档起始即出现折行续行', idx)
            span, text_line = logical[-1]
            logical[-1] = (span + (idx,), text_line + line[1:])
        else:
            logical.append(((idx,), line))
    return logical


def _split_params(segment, line_no):
    params = {}
    while segment:
        if segment.startswith('"'):
            raise IcalParseError('属性名位置出现引号', line_no)
        end = _find_unescaped(segment, '=')
        if end is None:
            raise IcalParseError('属性参数缺少等号', line_no)
        name = segment[:end].upper()
        if not name or any(ch not in _TOKEN_CHARS for ch in name):
            raise IcalParseError(f'非法属性参数名: {name}', line_no)
        segment = segment[end + 1:]
        if segment.startswith('"'):
            close = segment.find('"', 1)
            if close == -1:
                raise IcalParseError('引号参数未闭合', line_no)
            value = segment[1:close]
            segment = segment[close + 1:]
            if segment and segment[0] != ';':
                raise IcalParseError('引号参数后存在多余字符', line_no)
        else:
            nxt = _find_unescaped(segment, ';')
            if nxt is None:
                value, segment = segment, ''
            else:
                value, segment = segment[:nxt], segment[nxt + 1:]
        if not value:
            raise IcalParseError(f'参数 {name} 值为空', line_no)
        if name in params:
            raise IcalParseError(f'参数 {name} 重复', line_no)
        params[name] = value
        if segment.startswith(';'):
            segment = segment[1:]
    return params


def _find_unescaped(text, target):
    quoted = False
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        elif ch == target and not quoted:
            return i
    return None


def parse_content_line(line_no, text):
    """解析单条逻辑行为 :class:`ContentLine`（VALUE 保持未去转义）。"""
    colon = _find_unescaped(text, ':')
    if colon is None or colon == 0:
        raise IcalParseError('内容行缺少属性名或冒号', line_no)
    head, value = text[:colon], text[colon + 1:]
    parts = head.split(';')
    name = parts[0].upper()
    if not name or any(ch not in _TOKEN_CHARS for ch in name):
        raise IcalParseError(f'非法属性名: {parts[0]}', line_no)
    params = _split_params(';'.join(parts[1:]), line_no)
    return ContentLine(name=name, value=value, params=params,
                       physical_lines=(line_no,))


def parse_calendar(raw, source=''):
    """解析完整日历字节流，汇集其中所有 VEVENT 组件。

    非 VCALENDAR/VEVENT 组件被跳过但仍做嵌套校验；VEVENT 之外的散行不进入
    采集结果。组件顺序变化不影响后续按 UID 的汇集。
    """
    logical = unfold_lines(raw)
    events = []
    stack = []                      # (组件名, RawComponent)
    for span, text in logical:
        if not text:
            raise IcalParseError('出现空内容行', span[0])
        line = replace(parse_content_line(span[0], text), physical_lines=span)
        if line.name == 'BEGIN':
            comp = RawComponent(name=line.value.upper(),
                                line_span=(span[0], 0))
            if stack:
                stack[-1][1].children.append(comp)
            stack.append((line.value.upper(), comp))
            continue
        if line.name == 'END':
            if not stack or stack[-1][0] != line.value.upper():
                raise IcalParseError('END 与 BEGIN 不匹配', span[0])
            name, comp = stack.pop()
            comp.line_span = (comp.line_span[0], span[0])
            if name == 'VEVENT' and not any(n == 'VEVENT' for n, _ in stack):
                events.append(comp)
            continue
        if not stack:
            # VCALENDAR 之外的散行（VERSION 等）在组件外，忽略
            continue
        stack[-1][1].lines.append(line)
    if stack:
        raise IcalParseError(f'组件 {stack[-1][0]} 缺少 END')
    return ParsedCalendar(events=events, source=source)
