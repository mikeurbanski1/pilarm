import os
import signal
import sys
import threading
import time
import datetime
import yaml
import traceback
from slack import WebClient
# import RPi.GPIO

channel_id = 'C018H9JPE1G'

stop = False
switch_state = False

DEFAULT_CONFIG = {
    'timestamp_format': '%I:%M:%S %p on %Y/%m/%d',
    'door_opened_delay': '3',
    'door_open_overtime_delay': '8',
    'door_open_message': 'Door opened at $TIMESTAMP',
    'door_overtime_message': 'Door has been opened for $DURATION seconds as of $TIMESTAMP'
}

CONFIG_FILENAME = 'conf.yaml'


def send_door_open_message(task_config: dict, slack_client: WebClient):
    time_str = datetime.datetime.now().strftime(task_config['timestamp_format'])
    message = task_config['door_open_message'].replace('$TIMESTAMP', time_str)
    resp = slack_client.chat_postMessage(channel=task_config['slack_channel_id'], text=message)
    print(f'Sent message: {resp}')


def send_overtime_message(task_config: dict, slack_client: WebClient):
    time_str = datetime.datetime.now().strftime(task_config['timestamp_format'])
    message = task_config['door_overtime_message'].replace('$TIMESTAMP', time_str).replace('$DURATION', str(task_config['door_open_overtime_delay']))
    resp = slack_client.chat_postMessage(channel=task_config['slack_channel_id'], text=message)
    print(f'Sent message: {resp}')


def loop_thread(task_config: dict, slack_client: WebClient):
    last_switch_time = 0
    last_switch_state = switch_state
    alarm_triggered = False
    alarm_overtime = False
    door_opened_delay = int(task_config['door_opened_delay'])
    door_open_overtime_delay = int(task_config['door_open_overtime_delay'])

    while not stop:
        if switch_state and not last_switch_state:
            t = time.time()
            print(f'Switch triggered at {t}')
            last_switch_time = t
        elif switch_state and last_switch_state:
            t = time.time()
            if t > last_switch_time + door_opened_delay and not alarm_triggered:
                print(f'Switch was open for {door_opened_delay} seconds - alarm triggered')
                threading.Thread(target=send_door_open_message, args=(task_config, slack_client)).start()
                alarm_triggered = True
            if t > last_switch_time + door_open_overtime_delay and not alarm_overtime:
                print(f'Switch was open for {door_open_overtime_delay} seconds - second alarm triggered')
                threading.Thread(target=send_overtime_message, args=(task_config, slack_client)).start()
                alarm_overtime = True
        elif not switch_state and last_switch_state:
            t = time.time()
            if alarm_triggered:
                print(f'Alarm was reset after {t - last_switch_time} sec')
                alarm_triggered = False
                alarm_overtime = False
            else:
                print(f'Switch was reset within {door_opened_delay} sec ({t - last_switch_time})')

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
def get_channel_id(channel_name: str, slack_client: WebClient):
    resp = slack_client.conversations_list(types='public_channel,private_channel')

    for channel in resp['channels']:
        if channel['name'] == channel_name:
            return channel['id']

    raise Exception(f'Could not find slack channel named {channel_name}')


def validate_config(task_config: dict):
    keys_to_validate = ['slack_channel', 'slack_api_token']
    missing_keys = []

    for key in keys_to_validate:
        if key not in task_config:
            missing_keys.append(key)

    if missing_keys:
        raise Exception(f'Configuration keys missing: {", ".join(missing_keys)}')

    int_keys = ['door_opened_delay', 'door_open_overtime_delay']
    not_ints = []
    for key in int_keys:
        try:
            val = int(task_config[key])
            task_config[key] = val
        except ValueError:
            not_ints.append(key)

    if not_ints:
        raise Exception(f'Expected int values: {", ".join(not_ints)}')

    try:
        datetime.datetime.now().strftime(task_config['timestamp_format'])
    except:
        raise Exception(f'Invalid timestamp format: {task_config["timestamp_format"]}')


def execute():
    try:
        task_config = DEFAULT_CONFIG

        with open(CONFIG_FILENAME, 'r') as conf_file:
            config = yaml.load(conf_file, Loader=yaml.FullLoader)

        if config:
            task_config.update(config)

        print(task_config)
        validate_config(task_config)

        slack_client = WebClient(token=task_config['slack_api_token'])
        task_config['slack_channel_id'] = get_channel_id(task_config['slack_channel'], slack_client)

        t1 = threading.Thread(target=loop_thread, daemon=True, args=(task_config, slack_client))
        t2 = threading.Thread(target=input_thread)
        t1.start()
        t2.start()

    except Exception as e:
        print('Error during initialization')
        traceback.print_exc()


if __name__ == '__main__':
    execute()

