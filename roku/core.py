import logging
import requests

from lxml import etree as ET
from six.moves.urllib_parse import urlparse

from . import discovery
from .util import deserialize_apps


__version__ = '3.0.0'


ECP_KEYS = {
    'home': 'Home',
    'reverse': 'Rev',
    'forward': 'Fwd',
    'play': 'Play',
    'select': 'Select',
    'left': 'Left',
    'right': 'Right',
    'down': 'Down',
    'up': 'Up',
    'back': 'Back',
    'replay': 'InstantReplay',
    'info': 'Info',
    'backspace': 'Backspace',
    'search': 'Search',
    'enter': 'Enter',
    'literal': 'Lit',
}

ROKUTV_KEYS = {
    'channel_down': 'ChannelDown',
    'channel_up': 'ChannelUp',
    'mute': 'VolumeMute',
    'power': 'Power',
    'power_on': 'PowerOn',
    'power_off': 'PowerOff',
    'volume_down': 'VolumeDown',
    'volume_up': 'VolumeUp',
}

SENSORS = ('acceleration', 'magnetic', 'orientation', 'rotation')

TOUCH_OPS = ('up', 'down', 'press', 'move', 'cancel')


roku_logger = logging.getLogger('roku')


class RokuException(Exception):
    pass


class Application(object):

    def __init__(self, id, version, name, roku=None):
        self.id = str(id)
        self.version = version
        self.name = name
        self.roku = roku

    def __eq__(self, other):
        return isinstance(other, Application) and \
            (self.id, self.version) == (other.id, other.version)

    def __repr__(self):
        return ('<Application: [%s] %s v%s>' %
                (self.id, self.name, self.version))

    @property
    def icon(self):
        if self.roku:
            return self.roku.icon(self)

    def launch(self):
        if self.roku:
            self.roku.launch(self)

    def store(self):
        if self.roku:
            self.roku.store(self)


class DeviceInfo(object):

    def __init__(self, model_name, model_num, software_version, serial_num):
        self.model_name = model_name
        self.model_num = model_num
        self.software_version = software_version
        self.serial_num = serial_num

    def __repr__(self):
        return ('<DeviceInfo: %s-%s, SW v%s, Ser# %s>' %
                (self.model_name, self.model_num,
                 self.software_version, self.serial_num))


class Roku(object):

    @classmethod
    def discover(self, *args, **kwargs):
        rokus = []
        for device in discovery.discover(*args, **kwargs):
            o = urlparse(device.location)
            roku = Roku(o.hostname, o.port)
            device_info_response = roku._get('/query/device-info')
            device_info = ET.fromstring(device_info_response)
            is_tv = device_info.find('is-tv').text
            if is_tv == "true":
                rokus.append(RokuTV(o.hostname, o.port))
            else:
                rokus.append(roku)
        return rokus

    def __init__(self, host, port=8060):
        self.host = host
        self.port = port
        self._conn = None

        self.supported_keys = ECP_KEYS.copy()

    def __repr__(self):
        return "<Roku: %s:%s>" % (self.host, self.port)

    def __getattr__(self, name):

        if name not in self.supported_keys and name not in SENSORS:
            raise AttributeError('%s is not a valid key or sensor' % name)

        def command(*args):
            if name in SENSORS:
                keys = ['%s.%s' % (name, axis) for axis in ('x', 'y', 'z')]
                params = dict(zip(keys, args))
                self.input(params)
            elif name == 'literal':
                for char in args[0]:
                    path = '/keypress/%s_%s' % (
                        self.supported_keys[name], char.upper())
                    self._post(path)
            else:
                path = '/keypress/%s' % self.supported_keys[name]
                self._post(path)

        return command

    def __getitem__(self, key):
        key = str(key)
        app = self._app_for_name(key)
        if not app:
            app = self._app_for_id(key)
        return app

    def _app_for_name(self, name):
        for app in self.apps:
            if app.name == name:
                return app

    def _app_for_id(self, app_id):
        for app in self.apps:
            if app.id == app_id:
                return app

    def _connect(self):
        if self._conn is None:
            self._conn = requests.Session()

    def _get(self, path, *args, **kwargs):
        return self._call('GET', path, *args, **kwargs)

    def _post(self, path, *args, **kwargs):
        return self._call('POST', path, *args, **kwargs)

    def _call(self, method, path, *args, **kwargs):

        self._connect()

        roku_logger.debug(path)

        url = 'http://%s:%s%s' % (self.host, self.port, path)

        if method not in ('GET', 'POST'):
            raise ValueError('only GET and POST HTTP methods are supported')

        func = getattr(self._conn, method.lower())
        resp = func(url, *args, **kwargs)

        if resp.status_code != 200:
            raise RokuException(resp.content)

        return resp.content

    @property
    def apps(self):
        resp = self._get('/query/apps')
        applications = deserialize_apps(resp)
        for a in applications:
            a.roku = self
        return applications

    @property
    def device_info(self):
        resp = self._get('/query/device-info')
        root = ET.fromstring(resp)

        dinfo = DeviceInfo(
            model_name=root.find('model-name').text,
            model_num=root.find('model-number').text,
            software_version=''.join([
                root.find('software-version').text,
                '.',
                root.find('software-build').text
            ]),
            serial_num=root.find('serial-number').text
        )
        return dinfo

    @property
    def commands(self):
        return sorted(self.supported_keys.keys())

    def icon(self, app):
        return self._get('/query/icon/%s' % app.id)

    def launch(self, app):
        if app.roku and app.roku != self:
            raise RokuException('this app belongs to another Roku')
        return self._post('/launch/%s' % app.id, params={'contentID': app.id})

    def store(self, app):
        return self._post('/launch/11', params={'contentID': app.id})

    def input(self, params):
        return self._post('/input', params=params)

    def touch(self, x, y, op='down'):

        if op not in TOUCH_OPS:
            raise RokuException('%s is not a valid touch operation' % op)

        params = {
            'touch.0.x': x,
            'touch.0.y': y,
            'touch.0.op': op,
        }

        self.input(params)

    @property
    def current_app(self):
        resp = self._get('/query/active-app')
        root = ET.fromstring(resp)

        app_node = root.find('screensaver')
        if app_node is None:
            app_node = root.find('app')

        if app_node is None:
            return None

        return Application(
            id=app_node.get('id'),
            version=app_node.get('version'),
            name=app_node.text,
            roku=self,
        )

    @property
    def is_tv(self):
        return isinstance(self, RokuTV)


class RokuTV(Roku):

    INPUT_AV1 = 'AV1'
    INPUT_HDMI1 = 'HDMI1'
    INPUT_HDMI2 = 'HDMI2'
    INPUT_HDMI3 = 'HDMI3'
    INPUT_HDMI4 = 'HDMI4'
    INPUT_TUNER = 'Tuner'

    TV_INPUTS = (INPUT_AV1, INPUT_HDMI1, INPUT_HDMI2,
                 INPUT_HDMI3, INPUT_HDMI4, INPUT_TUNER)

    def __init__(self, *args, **kwargs):
        super(RokuTV, self).__init__(*args, **kwargs)
        self.supported_keys.update(ROKUTV_KEYS)

    def set_input(self, tv_input):
        if tv_input not in self.TV_INPUTS:
            raise RokuException('%s is not a valid TV input' % tv_input)
        return self._post('/keypress/Input{}'.format(tv_input))

    @property
    def current_power_mode(self):
        resp = self._get('/query/device-info')
        root = ET.fromstring(resp)
        return root.find('power-mode').text
