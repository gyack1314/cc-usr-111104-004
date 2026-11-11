"""版本选择、覆盖/取消、UID 隔离与支持子集拒绝的采集核对。"""
import unittest
from pathlib import Path

from tests.ical_helpers import NY, calendar, collect, parse, utc, vevent

SOURCES = Path(__file__).resolve().parent.parent / 'sources'
NOV = (utc(2026, 11, 1), utc(2026, 12, 1))


def collect_file(name, start=NOV[0], end=NOV[1]):
    return collect((SOURCES / name).read_bytes(), start, end, source=name)


class FixtureTests(unittest.TestCase):
    def test_calendar_fixture_override_and_exdate(self):
        result = collect_file('calendar.ics')
        self.assertEqual(result.failures, [])
        rows = [(i.uid, i.start_utc.isoformat(), i.origin)
                for i in result.instances]
        self.assertEqual(rows, [
            ('one-b', '2026-11-01T00:00:00+00:00', 'master'),
            ('weekly-a', '2026-11-02T15:00:00+00:00', 'override'),
            ('weekly-a', '2026-11-15T14:00:00+00:00', 'master'),
        ])
        override = next(i for i in result.instances if i.origin == 'override')
        # 身份保持原 RECURRENCE-ID（11-01 09:00 纽约），不按新开始另建
        self.assertEqual(override.identity,
                         ('L', NY, '2026-11-01T09:00:00'))
        self.assertEqual(override.duration.total_seconds(), 1800)
        self.assertEqual(override.summary, '改期答辩')

    def test_moved_override_replaces_old_slot_even_outside_window(self):
        # 窗口只覆盖旧场次 11-01，改期到 11-02 后窗口内不应残留旧实例，
        # 也不应按新开始时间在窗口内另建一条
        text = calendar(
            vevent(['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                    'DTSTAMP:20261001T000000Z'], 'm'),
            vevent(['RECURRENCE-ID:20261101T000000Z',
                    'DTSTART:20261103T000000Z', 'DURATION:PT1H',
                    'DTSTAMP:20261020T000000Z', 'SEQUENCE:2'], 'm'))
        old_window = collect(text, utc(2026, 10, 31), utc(2026, 11, 2))
        self.assertEqual(old_window.instances, [])
        wide = collect(text, utc(2026, 10, 31), utc(2026, 11, 4))
        self.assertEqual(len(wide.instances), 1)
        self.assertEqual(wide.instances[0].identity,
                         ('U', None, '2026-11-01T00:00:00'))
        self.assertTrue(wide.instances[0].moved)

    def test_cancelled_override_removes_instance(self):
        result = collect_file('cancel.ics')
        self.assertEqual(result.failures, [])
        uids = [(i.uid, i.origin) for i in result.instances]
        self.assertNotIn(('weekly-a', 'override'), uids)
        # 10-25 在窗外、11-01 被取消、11-08 被 EXDATE，窗内只剩 11-15
        self.assertEqual(
            [i.start_utc.day for i in result.instances
             if i.uid == 'weekly-a'], [15])
        self.assertTrue(any(i.uid == 'one-b' for i in result.instances))

    def test_same_rank_conflict_isolates_uid_only(self):
        result = collect_file('conflict.ics')
        self.assertEqual([f.uid for f in result.failures], ['one-b'])
        self.assertEqual(result.failures[0].code, 'REVISION_CONFLICT')
        # 无关 UID 不受影响
        self.assertEqual({i.uid for i in result.instances}, {'weekly-a'})

    def test_spring_fixture_gap_plus_unmatched_override(self):
        result = collect_file('spring.ics',
                              utc(2026, 2, 1), utc(2026, 4, 1))
        # 覆盖指向原序列不存在的 11-01，按 UID 失败隔离：整组不输出，
        # 失败可回溯到覆盖组件所在行
        self.assertEqual([f.code for f in result.failures],
                         ['ORPHAN_RECURRENCE_ID'])
        self.assertEqual(result.failures[0].uid, 'weekly-a')
        self.assertEqual(result.instances, [])

    def test_spring_gap_series_without_bad_override(self):
        # 去掉孤立覆盖后，同一主序列可干净验证 DST：03-08 空档跳过且计入 COUNT
        text = calendar(vevent([
            f'DTSTART;TZID={NY}:20260301T023000',
            'DURATION:PT1H',
            'RRULE:FREQ=WEEKLY;COUNT=3;BYDAY=SU',
            'DTSTAMP:20260101T000000Z'], 'weekly-a'))
        result = collect(text, utc(2026, 2, 1), utc(2026, 4, 1))
        self.assertEqual(result.failures, [])
        self.assertEqual(
            [i.start_utc for i in result.instances],
            [utc(2026, 3, 1, 7, 30), utc(2026, 3, 15, 6, 30)])
        walls = [i.start.replace(tzinfo=None).isoformat()
                 for i in result.instances]
        self.assertEqual(walls, ['2026-03-01T02:30:00',
                                 '2026-03-15T02:30:00'])

    def test_master_cancelled_kills_whole_group(self):
        text = calendar(
            vevent(['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                    'RRULE:FREQ=DAILY;COUNT=3', 'STATUS:CANCELLED',
                    'DTSTAMP:20261001T000000Z', 'SEQUENCE:3'], 'g'),
            vevent(['RECURRENCE-ID:20261102T000000Z',
                    'DTSTART:20261102T000000Z', 'DURATION:PT1H',
                    'DTSTAMP:20261020T000000Z'], 'g'))
        result = collect(text, *NOV)
        self.assertEqual(result.instances, [])
        self.assertEqual(result.failures, [])

    def test_orphan_override_without_master_is_uid_error(self):
        text = calendar(vevent(['RECURRENCE-ID:20261101T000000Z',
                                'DTSTART:20261101T000000Z',
                                'DURATION:PT1H',
                                'DTSTAMP:20261001T000000Z'], 'lonely'))
        result = collect(text, *NOV)
        self.assertEqual([f.code for f in result.failures],
                         ['ORPHAN_RECURRENCE_ID'])
        self.assertEqual(result.instances, [])

    def test_non_recurring_master_rescheduled_across_window(self):
        # 单次活动（无 RRULE）的唯一实例被改期：覆盖身份等于主事件起点，
        # 旧窗口清空，新窗口内以原身份出现一条 moved 实例
        text = calendar(
            vevent(['DTSTART:20261101T090000Z', 'DURATION:PT1H',
                    'DTSTAMP:20261001T000000Z', 'SEQUENCE:1'], 'defense'),
            vevent(['RECURRENCE-ID:20261101T090000Z',
                    'DTSTART:20261103T140000Z', 'DURATION:PT45M',
                    'DTSTAMP:20261025T000000Z', 'SEQUENCE:2',
                    'SUMMARY:答辩改期'], 'defense'))
        old = collect(text, utc(2026, 11, 1), utc(2026, 11, 2))
        self.assertEqual(old.instances, [])
        wide = collect(text, utc(2026, 11, 1), utc(2026, 11, 4))
        self.assertEqual(len(wide.instances), 1)
        inst = wide.instances[0]
        self.assertEqual(inst.identity,
                         ('U', None, '2026-11-01T09:00:00'))
        self.assertEqual(inst.start_utc, utc(2026, 11, 3, 14))
        self.assertEqual(inst.duration.total_seconds(), 2700)
        self.assertTrue(inst.moved)


class RevisionSelectionTests(unittest.TestCase):
    def _two_revisions(self, seq1, stamp1, seq2, stamp2, order='forward'):
        first = vevent(['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                        f'DTSTAMP:{stamp1}', f'SEQUENCE:{seq1}',
                        'SUMMARY:旧'], 'v')
        second = vevent(['DTSTART:20261102T000000Z', 'DURATION:PT1H',
                         f'DTSTAMP:{stamp2}', f'SEQUENCE:{seq2}',
                         'SUMMARY:新'], 'v')
        events = (first, second) if order == 'forward' else (second, first)
        return calendar(*events)

    def test_higher_sequence_wins_regardless_of_order(self):
        for order in ('forward', 'reverse'):
            text = self._two_revisions(1, '20261001T000000Z',
                                       2, '20260901T000000Z', order)
            result = collect(text, *NOV)
            self.assertEqual(len(result.instances), 1)
            self.assertEqual(result.instances[0].start_utc.day, 2)

    def test_dtstamp_breaks_sequence_tie(self):
        text = self._two_revisions(2, '20261001T000000Z',
                                   2, '20261002T000000Z')
        result = collect(text, *NOV)
        self.assertEqual(result.instances[0].summary, '新')

    def test_identical_duplicates_are_merged(self):
        comp = vevent(['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                       'DTSTAMP:20261001T000000Z', 'SEQUENCE:2'], 'd')
        result = collect(calendar(comp, comp), *NOV)
        self.assertEqual(result.failures, [])
        self.assertEqual(len(result.instances), 1)

    def test_duplicated_weekly_event_is_deduplicated(self):
        # 助教把同一条周重复实训抄了两遍：同 UID、同 SEQUENCE/DTSTAMP、
        # 同内容，必须只产生一份实例序列
        body = ['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                'RRULE:FREQ=WEEKLY;COUNT=3',
                'DTSTAMP:20261001T000000Z', 'SEQUENCE:1']
        comp = vevent(body, 'lab')
        result = collect(calendar(comp, comp), *NOV)
        self.assertEqual(result.failures, [])
        self.assertEqual([i.start_utc.day for i in result.instances],
                         [1, 8, 15])


class RejectionTests(unittest.TestCase):
    def assert_rejected(self, lines, code, uid='bad'):
        result = collect(calendar(vevent(lines, uid)), *NOV)
        self.assertEqual([f.code for f in result.failures], [code])
        self.assertEqual(result.instances, [])

    def test_monthly_rrule_rejected(self):
        self.assert_rejected(['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                              'RRULE:FREQ=MONTHLY;COUNT=2',
                              'DTSTAMP:20260101T000000Z'],
                             'UNSUPPORTED_RRULE')

    def test_unsupported_rrule_part_rejected(self):
        self.assert_rejected(['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                              'RRULE:FREQ=WEEKLY;BYSETPOS=1',
                              'DTSTAMP:20260101T000000Z'],
                             'UNSUPPORTED_RRULE')

    def test_thisandfuture_rejected(self):
        self.assert_rejected(
            ['DTSTART:20261101T000000Z', 'DURATION:PT1H',
             'RECURRENCE-ID;RANGE=THISANDFUTURE:20261101T000000Z',
             'DTSTAMP:20260101T000000Z'], 'UNSUPPORTED_RANGE')

    def test_floating_time_rejected(self):
        self.assert_rejected(['DTSTART:20261101T000000', 'DURATION:PT1H',
                              'DTSTAMP:20260101T000000Z'], 'FLOATING_TIME')

    def test_date_value_rejected(self):
        self.assert_rejected(['DTSTART;VALUE=DATE:20261101',
                              'DURATION:PT1H',
                              'DTSTAMP:20260101T000000Z'],
                             'UNSUPPORTED_VALUE')

    def test_leap_second_rejected(self):
        self.assert_rejected(['DTSTART:20161231T235960Z', 'DURATION:PT1H',
                              'DTSTAMP:20260101T000000Z'], 'LEAP_SECOND')

    def test_count_and_until_together_rejected(self):
        self.assert_rejected(
            ['DTSTART:20261101T000000Z', 'DURATION:PT1H',
             'RRULE:FREQ=DAILY;COUNT=2;UNTIL=20261105T000000Z',
             'DTSTAMP:20260101T000000Z'], 'BAD_RRULE')

    def test_local_until_must_be_utc(self):
        self.assert_rejected(
            [f'DTSTART;TZID={NY}:20261101T090000', 'DURATION:PT1H',
             f'RRULE:FREQ=DAILY;UNTIL=20261105T090000',
             'DTSTAMP:20260101T000000Z'], 'BAD_UNTIL')

    def test_week_duration_rejected(self):
        self.assert_rejected(['DTSTART:20261101T000000Z', 'DURATION:P1W',
                              'DTSTAMP:20260101T000000Z'], 'BAD_DURATION')

    def test_dtend_and_duration_together_rejected(self):
        self.assert_rejected(['DTSTART:20261101T000000Z',
                              'DTEND:20261101T010000Z', 'DURATION:PT1H',
                              'DTSTAMP:20260101T000000Z'],
                             'BAD_TIME_RANGE')

    def test_unknown_tzid_rejected(self):
        self.assert_rejected(['DTSTART;TZID=Mars/Olympus:20261101T000000',
                              'DURATION:PT1H',
                              'DTSTAMP:20260101T000000Z'], 'BAD_TZID')

    def test_dtstamp_must_be_utc(self):
        self.assert_rejected(
            [f'DTSTART;TZID={NY}:20261101T090000', 'DURATION:PT1H',
             f'DTSTAMP;TZID={NY}:20261001T000000'], 'BAD_DTSTAMP')

    def test_byday_ordinal_rejected(self):
        self.assert_rejected(
            ['DTSTART:20261101T000000Z', 'DURATION:PT1H',
             'RRULE:FREQ=WEEKLY;BYDAY=2MO',
             'DTSTAMP:20260101T000000Z'], 'UNSUPPORTED_RRULE')

    def test_explicit_gap_datetime_rejected(self):
        self.assert_rejected(
            [f'DTSTART;TZID={NY}:20260301T023000', 'DURATION:PT1H',
             f'RDATE;TZID={NY}:20260308T023000',
             'DTSTAMP:20260101T000000Z'], 'GAP_DATETIME')

    def test_value_kind_mismatch_rejected(self):
        self.assert_rejected(
            [f'DTSTART;TZID={NY}:20261101T090000', 'DURATION:PT1H',
             'EXDATE:20261101T090000Z',
             'DTSTAMP:20260101T000000Z'], 'VALUE_KIND_MISMATCH')

    def test_uid_failure_isolates_but_others_survive(self):
        good = vevent(['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                       'DTSTAMP:20260101T000000Z'], 'good')
        bad = vevent(['DTSTART:20261101T000000', 'DURATION:PT1H',
                      'DTSTAMP:20260101T000000Z'], 'bad')
        result = collect(calendar(good, bad), *NOV)
        self.assertEqual([f.uid for f in result.failures], ['bad'])
        self.assertEqual([i.uid for i in result.instances], ['good'])


class ProvenanceTests(unittest.TestCase):
    def test_instance_export_traces_original_lines(self):
        text = ('BEGIN:VCALENDAR\nBEGIN:VEVENT\n'
                'UID:trace\nDTSTAMP:20260101T000000Z\n'
                'DTSTART:20261101T000000Z\nDURATION:PT1H\n'
                'SUMMARY:可\n 追溯\nEND:VEVENT\nEND:VCALENDAR\n')
        result = collect(text, *NOV, source='trace.ics')
        data = result.instances[0].to_dict()
        prov = data['provenance']
        prov = data['provenance']
        self.assertEqual(prov['component_span'], [2, 9])
        self.assertEqual(prov['properties']['SUMMARY'][0]['physical_lines'],
                         [7, 8])
        self.assertEqual(data['source'], 'trace.ics')
        self.assertEqual(data['recurrence_id'],
                         {'kind': 'utc', 'tzid': None,
                          'value': '2026-11-01T00:00:00'})

    def test_failure_report_carries_lines(self):
        text = calendar(vevent(['DTSTART:20261101T000000', 'DURATION:PT1H',
                                'DTSTAMP:20260101T000000Z'], 'x'))
        result = collect(text, *NOV, source='x.ics')
        report = result.failures[0].to_dict()
        self.assertEqual(report['uid'], 'x')
        self.assertTrue(report['lines'])
        self.assertEqual(report['source'], 'x.ics')


class InvarianceTests(unittest.TestCase):
    def test_crlf_folding_and_order_share_semantics(self):
        base = calendar(
            vevent(['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                    'DTSTAMP:20260101T000000Z', 'SUMMARY:abc'], 'a'),
            vevent(['DTSTART:20261102T000000Z', 'DURATION:PT1H',
                    'DTSTAMP:20260101T000000Z'], 'b'))
        reordered = calendar(
            vevent(['SUMMARY:abc', 'DTSTAMP:20260101T000000Z',
                    'DURATION:PT1H', 'DTSTART:20261101T000000Z'], 'a'),
            vevent(['DTSTART:20261102T000000Z', 'DURATION:PT1H',
                    'DTSTAMP:20260101T000000Z'], 'b'))
        crlf = reordered.replace('\n', '\r\n').encode()
        folded = crlf.replace(b'SUMMARY:abc', b'SUMMARY:ab\r\n c')
        r1 = collect(base, *NOV)
        r2 = collect(folded, *NOV)
        self.assertEqual([i.to_dict()['start'] for i in r1.instances],
                         [i.to_dict()['start'] for i in r2.instances])


if __name__ == '__main__':
    unittest.main()
