from calendar import monthrange
from datetime import date,datetime,timezone,timedelta


def business_today():
    return datetime.now(timezone(timedelta(hours=-4))).date()


def next_billing_date(start: date,anchor_day: int):
    if not 1<=anchor_day<=31:raise ValueError('Ancla mensual inválida')
    year=start.year+(start.month//12);month=start.month%12+1
    return date(year,month,min(anchor_day,monthrange(year,month)[1]))
