import os
import signal
import sys
import threading
import time
import datetime
from typing import Tuple, List

import yaml
import logging.handlers
from slack import WebClient

LOGGER: logging = logging.getLogger(__name__)

LOGGER.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

os.makedirs('logs', exist_ok=True)
# log_file_size = 1 * 1024 * 1024  # 1 MB
# file_handler = logging.handlers.RotatingFileHandler('logs/pilarm.log', maxBytes=log_file_size, backupCount=10)
# file_handler.setFormatter(formatter)
# LOGGER.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
LOGGER.addHandler(console_handler)

try:
    import RPi.GPIO as GPIO

    LOGGER.info('Imported RPi package')
    DEV_ENV = False
except ModuleNotFoundError:
    import FakeRPi.GPIO as GPIO
    LOGGER.info('Imported FakeRPi package')
    DEV_ENV = True

shutdown_signal = False
switch_state = False

DEFAULT_CONFIG = {
    'timestamp_format': '%I:%M:%S %p on %Y/%m/%d',
    'door_opened_delay': '3',
    'door_open_overtime_delay': '8',
    'door_open_message': 'Door opened at $TIMESTAMP',
    'door_overtime_message': 'Door has been opened for $DURATION seconds as of $TIMESTAMP',
    # 'slack_verbosity': 3,
    'switch_pin': 2,
    'light_pin_r': 14,
    'light_pin_g': 15,
    'light_pin_b': 18,
    'dev_mode': False,
    'disable_gpio': False
}

CONFIG_FILENAME = 'conf.yaml'

main_threads: List[threading.Thread] = []


def send_door_open_message(task_config: dict, slack_client: WebClient):
    time_str = datetime.datetime.now().strftime(task_config['timestamp_format'])
    message = task_config['door_open_message'].replace('$TIMESTAMP', time_str)
    resp = slack_client.chat_postMessage(channel=task_config['slack_channel_id'], text=message)
    LOGGER.info(f'Sent message: {resp}')


def send_overtime_message(task_config: dict, slack_client: WebClient):
    time_str = datetime.datetime.now().strftime(task_config['timestamp_format'])
    message = task_config['door_overtime_message'].replace('$TIMESTAMP', time_str).replace('$DURATION', str(
        task_config['door_open_overtime_delay']))
    resp = slack_client.chat_postMessage(channel=task_config['slack_channel_id'], text=message)
    LOGGER.info(f'Sent message: {resp}')


def loop_thread(task_config: dict, slack_client: WebClient):
    last_switch_time = 0
    last_switch_state = switch_state
    alarm_triggered = False
    alarm_overtime = False
    door_opened_delay = int(task_config['door_opened_delay'])
    door_open_overtime_delay = int(task_config['door_open_overtime_delay'])

    while not shutdown_signal:
        if switch_state and not last_switch_state:
            t = time.time()
            LOGGER.info(f'Switch triggered at {t}')
            last_switch_time = t
        elif switch_state and last_switch_state:
            t = time.time()
            if t > last_switch_time + door_opened_delay and not alarm_triggered:
                LOGGER.info(f'Switch was open for {door_opened_delay} seconds - alarm triggered')
                threading.Thread(target=send_door_open_message, args=(task_config, slack_client)).start()
                alarm_triggered = True
            if t > last_switch_time + door_open_overtime_delay and not alarm_overtime:
                LOGGER.info(f'Switch was open for {door_open_overtime_delay} seconds - second alarm triggered')
                threading.Thread(target=send_overtime_message, args=(task_config, slack_client)).start()
                alarm_overtime = True
        elif not switch_state and last_switch_state:
            t = time.time()
            if alarm_triggered:
                LOGGER.info(f'Alarm was reset after {t - last_switch_time} sec')
                alarm_triggered = False
                alarm_overtime = False
            else:
                LOGGER.info(f'Switch was reset within {door_opened_delay} sec ({t - last_switch_time})')

        last_switch_state = switch_state

        time.sleep(0.1)

    LOGGER.info('Loop thread shutting down')


