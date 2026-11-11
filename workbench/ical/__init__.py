"""iCalendar 采集组件：请求、解析、展开三层可分别调用。

典型用法::

    from workbench.ical import fetch_calendar, parse_calendar, expand_window, collect

    raw = fetch_calendar(url)                 # 第一层：HTTP 原始字节
    parsed = parse_calendar(raw, source=url)  # 第二层：折行/转义/参数/VEVENT
    result = expand_window(parsed, start, end)  # 第三层：时间窗实例展开

    # 或一步到位：
    result = collect(url, start, end)

时间窗内的有效实例在 ``result.instances``，违反支持子集的 UID 隔离在
``result.failures``，两者都带可回溯到原始属性行的行号信息。
"""
from .collector import (CollectionResult, EventRecord, Instance,
                        UidFailure, expand)
from .errors import IcalParseError, UidError
from .fetch import fetch as fetch_calendar
from .model import ContentLine, ParsedCalendar, RawComponent
from .parser import parse_calendar, unfold_lines


def expand_window(parsed, window_start, window_end):
    """展开层入口：对已解析日历做版本选择、例外应用与时间窗选取。"""
    return expand(parsed, window_start, window_end)


def collect(source, window_start, window_end, *, timeout=10, headers=None):
    """一步完成 HTTP 请求、解析与展开，source 为 http(s) URL。"""
    raw = fetch_calendar(source, timeout=timeout, headers=headers)
    parsed = parse_calendar(raw, source=source)
    return expand(parsed, window_start, window_end)


__all__ = [
    'fetch_calendar', 'parse_calendar', 'unfold_lines', 'expand_window',
    'collect', 'CollectionResult', 'Instance', 'EventRecord', 'UidFailure',
    'ContentLine', 'RawComponent', 'ParsedCalendar',
    'IcalParseError', 'UidError',
]
