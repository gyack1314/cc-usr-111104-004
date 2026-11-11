"""重复序列、夏令时空档/歧义与 COUNT 语义核对。"""
import unittest
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from tests.ical_helpers import NY, calendar, collect, utc, vevent
from workbench.ical.timevalue import local_gap, to_utc


def failure_codes(result):
    return sorted(f.code for f in result.failures)


class DstTests(unittest.TestCase):
    def test_gap_detection_and_earlier_utc_for_ambiguous(self):
        ny = ZoneInfo(NY)
        gap = datetime(2026, 3, 8, 2, 30, tzinfo=ny)
        self.assertTrue(local_gap(gap))
        amb = datetime(2026, 11, 1, 1, 30, tzinfo=ny)
        self.assertFalse(local_gap(amb))
        self.assertEqual(to_utc(amb), utc(2026, 11, 1, 5, 30))

    def test_weekly_skips_spring_forward_gap_but_count_includes_it(self):
        # 2026-03-01 / 03-08(空档) / 03-15，COUNT=3 但只输出两例
        text = calendar(vevent([
            f'DTSTART;TZID={NY}:20260301T023000',
            'DURATION:PT1H',
            'RRULE:FREQ=WEEKLY;COUNT=3;BYDAY=SU',
            'DTSTAMP:20260101T000000Z'], 'spring'))
        result = collect(text, utc(2026, 2, 1), utc(2026, 4, 1))
        self.assertEqual(failure_codes(result), [])
        starts = [i.start_utc for i in result.instances]
        self.assertEqual(starts, [utc(2026, 3, 1, 7, 30),
                                  utc(2026, 3, 15, 6, 30)])
        # 当地墙上时间保持 02:30，DST 后 UTC 偏移从 -5 变 -4
        walls = [i.start.replace(tzinfo=None).isoformat()
                 for i in result.instances]
        self.assertEqual(walls, ['2026-03-01T02:30:00',
                                 '2026-03-15T02:30:00'])

    def test_fall_back_weekly_keeps_earlier_utc(self):
        text = calendar(vevent([
            f'DTSTART;TZID={NY}:20261025T013000',
            'DURATION:PT1H',
            'RRULE:FREQ=WEEKLY;COUNT=2;BYDAY=SU',
            'DTSTAMP:20260101T000000Z'], 'fall'))
        result = collect(text, utc(2026, 10, 1), utc(2026, 12, 1))
        self.assertEqual(failure_codes(result), [])
        second = result.instances[1]
        self.assertEqual(second.start_utc, utc(2026, 11, 1, 5, 30))
        self.assertEqual(second.duration, timedelta(hours=1))

    def test_exdate_does_not_reduce_count(self):
        # COUNT=4 原序列 4 个，EXDATE 去掉 11-01；若错误地只生成 3 个，
        # 最后一个 11-15 就会缺失
        text = calendar(vevent([
            f'DTSTART;TZID={NY}:20261025T090000',
            'DURATION:PT1H',
            'RRULE:FREQ=WEEKLY;COUNT=4;BYDAY=SU',
            f'EXDATE;TZID={NY}:20261101T090000',
            'DTSTAMP:20260101T000000Z'], 'cnt'))
        result = collect(text, utc(2026, 10, 1), utc(2026, 12, 1))
        days = sorted(i.start_utc.day for i in result.instances)
        self.assertEqual(days, [8, 15, 25])

    def test_until_inclusive_utc_boundary(self):
        text = calendar(vevent([
            'DTSTART:20261101T000000Z', 'DURATION:PT1H',
            'RRULE:FREQ=DAILY;INTERVAL=2;UNTIL=20261105T000000Z',
            'DTSTAMP:20260101T000000Z'], 'u'))
        result = collect(text, utc(2026, 10, 1), utc(2026, 12, 1))
        days = [i.start_utc.day for i in result.instances]
        self.assertEqual(days, [1, 3, 5])

    def test_daily_interval(self):
        text = calendar(vevent([
            'DTSTART:20261101T000000Z', 'DURATION:PT30M',
            'RRULE:FREQ=DAILY;INTERVAL=3;COUNT=3',
            'DTSTAMP:20260101T000000Z'], 'd'))
        result = collect(text, utc(2026, 10, 1), utc(2026, 12, 1))
        self.assertEqual([i.start_utc.day for i in result.instances],
                         [1, 4, 7])

    def test_rdate_adds_and_exdate_removes(self):
        text = calendar(vevent([
            'DTSTART:20261101T000000Z', 'DURATION:PT1H',
            'RDATE:20261103T000000Z,20261103T000000Z',
            'EXDATE:20261101T000000Z',
            'DTSTAMP:20260101T000000Z'], 'r'))
        result = collect(text, utc(2026, 10, 1), utc(2026, 12, 1))
        self.assertEqual([i.start_utc.day for i in result.instances], [3])
        self.assertEqual(result.instances[0].origin, 'rdate')

    def test_window_uses_nonempty_intersection(self):
        # 实例 00:00-01:00Z，窗口 00:30-02:00 有交集；窗口 01:00 起无交集
        text = calendar(vevent(['DTSTART:20261101T000000Z',
                                'DURATION:PT1H',
                                'DTSTAMP:20260101T000000Z'], 'w'))
        hit = collect(text, utc(2026, 11, 1, 0, 30), utc(2026, 11, 1, 2))
        miss = collect(text, utc(2026, 11, 1, 1), utc(2026, 11, 1, 2))
        self.assertEqual(len(hit.instances), 1)
        self.assertEqual(len(miss.instances), 0)

    def test_unbounded_rule_is_not_cut_off_for_far_window(self):
        text = calendar(vevent(['DTSTART:20260101T000000Z', 'DURATION:PT1H',
                                'RRULE:FREQ=DAILY',
                                'DTSTAMP:20260101T000000Z'], 'far'))
        result = collect(text, utc(2028, 6, 1), utc(2028, 6, 4))
        self.assertEqual(
            [i.start_utc.date().isoformat() for i in result.instances],
            ['2028-06-01', '2028-06-02', '2028-06-03'])

    def test_unbounded_override_outside_window_still_replaces_identity(self):
        text = calendar(
            vevent(['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                    'RRULE:FREQ=WEEKLY', 'DTSTAMP:20260101T000000Z'], 'm'),
            vevent(['RECURRENCE-ID:20261220T000000Z',
                    'DTSTART:20261110T000000Z', 'DURATION:PT1H',
                    'DTSTAMP:20261020T000000Z', 'SEQUENCE:2'], 'm'))
        result = collect(text, utc(2026, 11, 9), utc(2026, 11, 12))
        self.assertEqual(result.failures, [])
        self.assertEqual(len(result.instances), 1)
        inst = result.instances[0]
        self.assertEqual(inst.identity[2], '2026-12-20T00:00:00')
        self.assertEqual(inst.start_utc.date().isoformat(), '2026-11-10')
        self.assertTrue(inst.moved)

    def test_unbounded_cancelled_override_removes_far_slot(self):
        text = calendar(
            vevent(['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                    'RRULE:FREQ=WEEKLY', 'DTSTAMP:20260101T000000Z'], 'm'),
            vevent(['RECURRENCE-ID:20261220T000000Z', 'STATUS:CANCELLED',
                    'DTSTART:20261220T000000Z', 'DURATION:PT1H',
                    'DTSTAMP:20261020T000000Z', 'SEQUENCE:2'], 'm'))
        result = collect(text, utc(2026, 12, 19), utc(2026, 12, 22))
        self.assertEqual(
            [i.start_utc.date().isoformat() for i in result.instances], [])


if __name__ == '__main__':
    unittest.main()
