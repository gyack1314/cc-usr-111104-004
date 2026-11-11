"""HTTP 采集层：只负责取回 .ics 原始字节，不解释日历语义。

使用标准库 urllib，默认超时与允许的 Content-Type 都可配置；返回原始字节后，
调用方可以交给 :func:`workbench.ical.parser.parse_calendar`，也可以先经
:mod:`workbench.raw_store` 落盘留痕。请求、解析、展开因此可以分别调用。
"""
import urllib.error
import urllib.request

from .errors import IcalParseError

DEFAULT_TIMEOUT = 10
_ACCEPTED_TYPES = ('text/calendar', 'application/ics', 'text/plain')


def fetch(url, *, timeout=DEFAULT_TIMEOUT, headers=None,
          accept_content_types=_ACCEPTED_TYPES):
    """发起一次 HTTP(S) GET，返回日历原始字节。

    非 2xx 状态、无法解码的分块或声明的 Content-Type 明显不是日历都会报错；
    缺少 Content-Type 时不拦截，交给解析层判定。
    """
    try:
        request = urllib.request.Request(
            url, headers={'Accept': 'text/calendar', **(headers or {})})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get('Content-Type', '')
            mime = content_type.split(';', 1)[0].strip().lower()
            if mime and accept_content_types and mime not in accept_content_types:
                raise IcalParseError(
                    f'响应 Content-Type 不是日历类型: {content_type}')
            return response.read()
    except IcalParseError:
        raise
    except urllib.error.HTTPError as exc:
        raise IcalParseError(f'HTTP {exc.code} 获取日历失败: {url}') from exc
    except (urllib.error.URLError, ValueError) as exc:
        raise IcalParseError(f'无法获取日历来源 {url}: {exc}') from exc
