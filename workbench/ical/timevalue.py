"""日期时间与时长的严格解析，只支持字段约定声明的子集。

支持：
* UTC 日期时间（结尾 Z）；
* 带 IANA TZID 参数的本地日期时间（依赖系统 zoneinfo，拒绝浮动时间与缺时区值）；
* 非负整数天/时/分/秒形式的 DURATION。

本地时间转 UTC 的规则：
* 落在夏令时跳时空档的墙钟时间由调用方通过 :func:`local_gap` 识别后跳过；
* 回拨产生的歧义墙钟时间固定选择较早 UTC（``fold=0``）。
DATE 值、闰秒、自定义 VTIMEZONE、周单位与小数时长一律拒绝。
"""
import re
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from .errors import UidError

_DT_RE = re.compile(r'^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$')
_DUR_RE = re.compile(
    r'^\+?P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$')
_WEEKDAYS = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')


def parse_datetime(line):
    """把内容行解析为带时区的 datetime，浮动时间与 DATE 值直接拒绝。

    返回 ``(datetime, kind, tzid)``：kind 为 'utc' 或 'local'，
    local 时 tzid 为 IANA 名称字符串。
    """
    value = line.value
    if line.params.get('VALUE', 'DATE-TIME') != 'DATE-TIME':
        raise UidError('UNSUPPORTED_VALUE',
                       f'只支持 DATE-TIME，遇到 {line.params["VALUE"]}',
                       [line.line_no])
    match = _DT_RE.match(value)
    if not match:
        raise UidError('BAD_DATETIME', f'无法解析日期时间: {value}',
                       [line.line_no])
    year, mon, day, hour, minute, second, zulu = match.groups()
    if second == '60':
        raise UidError('LEAP_SECOND', '不接受闰秒值', [line.line_no])
    tzid_param = line.params.get('TZID')
    if zulu:
        if tzid_param is not None:
            raise UidError('BAD_DATETIME', 'UTC 值不能同时携带 TZID',
                           [line.line_no])
        dt = datetime(int(year), int(mon), int(day), int(hour), int(minute),
                      int(second), tzinfo=timezone.utc)
        return dt, 'utc', None
    if tzid_param is None:
        raise UidError('FLOATING_TIME',
                       f'本地日期时间缺少 IANA TZID: {value}',
                       [line.line_no])
    tz = _load_zone(tzid_param, line.line_no)
    dt = datetime(int(year), int(mon), int(day), int(hour), int(minute),
                  int(second), tzinfo=tz)
    return dt, 'local', tzid_param


def _load_zone(tzid, line_no):
    if tzid.startswith('/'):
        raise UidError('BAD_TZID', f'只接受 IANA 时区名: {tzid}', [line_no])
    try:
        return ZoneInfo(tzid)
    except Exception as exc:
        raise UidError('BAD_TZID', f'无法识别的 IANA 时区: {tzid}',
                       [line_no]) from exc


def local_gap(dt):
    """判断带 tzinfo 的本地墙钟时间是否落在跳时空档。

    zoneinfo 对空档时间在 fold=0/1 下给出不同偏移，且 fold=1 偏移更大。
    """
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('需要带时区的本地时间')
    early = dt.replace(fold=0)
    late = dt.replace(fold=1)
    off_early = early.utcoffset()
    off_late = late.utcoffset()
    return off_late > off_early


def to_utc(dt):
    """本地时间转 UTC；歧义时间取较早 UTC，普通时间不受 fold 影响。"""
    if dt.tzinfo is None:
        raise ValueError('浮动时间不能转换')
    return dt.replace(fold=0).astimezone(timezone.utc)


def parse_duration(line):
    """解析 DURATION 行，返回 timedelta；周单位、小数与负时长拒绝。"""
    match = _DUR_RE.match(line.value)
    if not match:
        raise UidError('BAD_DURATION', f'无法解析时长: {line.value}',
                       [line.line_no])
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def weekday_index(token):
    """BYDAY 记号转 Monday=0 的星期下标；带序数（如 2MO）拒绝。"""
    if token not in _WEEKDAYS:
        return None
    return _WEEKDAYS.index(token)


def identity_key(dt, kind, tzid):
    """实例逻辑键的时间部分：本地按墙钟、UTC 按 UTC 归一。"""
    if kind == 'local':
        return ('L', tzid, dt.replace(tzinfo=None).isoformat())
    return ('U', None, to_utc(dt).replace(tzinfo=None).isoformat())
