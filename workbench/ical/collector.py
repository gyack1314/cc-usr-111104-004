"""按 UID 汇集 VEVENT 并展开时间窗内实际有效的活动实例。

固定处理顺序（任何资料都走同一条管线）：
1. 组件级子集校验，不支持的规则与 RANGE=THISANDFUTURE 直接拒绝该 UID；
2. 同 UID + RECURRENCE-ID 的修订按 SEQUENCE、再按 DTSTAMP 选最高排序位置，
   同位置语义冲突则隔离，不依赖输入顺序；
3. 主事件生成原重复序列，COUNT 统计原序列，空档与 EXDATE 之后才剔除；
4. 合并 RDATE、应用 EXDATE，再用 RECURRENCE-ID 覆盖（身份保持原序列开始值，
   改期移出窗口也只替换不另建）；
5. 按实例区间与时间窗的非空交集选取。
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .errors import UidError
from .recur import generate_series, parse_rrule
from .timevalue import (_DT_RE, _load_zone, identity_key, local_gap,
                        parse_datetime, parse_duration, to_utc)

_SINGLE_PROPS = ('UID', 'DTSTAMP', 'DTSTART', 'DTEND', 'DURATION', 'RRULE',
                 'SEQUENCE', 'STATUS', 'RECURRENCE-ID')
_KNOWN_STATUS = {'TENTATIVE', 'CONFIRMED', 'CANCELLED'}
_TIME_PARAMS = {'TZID', 'VALUE'}


@dataclass
class EventRecord:
    """单个 VEVENT 组件校验后的语义视图。"""

    component: object
    uid: str
    rid_key: object                 # None 表示主事件，否则为身份键元组
    sequence: int
    dtstamp: object
    status: str
    start: object                   # aware datetime
    kind: str                       # 'utc' | 'local'
    tzid: object
    duration: timedelta
    rule: object
    rdates: list
    exdates: list
    fingerprint: tuple

    @property
    def is_override(self):
        return self.rid_key is not None

    @property
    def start_naive(self):
        return self.start.replace(tzinfo=None)

    @property
    def start_utc(self):
        return to_utc(self.start)


@dataclass
class Instance:
    """展开后的一个有效活动实例。"""

    uid: str
    identity: tuple
    start: object
    duration: timedelta
    kind: str
    tzid: object
    status: str
    summary: object
    origin: str
    moved: bool
    record: EventRecord
    source: str

    @property
    def end(self):
        if self.kind == 'local':
            end_naive = self.start.replace(tzinfo=None) + self.duration
            return end_naive.replace(tzinfo=self.start.tzinfo, fold=0)
        return self.start + self.duration

    @property
    def start_utc(self):
        return to_utc(self.start)

    @property
    def end_utc(self):
        return to_utc(self.end)

    def to_dict(self):
        rid_kind, rid_value, rid_tzid = _describe_identity(self.identity)
        return {
            'uid': self.uid,
            'recurrence_id': {
                'kind': rid_kind,
                'tzid': rid_tzid,
                'value': rid_value,
            },
            'start': _time_dict(self.start, self.kind, self.tzid),
            'end': _time_dict(self.end, self.kind, self.tzid),
            'duration_seconds': int(self.duration.total_seconds()),
            'status': self.status,
            'summary': self.summary,
            'origin': self.origin,
            'moved': self.moved,
            'source': self.source,
            'provenance': _provenance(self.record),
        }


@dataclass
class UidFailure:
    """UID 级失败报告，行号可回溯到原始属性行。"""

    uid: object
    code: str
    message: str
    lines: tuple
    source: str

    def to_dict(self):
        return {
            'uid': self.uid,
            'code': self.code,
            'message': self.message,
            'lines': list(self.lines),
            'source': self.source,
        }


@dataclass
class CollectionResult:
    instances: list = field(default_factory=list)
    failures: list = field(default_factory=list)

    def to_dict(self):
        return {
            'instances': [item.to_dict() for item in self.instances],
            'failures': [item.to_dict() for item in self.failures],
        }


def expand(parsed, window_start, window_end):
    """把 :class:`~workbench.ical.model.ParsedCalendar` 展开为采集结果。

    窗口边界必须是带时区的 datetime，按 UTC 比较；实例与窗口存在非空交集
    （DTEND 不含端点）即选中。
    """
    win_start, win_end = _window_utc(window_start, window_end)
    result = CollectionResult()
    for uid, components in parsed.grouped().items():
        try:
            if uid is None:
                raise UidError('MISSING_UID', 'VEVENT 缺少 UID 属性',
                               [c.line_span[0] for c in components])
            records = [_validate(c, uid) for c in components]
            selected = _select_revisions(records, uid)
            instances = _build_instances(uid, selected, parsed.source,
                                         win_end)
        except UidError as exc:
            result.failures.append(UidFailure(
                uid=uid, code=exc.code, message=exc.message,
                lines=exc.lines, source=parsed.source))
            continue
        for inst in instances:
            if inst.start_utc < win_end and inst.end_utc > win_start:
                result.instances.append(inst)
    result.instances.sort(key=lambda x: (x.start_utc, x.uid, x.identity))
    return result


def _window_utc(window_start, window_end):
    bounds = []
    for value in (window_start, window_end):
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError('时间窗边界必须是带时区的 datetime')
        bounds.append(value.astimezone(timezone.utc))
    if bounds[0] >= bounds[1]:
        raise ValueError('时间窗开始必须早于结束')
    return bounds


# ---------- 组件级校验 -----------------------------------------------------

def _validate(component, uid):
    if component.children:
        raise UidError('NESTED_COMPONENT', 'VEVENT 不允许嵌套组件',
                       [component.line_span[0]])
    lines = component.lines
    _check_multiplicity(lines)
    for line in lines:
        if line.params.get('RANGE') == 'THISANDFUTURE':
            raise UidError('UNSUPPORTED_RANGE',
                           'RANGE=THISANDFUTURE 不在支持子集内',
                           [line.line_no])
    uid_line = _require(lines, 'UID', uid)
    if not uid_line.value:
        raise UidError('MISSING_UID', 'UID 值为空', [uid_line.line_no])

    dtstamp_line = _require(lines, 'DTSTAMP', uid)
    dtstamp, stamp_kind, _ = parse_datetime(dtstamp_line)
    if stamp_kind != 'utc':
        raise UidError('BAD_DTSTAMP', 'DTSTAMP 必须为 UTC',
                       [dtstamp_line.line_no])

    sequence = 0
    seq_line = component.first('SEQUENCE')
    if seq_line is not None:
        if not seq_line.value.isdigit():
            raise UidError('BAD_SEQUENCE',
                           f'SEQUENCE 必须为非负整数: {seq_line.value}',
                           [seq_line.line_no])
        sequence = int(seq_line.value)

    status = 'CONFIRMED'
    status_line = component.first('STATUS')
    if status_line is not None:
        status = status_line.value.upper()
        if status not in _KNOWN_STATUS:
            raise UidError('BAD_STATUS', f'未知 STATUS: {status_line.value}',
                           [status_line.line_no])

    dtstart_line = _require(lines, 'DTSTART', uid)
    _check_time_params(dtstart_line)
    start, kind, tzid = parse_datetime(dtstart_line)

    end_line = component.first('DTEND')
    dur_line = component.first('DURATION')
    if end_line is not None and dur_line is not None:
        raise UidError('BAD_TIME_RANGE', 'DTEND 与 DURATION 不能同时出现',
                       [end_line.line_no, dur_line.line_no])
    if end_line is None and dur_line is None:
        raise UidError('MISSING_DURATION', '活动必须有 DTEND 或 DURATION',
                       [component.line_span[0]])
    if end_line is not None:
        _check_time_params(end_line)
        end, end_kind, end_tzid = parse_datetime(end_line)
        _same_value_kind(end_kind, end_tzid, kind, tzid, end_line)
        duration = end.replace(tzinfo=None) - start.replace(tzinfo=None)
    else:
        duration = parse_duration(dur_line)
    if duration <= timedelta(0):
        raise UidError('NONPOSITIVE_DURATION', '活动必须有有限正时长',
                       [component.line_span[0]])

    rid_key = None
    rid_line = component.first('RECURRENCE-ID')
    if rid_line is not None:
        _check_time_params(rid_line)
        rid_dt, rid_kind, rid_tzid = parse_datetime(rid_line)
        _same_value_kind(rid_kind, rid_tzid, kind, tzid, rid_line)
        if kind == 'local' and local_gap(rid_dt):
            raise UidError('GAP_DATETIME',
                           'RECURRENCE-ID 落在夏令时跳时空档',
                           [rid_line.line_no])
        rid_key = identity_key(rid_dt, rid_kind, rid_tzid)

    rule = None
    rule_line = component.first('RRULE')
    if rule_line is not None:
        if rid_line is not None:
            raise UidError('OVERRIDE_NOT_ATOMIC',
                           '覆盖组件不能携带 RRULE', [rule_line.line_no])
        rule = parse_rrule(
            rule_line, kind, tzid,
            lambda raw, line: _parse_until(raw, line, kind))
        if rule.byday and start.weekday() not in rule.byday:
            raise UidError('BAD_RRULE',
                           'DTSTART 的星期必须出现在 BYDAY 中',
                           [rule_line.line_no])

    rdates = _collect_dates(component.all('RDATE'), kind, tzid, uid)
    exdates = _collect_dates(component.all('EXDATE'), kind, tzid, uid)
    if rid_line is not None and (rdates or exdates):
        raise UidError('OVERRIDE_NOT_ATOMIC',
                       '覆盖组件不能携带 RDATE/EXDATE',
                       [component.line_span[0]])

    fingerprint = tuple(sorted(line.canonical() for line in lines))
    return EventRecord(
        component=component, uid=uid, rid_key=rid_key, sequence=sequence,
        dtstamp=dtstamp, status=status, start=start, kind=kind, tzid=tzid,
        duration=duration, rule=rule, rdates=rdates, exdates=exdates,
        fingerprint=fingerprint)


def _check_multiplicity(lines):
    seen = set()
    for line in lines:
        if line.name in _SINGLE_PROPS and line.name in seen:
            raise UidError('DUPLICATE_PROPERTY',
                           f'单值属性 {line.name} 出现多次', [line.line_no])
        seen.add(line.name)


def _require(lines, name, uid):
    for line in lines:
        if line.name == name:
            return line
    raise UidError('MISSING_PROPERTY', f'{uid} 缺少必需属性 {name}',
                   [lines[0].line_no if lines else 0])


def _check_time_params(line):
    extra = set(line.params) - _TIME_PARAMS
    if extra:
        raise UidError('BAD_PARAM',
                       f'{line.name} 出现不支持的参数: {sorted(extra)}',
                       [line.line_no])


def _same_value_kind(actual_kind, actual_tzid, want_kind, want_tzid, line):
    if actual_kind != want_kind or actual_tzid != want_tzid:
        raise UidError('VALUE_KIND_MISMATCH',
                       f'{line.name} 的值类型/时区须与 DTSTART 一致',
                       [line.line_no])


def _parse_until(raw, line, dtstart_kind):
    match = _DT_RE.match(raw)
    if not match or match.group(7) != 'Z':
        raise UidError('BAD_UNTIL',
                       'UNTIL 必须为 UTC 日期时间（结尾 Z）', [line.line_no])
    year, mon, day, hour, minute, second, _ = match.groups()
    if second == '60':
        raise UidError('LEAP_SECOND', '不接受闰秒值', [line.line_no])
    return datetime(int(year), int(mon), int(day), int(hour), int(minute),
                    int(second), tzinfo=timezone.utc)


def _collect_dates(lines, kind, tzid, uid):
    values = []
    for line in lines:
        _check_time_params(line)
        if line.params.get('VALUE', 'DATE-TIME') != 'DATE-TIME':
            raise UidError('UNSUPPORTED_VALUE',
                           'RDATE/EXDATE 只支持 DATE-TIME', [line.line_no])
        for raw in line.value.split(','):
            if not raw:
                raise UidError('BAD_DATETIME', '日期时间值为空',
                               [line.line_no])
            dt, value_kind, value_tzid = _parse_value(raw, line)
            _same_value_kind(value_kind, value_tzid, kind, tzid, line)
            if kind == 'local' and local_gap(dt):
                raise UidError('GAP_DATETIME',
                               f'{line.name} 值落在夏令时跳时空档: {raw}',
                               [line.line_no])
            values.append(dt)
    return values


def _parse_value(raw, line):
    match = _DT_RE.match(raw)
    if not match:
        raise UidError('BAD_DATETIME', f'无法解析日期时间: {raw}',
                       [line.line_no])
    year, mon, day, hour, minute, second, zulu = match.groups()
    if second == '60':
        raise UidError('LEAP_SECOND', '不接受闰秒值', [line.line_no])
    if zulu:
        return (datetime(int(year), int(mon), int(day), int(hour),
                         int(minute), int(second), tzinfo=timezone.utc),
                'utc', None)
    tz = line.params.get('TZID')
    if tz is None:
        raise UidError('FLOATING_TIME',
                       f'本地日期时间缺少 IANA TZID: {raw}', [line.line_no])
    return (datetime(int(year), int(mon), int(day), int(hour), int(minute),
                     int(second), tzinfo=_load_zone(tz, line.line_no)),
            'local', tz)


# ---------- 修订选择 -------------------------------------------------------

def _select_revisions(records, uid):
    buckets = {}
    for record in records:
        buckets.setdefault(record.rid_key, []).append(record)
    selected = {}
    for rid_key, bucket in buckets.items():
        ranked = sorted(bucket,
                        key=lambda r: (r.sequence, r.dtstamp))
        top = ranked[-1]
        winners = [r for r in ranked
                   if (r.sequence, r.dtstamp) == (top.sequence, top.dtstamp)]
        if len(winners) > 1:
            fingerprints = {r.fingerprint for r in winners}
            if len(fingerprints) > 1:
                raise UidError(
                    'REVISION_CONFLICT',
                    f'{uid} 的同排序位置修订存在内容冲突',
                    sorted(line.line_no for r in winners
                           for line in r.component.lines))
            top = winners[0]      # 完全重复的修订视为同一份
        selected[rid_key] = top
    return selected


# ---------- 实例构建 -------------------------------------------------------

def _build_instances(uid, selected, source, window_end_utc):
    master = selected.get(None)
    overrides = [r for r in selected.values() if r.is_override]
    if master is None:
        for record in overrides:
            if record.status != 'CANCELLED':
                raise UidError('ORPHAN_RECURRENCE_ID',
                               f'{uid} 的覆盖组件没有对应主事件',
                               [record.component.line_span[0]])
        return []
    if master.status == 'CANCELLED':
        return []                   # 主事件取消影响整组

    tzinfo = master.start.tzinfo
    # 无界规则的生成视界至少覆盖所有覆盖身份：移出窗口的改期/取消也要能
    # 替换原实例，不能因为窗口截断而把 RECURRENCE-ID 误判为孤立
    horizon = window_end_utc
    if master.rule is not None and not master.rule.bounded:
        for record in overrides:
            rid_utc = _identity_utc(record.rid_key)
            if rid_utc > horizon:
                horizon = rid_utc
    walls = {}                      # identity -> naive wall
    origin = {}
    if master.rule is not None:
        series = generate_series(master.start_naive, master.rule, tzinfo,
                                 horizon)
    else:
        series = [master.start_naive]
    for wall in series:
        aware = wall.replace(tzinfo=tzinfo) if master.kind == 'local' \
            else wall.replace(tzinfo=timezone.utc)
        if master.kind == 'local' and local_gap(aware):
            continue                # 空档跳过，但已计入 COUNT
        key = identity_key(aware, master.kind, master.tzid)
        walls.setdefault(key, wall)
        origin.setdefault(key, 'master')

    for rdate in master.rdates:
        key = identity_key(rdate, master.kind, master.tzid)
        if key not in walls:
            walls[key] = rdate.replace(tzinfo=None)
            origin[key] = 'rdate'

    for exdate in master.exdates:
        walls.pop(identity_key(exdate, master.kind, master.tzid), None)

    instances = []
    for record in overrides:
        if record.status == 'CANCELLED':
            walls.pop(record.rid_key, None)
            continue
        if record.rid_key not in walls:
            raise UidError('ORPHAN_RECURRENCE_ID',
                           f'{uid} 的 RECURRENCE-ID 在原序列/RDATE 中不存在',
                           [record.component.line_span[0]])
        walls.pop(record.rid_key)
        instances.append(_make_override(uid, record, source))

    for key, wall in walls.items():
        if master.kind == 'local':
            start = wall.replace(tzinfo=tzinfo, fold=0)
        else:
            start = wall.replace(tzinfo=timezone.utc)
        instances.append(Instance(
            uid=uid, identity=key, start=start, duration=master.duration,
            kind=master.kind, tzid=master.tzid, status=master.status,
            summary=_summary(master), origin=origin[key], moved=False,
            record=master, source=source))
    return instances


def _identity_utc(identity):
    kind, tzid, iso_value = identity
    naive = datetime.fromisoformat(iso_value)
    if kind == 'U':
        return naive.replace(tzinfo=timezone.utc)
    aware = naive.replace(tzinfo=_load_zone(tzid, 0))
    return to_utc(aware)


def _make_override(uid, record, source):
    # 身份始终沿用原 RECURRENCE-ID，即便改期后的开始时间已移出窗口
    return Instance(
        uid=uid, identity=record.rid_key, start=record.start,
        duration=record.duration, kind=record.kind, tzid=record.tzid,
        status=record.status, summary=_summary(record), origin='override',
        moved=True, record=record, source=source)


def _summary(record):
    line = record.component.first('SUMMARY')
    return line.decode_text() if line is not None else None


def _describe_identity(identity):
    kind, tzid, value = identity
    if kind == 'L':
        return 'local', value, tzid
    return 'utc', value, None


def _time_dict(dt, kind, tzid):
    if kind == 'local':
        return {
            'utc': to_utc(dt).isoformat().replace('+00:00', 'Z'),
            'local': dt.replace(tzinfo=None).isoformat(),
            'tzid': tzid,
        }
    return {'utc': to_utc(dt).isoformat().replace('+00:00', 'Z'),
            'local': None, 'tzid': None}


def _provenance(record):
    properties = {}
    physical = []
    for line in record.component.lines:
        properties.setdefault(line.name, []).append({
            'value': line.value,
            'params': dict(line.params),
            'physical_lines': list(line.physical_lines),
        })
        physical.extend(line.physical_lines)
    return {
        'component_span': list(record.component.line_span),
        'physical_lines': sorted(set(physical)),
        'properties': properties,
    }
