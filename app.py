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

DEFAULT_LOG_LEVEL = 'INFO'
LOGGER.setLevel(logging._nameToLevel[DEFAULT_LOG_LEVEL])
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
    'disable_gpio': False,
    'log_level': 'INFO'
}

CONFIG_FILENAME = 'conf.yaml'

main_threads: List[threading.Thread] = []
task_config: dict
slack_client: WebClient


def send_door_open_message():
    time_str = datetime.datetime.now().strftime(task_config['timestamp_format'])
    message = task_config['door_open_message'].replace('$TIMESTAMP', time_str)
    resp = slack_client.chat_postMessage(channel=task_config['slack_channel_id'], text=message)
    LOGGER.info(f'Sent message: {resp}')


def send_overtime_message():
    time_str = datetime.datetime.now().strftime(task_config['timestamp_format'])
    message = task_config['door_overtime_message'].replace('$TIMESTAMP', time_str).replace('$DURATION', str(
        task_config['door_open_overtime_delay']))
    resp = slack_client.chat_postMessage(channel=task_config['slack_channel_id'], text=message)
    LOGGER.info(f'Sent message: {resp}')


def loop_thread():
    LOGGER.debug('Entered main loop thread')
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
                threading.Thread(target=send_door_open_message).start()
                alarm_triggered = True
            if t > last_switch_time + door_open_overtime_delay and not alarm_overtime:
                LOGGER.info(f'Switch was open for {door_open_overtime_delay} seconds - second alarm triggered')
                threading.Thread(target=send_overtime_message).start()
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


def switch_monitor_thread():
    global switch_state
    LOGGER.debug('Entered switch input thread')
    switch_pin = task_config['switch_pin']
    GPIO.setup(switch_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    while not shutdown_signal:
        val = GPIO.input(switch_pin)
        switch_state = val == 0
        time.sleep(1)
    LOGGER.info('Switch input thread shutting down')


def input_thread():
    global switch_state
    LOGGER.debug('Entered keyboard input thread')
    while not shutdown_signal:
        s = sys.stdin.readline().rstrip()
        if s == 'exit':
            break
        switch_state = not switch_state
    LOGGER.info('Keyboard input thread shutting down')


def handle_signal(sig=None, frame=None):
    LOGGER.warning(f'Got shutdown signal: {sig}')
    shutdown()


def shutdown():
    global shutdown_signal
    LOGGER.warning('Shutting down')
    shutdown_signal = True
    if not DEV_ENV and not task_config['disable_gpio']:
        GPIO.cleanup()
        LOGGER.info('GPIO cleanup complete')

    LOGGER.info('Waiting for threads')
    for t in main_threads:
        t.join()
    LOGGER.info('All threads stopped')


signal.signal(signal.SIGHUP, handle_signal)
signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGQUIT, handle_signal)
signal.signal(signal.SIGILL, handle_signal)
signal.signal(signal.SIGTRAP, handle_signal)
signal.signal(signal.SIGABRT, handle_signal)
signal.signal(signal.SIGBUS, handle_signal)
signal.signal(signal.SIGFPE, handle_signal)
signal.signal(signal.SIGUSR1, handle_signal)
signal.signal(signal.SIGSEGV, handle_signal)
signal.signal(signal.SIGUSR2, handle_signal)
signal.signal(signal.SIGPIPE, handle_signal)
signal.signal(signal.SIGALRM, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def get_channel_id(channel_name: str):
    resp = slack_client.conversations_list(types='public_channel,private_channel')

    for channel in resp['channels']:
        if channel['name'] == channel_name:
            return channel['id']

    raise Exception(f'Could not find slack channel named {channel_name}')


def validate_config():
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

    if 'log_level' in task_config:
        level = task_config['log_level']
        if level not in logging._nameToLevel:
            raise ValueError(f'{level} is not a valid log level. Expected one of: {logging._nameToLevel.keys()}')
        LOGGER.setLevel(logging._nameToLevel[level])

    try:
        datetime.datetime.now().strftime(task_config['timestamp_format'])
    except:
        raise Exception(f'Invalid timestamp format: {task_config["timestamp_format"]}')


def configure():
    global task_config
    global slack_client
    task_config = DEFAULT_CONFIG

    with open(CONFIG_FILENAME, 'r') as conf_file:
        config = yaml.load(conf_file, Loader=yaml.FullLoader)

    if config:
        task_config.update(config)

    LOGGER.info(f'Got config: {task_config}')
    validate_config()
    slack_client = WebClient(token=task_config['slack_api_token'])
    task_config['slack_channel_id'] = get_channel_id(task_config['slack_channel'])
    LOGGER.info(f'Validated config: {task_config}')


def execute():
    try:
        configure()

        main_threads.append(
            threading.Thread(name='alarm_handler', target=loop_thread))

        if DEV_ENV or task_config['dev_mode']:
            main_threads.append(threading.Thread(name='input_sim_thread', target=input_thread))
        else:
            LOGGER.debug('DEV_ENV is false and dev_mode is false; did not start keyboard input thread')

        if not DEV_ENV and not task_config['disable_gpio']:
            main_threads.append(threading.Thread(name='switch_checker', target=switch_monitor_thread))
            GPIO.setmode(GPIO.BCM)
        else:
            LOGGER.debug('DEV_ENV is true or disable_gpio is true; did not start switch input thread')

        for t in main_threads:
            t.start()

    except Exception as e:
        LOGGER.exception('Error during initialization', exc_info=True)


if __name__ == '__main__':
    LOGGER.info(f'Starting execution')
    execute()
    LOGGER.info(f'Initialization complete; main thread exited')
