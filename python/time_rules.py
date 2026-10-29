"""UTC storage, IANA trading sessions. Exits remain permitted outside entry hours."""
import re
import numpy as np
import pandas as pd


def validate(c):
    zone = c.setdefault('tradingTimezone', 'UTC')
    try: pd.Timestamp('2026-01-01', tz='UTC').tz_convert(zone)
    except Exception: raise ValueError('無效的 IANA 交易時區。')
    for key, default in [('sessionStart', '09:30'), ('sessionEnd', '16:00')]:
        value = c.setdefault(key, default)
        if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('交易時段須為 HH:MM。')
    days = c.setdefault('sessionDays', [0, 1, 2, 3, 4])
    if not isinstance(days, list) or not days or any(type(x) is not int or x not in range(7) for x in days):
        raise ValueError('交易日須為 0–6（週一至週日）。')
    if type(c.setdefault('sessionEnabled', False)) is not bool: raise ValueError('sessionEnabled 須為布林值。')
    if c['sessionEnabled'] and c.get('barHours', 24) >= 24: raise ValueError('盤中進場時段限制需使用盤中行情。')


def allowed(index, c):
    if not c.get('sessionEnabled', False): return np.ones(len(index), dtype=bool)
    local = index.tz_convert(c.get('tradingTimezone', 'UTC'))
    minute = local.hour*60+local.minute
    start, end = [sum(int(v)*m for v, m in zip(c[k].split(':'), [60, 1])) for k in ['sessionStart', 'sessionEnd']]
    # Overnight session belongs to the weekday on which the window starts.
    weekdays = (local.dayofweek-1)%7 if start > end else local.dayofweek
    if start > end:
        weekdays = np.where(minute < end, weekdays, local.dayofweek)
        window = (minute >= start) | (minute < end)
    elif start == end: window = np.ones(len(index), dtype=bool)
    else: window = (minute >= start) & (minute < end)
    return np.asarray(window & np.isin(weekdays, c.get('sessionDays', [0, 1, 2, 3, 4])))


def describe(c):
    return dict(timezone=c.get('tradingTimezone', 'UTC'), enabled=c.get('sessionEnabled', False), start=c.get('sessionStart', '09:30'), end=c.get('sessionEnd', '16:00'), weekdays=c.get('sessionDays', [0, 1, 2, 3, 4]), rule='New entries require BOTH decision-close and execution-open inside [start,end). Exits allowed at all hours; no session-end flatten. Overnight weekday=window start day. Daily statistics group by local BAR OPEN date; UTC ledger retained. DST uses IANA rules, not a fixed offset.')
