"""请求层与端到端管线核对，使用本地合成资料服务。"""
import json
import unittest
import urllib.request
from pathlib import Path

from source_lab.http import start
from tests.ical_helpers import utc
from workbench.ical import (collect, expand_window, fetch_calendar,
                            parse_calendar)
from workbench.ical.errors import IcalParseError
from workbench.raw_store import save_bytes, read_bytes

SOURCES = Path(__file__).resolve().parent.parent / 'sources'


class HttpPipelineTests(unittest.TestCase):
    def setUp(self):
        self.server = start(SOURCES / 'normal.json')
        self.url = f'http://127.0.0.1:{self.server.server_port}/calendar.ics'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_three_layers_callable_separately(self):
        raw = fetch_calendar(self.url)
        self.assertEqual(raw, (SOURCES / 'calendar.ics').read_bytes())
        parsed = parse_calendar(raw, source=self.url)
        result = expand_window(parsed, utc(2026, 11, 1), utc(2026, 12, 1))
        self.assertEqual(result.failures, [])
        self.assertEqual(len(result.instances), 3)

    def test_one_shot_collect_matches_layered(self):
        result = collect(self.url, utc(2026, 11, 1), utc(2026, 12, 1))
        self.assertEqual({i.uid for i in result.instances},
                         {'weekly-a', 'one-b'})

    def test_raw_store_handoff_between_request_and_parse(self):
        import tempfile
        raw = fetch_calendar(self.url)
        with tempfile.TemporaryDirectory() as d:
            key = save_bytes(d, raw)
            again = read_bytes(d, key)
        parsed = parse_calendar(again, source='stored.ics')
        result = expand_window(parsed, utc(2026, 11, 1), utc(2026, 12, 1))
        self.assertEqual(len(result.instances), 3)

    def test_http_error_surfaces_as_parse_error(self):
        with self.assertRaises(IcalParseError):
            fetch_calendar(
                f'http://127.0.0.1:{self.server.server_port}/missing.ics')

    def test_bad_url_scheme_surfaces_as_parse_error(self):
        with self.assertRaises(IcalParseError):
            fetch_calendar('not-a-url', timeout=2)

    def test_unreachable_host_surfaces_as_parse_error(self):
        with self.assertRaises(IcalParseError):
            fetch_calendar('http://127.0.0.1:9/calendar.ics', timeout=2)

    def test_bad_content_type_rejected(self):
        scene = {
            'routes': [{
                'path': '/calendar.ics',
                'responses': [{
                    'status': 200,
                    'headers': [['Content-Type', 'application/json']],
                    'text': 'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n',
                }],
            }],
        }
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            custom = Path(d) / 'scene.json'
            custom.write_text(json.dumps(scene), encoding='utf8')
            server = start(custom)
            try:
                with self.assertRaises(IcalParseError):
                    fetch_calendar(
                        f'http://127.0.0.1:{server.server_port}/calendar.ics')
            finally:
                server.shutdown()
                server.server_close()

    def test_request_is_recorded_by_source_service(self):
        fetch_calendar(self.url)
        self.assertEqual(self.server.events[-1]['path'], '/calendar.ics')
        self.assertEqual(self.server.events[-1]['method'], 'GET')


if __name__ == '__main__':
    unittest.main()
