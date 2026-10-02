"""Pure timezone recurrence policy used by previews and execution alike.

Nonexistent local times are skipped. Ambiguous local times use fold=0 once;
run identity is the resulting UTC timestamp, never the local clock string.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def instant(value):
    if not isinstance(value, str): raise ValueError('Provide an ISO date and time with a UTC offset')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None: raise ValueError('The date and time needs an explicit UTC offset')
    return result.timestamp()


def normalize(spec):
    if not isinstance(spec, dict): raise ValueError('A schedule specification is required')
    kind = spec.get('kind')
    if kind not in {'once', 'interval', 'daily', 'weekly'}: raise ValueError('Choose once, interval, daily, or weekly')
    timezone_name = spec.get('timezone')
    if not isinstance(timezone_name, str): raise ValueError('Choose an IANA timezone')
    try: ZoneInfo(timezone_name)
    except (KeyError, ValueError): raise ValueError('Choose a recognized IANA timezone') from None
    result = {'kind': kind, 'timezone': timezone_name, 'fold': 'first', 'nonexistent': 'skip'}
    if kind in {'once', 'interval'}:
        start = instant(spec['startAt']) if spec.get('startAt') else local_occurrence(normalize({'kind': 'daily', 'timezone': timezone_name, 'localTime': spec.get('localTime'), 'startDate': spec.get('startDate')}), datetime.strptime(spec['startDate'], '%Y-%m-%d').date())
        if start is None: raise ValueError('That local clock time does not exist; choose another time')
        result['startAt'] = datetime.fromtimestamp(start, UTC).isoformat()
        if kind == 'interval':
            seconds = spec.get('intervalSeconds')
            if type(seconds) is not int or not 60 <= seconds <= 366 * 86400: raise ValueError('Intervals must be whole seconds between one minute and one year')
            result['intervalSeconds'] = seconds
    else:
        try:
            hour, minute = spec['localTime'].split(':')
            parsed = datetime.strptime(spec['startDate'], '%Y-%m-%d').date()
            hour, minute = int(hour), int(minute)
            if not 0 <= hour < 24 or not 0 <= minute < 60: raise ValueError()
        except (ValueError, KeyError, AttributeError): raise ValueError('Provide a local time HH:MM and start date YYYY-MM-DD') from None
        result.update(localTime=f'{hour:02}:{minute:02}', startDate=parsed.isoformat())
        if kind == 'weekly':
            days = spec.get('weekdays')
            if not isinstance(days, list) or not days or any(type(day) is not int or not 0 <= day <= 6 for day in days): raise ValueError('Choose weekdays from 0 (Monday) to 6 (Sunday)')
            result['weekdays'] = sorted(set(days))
    return result


def local_occurrence(spec, date):
    if date.isoformat() < spec['startDate'] or (spec['kind'] == 'weekly' and date.weekday() not in spec['weekdays']): return None
    hour, minute = map(int, spec['localTime'].split(':'))
    naive = datetime(date.year, date.month, date.day, hour, minute)
    zone = ZoneInfo(spec['timezone'])
    candidate = naive.replace(tzinfo=zone, fold=0)
    if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive: return None
    return candidate.timestamp()


def next_after(spec, after):
    kind = spec['kind']
    if kind in {'once', 'interval'}:
        start = instant(spec['startAt'])
        if start > after: return start
        if kind == 'once': return None
        return start + (int((after - start) // spec['intervalSeconds']) + 1) * spec['intervalSeconds']
    first = max(datetime.fromtimestamp(after, ZoneInfo(spec['timezone'])).date(), datetime.fromisoformat(spec['startDate']).date())
    for offset in range(370):
        value = local_occurrence(spec, first + timedelta(days=offset))
        if value is not None and value > after: return value
    raise ValueError('No compatible local occurrence within one year')


def latest_before(spec, now):
    if spec['kind'] in {'once', 'interval'}:
        start = instant(spec['startAt'])
        if now < start: return None
        return start if spec['kind'] == 'once' else start + int((now - start) // spec['intervalSeconds']) * spec['intervalSeconds']
    day = datetime.fromtimestamp(now, ZoneInfo(spec['timezone'])).date()
    for offset in range(15):
        value = local_occurrence(spec, day - timedelta(days=offset))
        if value is not None and value <= now: return value
    return None


def preview(spec, after, count=5):
    result = []
    for _ in range(count):
        value = next_after(spec, after)
        if value is None: break
        result.append({'dueAt': value, 'utc': datetime.fromtimestamp(value, UTC).isoformat(), 'local': datetime.fromtimestamp(value, ZoneInfo(spec['timezone'])).isoformat()})
        after = value
    return result


def due_occurrence(spec, next_due, now, missed, grace=60):
    if next_due is None or now < next_due: return None
    if missed not in {'skip', 'latest'}: raise ValueError('Choose skip or latest for missed runs')
    if missed == 'skip' and now - next_due > grace:
        return {'dueAt': next_due, 'nextDue': next_after(spec, now), 'skip': True}
    selected = max(next_due, latest_before(spec, now) or next_due)
    return {'dueAt': selected, 'nextDue': next_after(spec, selected), 'skip': False}