def switch_monitor_thread(task_config: dict):
    global switch_state

    while not shutdown_signal:
        val = GPIO.input(task_config['switch_pin'])
        LOGGER.info(f'Pin value: {val}')
        time.sleep(1)


def input_thread():
    global switch_state
    while not shutdown_signal:
        s = sys.stdin.readline().rstrip()
        if s == 'exit':
            break
        switch_state = not switch_state
    LOGGER.info('Input thread shutting down')


def handle_signal(sig=None, frame=None):
    LOGGER.warning(f'Got shutdown signal: {sig}')
    shutdown()


def shutdown():
    global shutdown_signal
    LOGGER.warning('Shutting down')
    shutdown_signal = True
    LOGGER.info('Waiting for threads')
    for t in main_threads:
        t.join()
    LOGGER.info('All threads stopped')


signal.signal(signal.SIGHUP, shutdown)
signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGQUIT, shutdown)
signal.signal(signal.SIGILL, shutdown)
signal.signal(signal.SIGTRAP, shutdown)
signal.signal(signal.SIGABRT, shutdown)
signal.signal(signal.SIGBUS, shutdown)
signal.signal(signal.SIGFPE, shutdown)
# signal.signal(signal.SIGKILL, receiveSignal)
signal.signal(signal.SIGUSR1, shutdown)
signal.signal(signal.SIGSEGV, shutdown)
signal.signal(signal.SIGUSR2, shutdown)
signal.signal(signal.SIGPIPE, shutdown)
signal.signal(signal.SIGALRM, shutdown)
signal.signal(signal.SIGTERM, shutdown)


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

    int_keys = [
        'door_opened_delay',
        'door_open_overtime_delay',
        'switch_pin',
        'light_pin_r',
        'light_pin_g',
        'light_pin_b'
    ]
    not_ints = []
    for key in int_keys:
        try:
            val = int(task_config[key])
            task_config[key] = val
        except ValueError:
            not_ints.append(key)

    if not_ints:
        raise Exception(f'Expected int values: {", ".join(not_ints)}')

    bool_keys = ['dev_mode', 'disable_gpio']

    for key in bool_keys:
        s = str(task_config[key])
        if s.lower() not in ['true', 'false']:
            raise ValueError(f'Got value {s} for {key}; expected true or false')
        task_config[key] = s.lower() == 'true'

    try:
        datetime.datetime.now().strftime(task_config['timestamp_format'])
    except:
        raise Exception(f'Invalid timestamp format: {task_config["timestamp_format"]}')


def configure() -> Tuple[dict, WebClient]:
    task_config = DEFAULT_CONFIG

    with open(CONFIG_FILENAME, 'r') as conf_file:
        config = yaml.load(conf_file, Loader=yaml.FullLoader)

    if config:
        task_config.update(config)

    LOGGER.info(f'Got config: {task_config}')
    validate_config(task_config)
    LOGGER.info(f'Validated config: {task_config}')

    slack_client = WebClient(token=task_config['slack_api_token'])
    task_config['slack_channel_id'] = get_channel_id(task_config['slack_channel'], slack_client)
    return task_config, slack_client


def execute():
    try:
        task_config, slack_client = configure()

        main_threads.append(
            threading.Thread(name='alarm_handler', target=loop_thread, args=(task_config, slack_client)))

        if DEV_ENV or task_config['dev_mode']:
            main_threads.append(threading.Thread(name='input_sim_thread', target=input_thread))

        if not DEV_ENV:
            main_threads.append(threading.Thread(name='switch_checker', target=switch_monitor_thread, args=(task_config,)))

        for t in main_threads:
            t.start()

    except Exception as e:
        LOGGER.exception('Error during initialization', exc_info=True)


if __name__ == '__main__':
    LOGGER.info(f'Starting execution')
    execute()
    LOGGER.info(f'Initialization complete; main thread exited')
