import datetime

def main():
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    local_now = datetime.datetime.now().astimezone()
    tz_h = local_now.utcoffset().total_seconds() / 3600.0
    tz_n = local_now.tzname()
    print(utc_now.strftime('%Y-%m-%d %H:%M:%S UTC'))
    print('Local: ' + local_now.strftime('%Y-%m-%d %H:%M:%S') + ' (' + tz_n + ', offset ' + format(tz_h, '+.2f') + 'h)')

main()