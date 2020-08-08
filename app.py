import signal
import sys
import threading
import time
import datetime
from twilio.rest import Client
# import RPi.GPIO


stop = False
switch_state = False

alarm_trigger_time = 3
alarm_over_time = 8


phone_number = '+17205130549'
account_sid = 'AC26b29f539656f407c7da50749db8f073'
auth_token = 'e3844d526b2ea49213a5c215cc75fbbd'
client = Client(account_sid, auth_token)


def send_text():
    t = datetime.datetime.now()
    message = client.messages.create(body=f'Alarm triggered at {t}',
                                     from_=phone_number,
                                     to='+16125999957')
    print(f'Sent message: {message.sid}')


def loop_thread():
    last_switch_time = 0
    last_switch_state = switch_state
    alarm_triggered = False
    alarm_overtime = False
    while not stop:
        if switch_state and not last_switch_state:
            t = time.time()
            print(f'Switch triggered at {t}')
            last_switch_time = t
        elif switch_state and last_switch_state:
            t = time.time()
            if t > last_switch_time + alarm_trigger_time and not alarm_triggered:
                print(f'Switch was open for {alarm_trigger_time} seconds - alarm triggered')
                alarm_triggered = True
            if t > last_switch_time + alarm_over_time and not alarm_overtime:
                print(f'Switch was open for {alarm_over_time} seconds - second alarm triggered')
                threading.Thread(target=send_text()).start()
                alarm_overtime = True
        elif not switch_state and last_switch_state:
            t = time.time()
            if alarm_triggered:
                print(f'Alarm was reset after {t - last_switch_time} sec')
                alarm_triggered = False
                alarm_overtime = False
            else:
                print(f'Switch was reset within {alarm_trigger_time} sec ({t - last_switch_time})')

        last_switch_state = switch_state

        time.sleep(0.1)


def input_thread():
    global switch_state
    while not stop:
        s = sys.stdin.readline().rstrip()
        if s == 'exit':
            break
        switch_state = not switch_state


# def signal_handler(sig=None, frame=None):
#     global stop
#     stop = True
#     t1.join()
#     t2.join()


# signal.signal(signal.SIGINT, signal_handler)


t1 = threading.Thread(target=loop_thread, daemon=True)
t2 = threading.Thread(target=input_thread)
t1.start()
t2.start()
