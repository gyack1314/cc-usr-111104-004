# 实操与答辩日历的例外实例资料

此项目维护本地合成来源、协议资料及原始字节工具，用于复查机器人赛事与课程资料。所有名称、数据和地址都不代表真实组织或设备。

`source_lab` 提供资料服务，`sources` 保存业务样本，`workbench.raw_store` 按摘要存取原始字节，`workbench.ical` 是 iCalendar 采集组件。HTTP来源的每条路由可依次返回多个响应，最后一个响应会重复；服务仅绑定环回地址。详细数据格式见 [资料约定](docs/source-format.md)。

## iCalendar 采集组件

`workbench.ical` 只把指定时间窗内实际有效的活动实例交给后续流程，不做排班或提醒。请求、解析、展开三层可分别调用，也可一步到位：

```python
from datetime import datetime, timezone
from workbench.ical import fetch_calendar, parse_calendar, expand_window, collect

window = (datetime(2026, 11, 1, tzinfo=timezone.utc),
          datetime(2026, 12, 1, tzinfo=timezone.utc))
raw = fetch_calendar(url)                  # 第一层：HTTP 取回 .ics 原始字节
parsed = parse_calendar(raw, source=url)   # 第二层：CRLF 折行、转义、参数、VEVENT
result = expand_window(parsed, *window)    # 第三层：版本选择、例外、时间窗展开
result = collect(url, *window)             # 或三层合一
```

`result.instances` 是窗内有效实例（按 UTC 开始时间排序），`result.failures` 是 UID 级失败报告；两者都带可回溯到原始属性行的物理行号，换行方式（CRLF/LF）、折行与组件顺序变化不改变语义。支持的明确子集：UTC 日期时间或带 IANA TZID 的本地日期时间、DTSTART 配 DTEND 或 DURATION、RRULE 的 DAILY/WEEKLY 及 INTERVAL/COUNT/UNTIL/BYDAY、RDATE/EXDATE、RECURRENCE-ID 单实例覆盖与 STATUS:CANCELLED。不支持的规则与 RANGE=THISANDFUTURE 直接拒绝该 UID 并隔离，不悄悄当作一次活动。

关键语义：DTEND 不含端点，时间窗按非空交集选取；COUNT 统计原重复序列，不扣除 EXDATE 与夏令时空档；跨 DST 的周活动保持当地墙上时间，歧义本地时间选较早 UTC，落在跳时空档的实例跳过；覆盖以原 RECURRENCE-ID 为身份，移出窗口的改期/取消仍替换旧实例；主事件取消影响整组。

命令行核对（URL 或本地文件）：

```sh
python -m workbench.ical.cli sources/calendar.ics 2026-11-01T00:00:00Z 2026-12-01T00:00:00Z
```

使用 Python 3.11 或以上版本，无第三方Python依赖。在项目根目录执行：

```sh
python -m unittest discover -s tests -v
python -m compileall -q workbench source_lab tests
python -m workbench.cli inspect sources
```

启动本地HTTP资料服务：

```sh
python -m source_lab.http sources/normal.json --port 8765
```

容器方式可执行 `docker build -t resource-source .`，随后 `docker run --rm resource-source python -m unittest discover -s tests -v`。服务地址只对本次运行有效，客户端应从配置取得地址。输出资料写入单独目录，原始样本保持只读。
