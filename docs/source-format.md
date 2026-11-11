# 日历字段约定

日期时间格式为YYYYMMDDTHHMMSS及可选Z，不接受DATE、浮动时间、自定义VTIMEZONE或闰秒。SEQUENCE缺省零，DTSTAMP必须为UTC。DURATION支持天、时、分、秒的非负整数ISO形式；DTEND与DURATION不能同时出现，活动必须有有限正时长。星期参数仅周规则可用，不带序数，周从星期一开始。UNTIL含边界，带TZID起点的UNTIL必须为UTC。COUNT和UNTIL不同时使用。缺失RRULE表示一次主活动，RDATE/EXDATE仍适用；RDATE和EXDATE的值类型/时区须与DTSTART一致。原序列候选先产生并计COUNT，跳时空档再剔除。重复区间的时长取主活动DTEND相对DTSTART的墙钟差或DURATION的精确时长，所有示例用DURATION。覆盖需完整DTSTART与结束描述，不继承旧时间字段。

实例逻辑键由UID和原序列开始值组成。重复本地时间选较早UTC，空档跳过。同排序修订比较展开前的语义属性，忽略属性顺序与折行。主事件取消后整组不输出。非取消孤立RECURRENCE-ID若无对应主事件视为UID错误。失败按UID隔离，不影响无关活动。资料服务只发送ICS原始字节。

## 采集组件行为（workbench.ical）

处理顺序固定为：组件级子集校验 → 同UID+RECURRENCE-ID修订按SEQUENCE再按DTSTAMP选最高排序位置（同位置语义冲突则隔离，完全重复视为同一份）→ 主事件生成原重复序列（COUNT按原序列计数）→ 剔除夏令时空档 → 合并RDATE、应用EXDATE → 应用RECURRENCE-ID覆盖 → 按实例区间与时间窗的非空交集选取（DTEND不含端点）。

失败按UID隔离，错误码包括：`UNSUPPORTED_RRULE`、`UNSUPPORTED_RANGE`、`UNSUPPORTED_VALUE`、`FLOATING_TIME`、`BAD_TZID`、`BAD_DATETIME`、`BAD_DURATION`、`BAD_RRULE`、`BAD_UNTIL`、`BAD_DTSTAMP`、`BAD_STATUS`、`BAD_SEQUENCE`、`BAD_PARAM`、`LEAP_SECOND`、`GAP_DATETIME`、`VALUE_KIND_MISMATCH`、`MISSING_UID`、`MISSING_PROPERTY`、`MISSING_DURATION`、`NONPOSITIVE_DURATION`、`DUPLICATE_PROPERTY`、`NESTED_COMPONENT`、`OVERRIDE_NOT_ATOMIC`、`ORPHAN_RECURRENCE_ID`、`REVISION_CONFLICT`。词法结构错误（折行、编码、组件嵌套）不属于任何UID，抛出`IcalParseError`。
