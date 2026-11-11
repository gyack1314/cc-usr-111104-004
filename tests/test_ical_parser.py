"""折行、转义、属性参数与组件顺序的词法核对。"""
import unittest

from workbench.ical import parse_calendar, unfold_lines
from workbench.ical.errors import IcalParseError
from tests.ical_helpers import calendar, parse, vevent


class UnfoldTests(unittest.TestCase):
    def test_crlf_and_lf_equivalent(self):
        crlf = b'BEGIN:VCALENDAR\r\nUID:x\r\nEND:VCALENDAR\r\n'
        lf = b'BEGIN:VCALENDAR\nUID:x\nEND:VCALENDAR\n'
        self.assertEqual(unfold_lines(crlf), unfold_lines(lf))

    def test_folded_line_merges_space_and_tab(self):
        raw = b'SUMMARY:aaa\r\n bbb\r\n\tccc\n'
        lines = unfold_lines(raw)
        self.assertEqual(lines, [((1, 2, 3), 'SUMMARY:aaabbbccc')])

    def test_leading_continuation_rejected(self):
        with self.assertRaises(IcalParseError):
            unfold_lines(b'  orphan\n')

    def test_non_utf8_rejected(self):
        with self.assertRaises(IcalParseError):
            parse_calendar(b'\xff\xfe\x00')


class ParseTests(unittest.TestCase):
    def test_quoted_param_with_semicolon(self):
        parsed = parse(vevent(['DTSTART:20261101T000000Z',
                               'DURATION:PT1H',
                               'DTSTAMP:20260101T000000Z',
                               'X-LABEL;P1="a;b":v']))
        line = parsed.events[0].first('X-LABEL')
        self.assertEqual(line.params, {'P1': 'a;b'})

    def test_text_unescape(self):
        parsed = parse(vevent(['DTSTART:20261101T000000Z',
                               'DURATION:PT1H',
                               'DTSTAMP:20260101T000000Z',
                               r'SUMMARY:a\,b\;c\n d\\e']))
        self.assertEqual(parsed.events[0].first('SUMMARY').decode_text(),
                         'a,b;c\n d\\e')

    def test_folded_summary_physical_lines(self):
        event = ('BEGIN:VEVENT\nUID:e1\nDTSTART:20261101T000000Z\n'
                 'DURATION:PT1H\nDTSTAMP:20260101T000000Z\n'
                 'SUMMARY:长标题\n 续行\nEND:VEVENT')
        parsed = parse(calendar(event))
        line = parsed.events[0].first('SUMMARY')
        self.assertEqual(line.value, '长标题续行')
        self.assertEqual(line.physical_lines, (9, 10))

    def test_component_order_does_not_change_grouping(self):
        body = (['DTSTART:20261101T000000Z', 'DURATION:PT1H',
                 'DTSTAMP:20260101T000000Z'])
        a = parse(calendar(vevent(body, 'u1'), vevent(body, 'u2')))
        b = parse(calendar(vevent(body, 'u2'), vevent(body, 'u1')))
        self.assertEqual(sorted(a.grouped()), sorted(b.grouped()))

    def test_unmatched_end_rejected(self):
        with self.assertRaises(IcalParseError):
            parse_calendar(b'BEGIN:VCALENDAR\nEND:VEVENT\n')

    def test_empty_value_line_rejected(self):
        with self.assertRaises(IcalParseError):
            parse_calendar(b'BEGIN:VEVENT\n\nEND:VEVENT\n')

    def test_duplicate_param_rejected(self):
        with self.assertRaises(IcalParseError):
            parse(vevent(['DTSTART;TZID=A;TZID=B:20261101T000000',
                          'DURATION:PT1H', 'DTSTAMP:20260101T000000Z']))


if __name__ == '__main__':
    unittest.main()
