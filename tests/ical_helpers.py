"""iCalendar 测试共用的日历构造辅助。"""
from datetime import datetime, timezone

from workbench.ical import parse_calendar, expand_window

NY = 'America/New_York'


def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second,
                    tzinfo=timezone.utc)


def parse(text, source='memo.ics'):
    if isinstance(text, str):
        text = text.encode('utf-8')
    return parse_calendar(text, source=source)


def collect(text, start, end, source='memo.ics'):
    return expand_window(parse(text, source), start, end)


def vevent(lines, uid='e1'):
    body = '\n'.join([f'UID:{uid}', *lines])
    return f'BEGIN:VEVENT\n{body}\nEND:VEVENT'


def calendar(*events):
    return ('BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Test//ZH\n'
            + '\n'.join(events) + '\nEND:VCALENDAR\n')


def identities(result):
    return sorted((inst.uid, inst.identity[1], inst.origin)
                  for inst in result.instances)


def failure_codes(result):
    return sorted(f.code for f in result.failures)
