"""命令行核对工具：对 URL 或本地 .ics 文件执行一次时间窗采集并打印 JSON。

时间参数使用带 Z 的 UTC ISO 形式，例如：
python -m workbench.ical.cli http://127.0.0.1:8765/calendar.ics \
    2026-11-01T00:00:00Z 2026-12-01T00:00:00Z
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from . import expand_window, fetch_calendar, parse_calendar


def _parse_iso(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError('时间必须带时区，例如 ...Z')
    return dt


def main(argv=None):
    parser = argparse.ArgumentParser(description='iCalendar 时间窗采集核对')
    parser.add_argument('source', help='http(s) URL 或本地 .ics 文件路径')
    parser.add_argument('window_start', type=_parse_iso)
    parser.add_argument('window_end', type=_parse_iso)
    args = parser.parse_args(argv)

    if args.source.startswith(('http://', 'https://')):
        raw = fetch_calendar(args.source)
        source = args.source
    else:
        raw = Path(args.source).read_bytes()
        source = Path(args.source).name
    parsed = parse_calendar(raw, source=source)
    result = expand_window(parsed, args.window_start, args.window_end)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 1 if result.failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
