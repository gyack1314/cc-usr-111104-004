"""RRULE 子集解析与重复序列展开。

只支持 FREQ=DAILY/WEEKLY，可选 INTERVAL、COUNT、UNTIL、BYDAY（仅周规则、
不带序数）。原序列候选先按 COUNT/UNTIL 全部产生并计数，再由调用方剔除
夏令时空档与 EXDATE：即 COUNT 统计的是原重复序列数量，不扣除空档与例外。
"""
from datetime import timedelta

from .errors import UidError
from .timevalue import local_gap, to_utc, weekday_index

_ALLOWED = {'FREQ', 'INTERVAL', 'COUNT', 'UNTIL', 'BYDAY'}


class RecurRule:
    """校验后的 RRULE 摘要，供序列生成使用。"""

    def __init__(self, freq, interval, count, until, byday):
        self.freq = freq
        self.interval = interval
        self.count = count
        self.until = until              # aware datetime（UTC）或 None
        self.byday = byday              # 星期下标元组，Monday=0

    @property
    def bounded(self):
        return self.count is not None or self.until is not None


def parse_rrule(line, dtstart_kind, dtstart_tzid, resolve_until):
    """解析并校验单条 RRULE 行，任何超子集特性都拒绝该 UID。"""
    terms = {}
    for term in line.value.split(';'):
        if '=' not in term:
            raise UidError('BAD_RRULE', f'RRULE 片段缺少等号: {term}',
                           [line.line_no])
        key, raw = term.split('=', 1)
        key = key.upper()
        if key not in _ALLOWED:
            raise UidError('UNSUPPORTED_RRULE', f'不支持的 RRULE 部分: {key}',
                           [line.line_no])
        if key in terms:
            raise UidError('BAD_RRULE', f'RRULE 部分重复: {key}',
                           [line.line_no])
        terms[key] = raw
    freq = terms.get('FREQ')
    if freq not in ('DAILY', 'WEEKLY'):
        raise UidError('UNSUPPORTED_RRULE', f'只支持 DAILY/WEEKLY，遇到 {freq}',
                       [line.line_no])
    interval = _positive_int(terms, 'INTERVAL', line.line_no, default=1)
    count = _positive_int(terms, 'COUNT', line.line_no, default=None)
    until = None
    if 'UNTIL' in terms:
        until = resolve_until(terms['UNTIL'], line)
    if count is not None and until is not None:
        raise UidError('BAD_RRULE', 'COUNT 与 UNTIL 不能同时使用',
                       [line.line_no])
    byday = ()
    if 'BYDAY' in terms:
        if freq != 'WEEKLY':
            raise UidError('UNSUPPORTED_RRULE', 'BYDAY 仅可用于 WEEKLY',
                           [line.line_no])
        tokens = terms['BYDAY'].split(',')
        if any(weekday_index(tok) is None for tok in tokens):
            raise UidError('UNSUPPORTED_RRULE',
                           f'BYDAY 含序数或非法记号: {terms["BYDAY"]}',
                           [line.line_no])
        byday = tuple(sorted(weekday_index(tok) for tok in tokens))
    return RecurRule(freq, interval, count, until, byday)


def _positive_int(terms, key, line_no, default):
    if key not in terms:
        return default
    raw = terms[key]
    if not raw.isdigit() or int(raw) <= 0:
        raise UidError('BAD_RRULE', f'{key} 必须为正整数: {raw}', [line_no])
    return int(raw)


def _wall_utc(wall_naive, tzinfo):
    aware = wall_naive.replace(tzinfo=tzinfo)
    if local_gap(aware):
        aware = aware.replace(fold=0)
    return to_utc(aware)


def generate_series(dtstart_naive, rule, tzinfo, horizon_utc=None):
    """按墙钟时间产生原序列候选（naive 本地时间 + 时区上下文）。

    返回有序去重的 naive datetime 列表。COUNT 在此阶段按原序列计数，
    空档剔除与 EXDATE 由采集层后续处理。

    有界规则（COUNT/UNTIL）总是完整生成，保证 COUNT 语义；无界规则必须
    传入 ``horizon_utc``（通常是时间窗结束的 UTC 时刻），生成到候选 UTC
    不早于视界即停止，避免无限循环，也不会截断远处窗口需要的实例。
    """
    if not rule.bounded and horizon_utc is None:
        raise ValueError('无界 RRULE 展开必须提供 horizon_utc')
    candidates = []
    seen = set()

    def emit(wall):
        if wall not in seen:
            seen.add(wall)
            candidates.append(wall)

    def beyond_horizon(wall):
        return (not rule.bounded
                and horizon_utc is not None
                and _wall_utc(wall, tzinfo) > horizon_utc)

    start = dtstart_naive
    if rule.freq == 'DAILY':
        step = timedelta(days=rule.interval)
        period_no = 0
        while True:
            wall = start + step * period_no
            if rule.until is not None and _past_until(wall, rule, tzinfo):
                break
            if beyond_horizon(wall):
                break
            emit(wall)
            period_no += 1
            if rule.count is not None and len(candidates) >= rule.count:
                break
    else:
        weekdays = rule.byday or (start.weekday(),)
        period_no = 0
        produced = 0
        while True:
            week_base = start + timedelta(weeks=rule.interval * period_no)
            monday = week_base - timedelta(days=week_base.weekday())
            period_walls = sorted(monday + timedelta(days=wd)
                                  for wd in weekdays)
            period_emitted = False
            for wall in period_walls:
                if wall < start:
                    continue
                if rule.until is not None and _past_until(wall, rule, tzinfo):
                    return candidates
                if beyond_horizon(wall):
                    return candidates
                emit(wall)
                period_emitted = True
                produced += 1
                if rule.count is not None and produced >= rule.count:
                    return candidates
            if not period_emitted and beyond_horizon(monday):
                return candidates
            period_no += 1
    return candidates


def _past_until(wall_naive, rule, tzinfo):
    return _wall_utc(wall_naive, tzinfo) > rule.until
