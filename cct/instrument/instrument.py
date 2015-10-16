from ..devices.detector import Pilatus
from ..devices.xray_source import GeniX
from ..devices.motor import TMCM351, TMCM6110
from ..devices.vacuumgauge import TPG201
from ..devices.device import DeviceError
from ..services import Interpreter
from .motor import Motor
from gi.repository import GLib
import traceback
import json
import os
import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

from gi.repository import GObject


class InstrumentError(Exception):
    pass


class Instrument(GObject.GObject):
    configdir = 'config'

    def __init__(self):
        GObject.GObject.__init__(self)
        self.devices = {}
        self.xray_source = None
        self.detector = None
        self.motorcontrollers = {}
        self.motors = {}
        self.environmentcontrollers = {}
        self.configfile = os.path.join(self.configdir, 'cct.json')
        self._initialize_config()
        self.load_state()
        self.start_services()

    def _initialize_config(self):
        """Create a sane configuration in `self.config` from sratch."""
        self.config = {}
        self.config['path'] = {}
        self.config['path']['directories'] = {'log': 'log',
                                              'images': 'images',
                                              'param': 'param',
                                              'config': 'config',
                                              'mask': 'mask',
                                              'nexus': 'nexus',
                                              'eval1d': 'eval1d',
                                              'eval2d': 'eval2d',
                                              'param_override': 'param_override',
                                              'scan': 'scan'
                                              }
        self.config['path']['fsndigits'] = 5
        self.config['connections'] = {}
        self.config['connections']['xray_source'] = {'host': 'genix.credo',
                                                     'port': 502,
                                                     'timeout': 1,
                                                     'name': 'genix'}
        self.config['connections']['detector'] = {'host': 'pilatus300k.credo',
                                                  'port': 41234,
                                                  'timeout': 0.01,
                                                  'poll_timeout': 0.01,
                                                  'name': 'pilatus'}
        self.config['connections']['environmentcontrollers'] = {}
        self.config['connections']['environmentcontrollers']['vacuum'] = {
            'host': 'devices.credo',
            'port': 2006,
            'timeout': 0.1,
            'poll_timeout': 0.1,
            'name': 'tpg201'}
        self.config['connections']['environmentcontrollers']['temperature'] = {
            'host': 'devices.credo',
            'port': 2001,
            'timeout': 0.01,
            'poll_timeout': 0.01,
            'name': 'haakephoenix'}
        self.config['connections']['motorcontrollers'] = {}
        self.config['connections']['motorcontrollers']['tmcm351a'] = {
            'host': 'devices.credo',
            'port': 2003,
            'timeout': 0.01,
            'poll_timeout': 0.01,
            'name': 'tmcm351a'}
        self.config['connections']['motorcontrollers']['tmcm351b'] = {
            'host': 'devices.credo',
            'port': 2004,
            'timeout': 0.01,
            'poll_timeout': 0.01,
            'name': 'tmcm351b'}
        self.config['connections']['motorcontrollers']['tmcm6110'] = {
            'host': 'devices.credo',
            'port': 2005,
            'timeout': 0.01,
            'poll_timeout': 0.01,
            'name': 'tmcm6110'}
        self.config['motors'] = [{'name': 'Unknown1', 'controller': 'tmcm351a', 'index': 0},
                                 {'name': 'Sample_X',
                                     'controller': 'tmcm351a', 'index': 1},
                                 {'name': 'Sample_Y',
                                     'controller': 'tmcm351a', 'index': 2},
                                 {'name': 'PH1X',
                                     'controller': 'tmcm6110', 'index': 0},
                                 {'name': 'PH1Y',
                                     'controller': 'tmcm6110', 'index': 1},
                                 {'name': 'PH2X',
                                     'controller': 'tmcm6110', 'index': 2},
                                 {'name': 'PH2Y',
                                     'controller': 'tmcm6110', 'index': 3},
                                 {'name': 'PH3X',
                                     'controller': 'tmcm6110', 'index': 4},
                                 {'name': 'PH3Y',
                                     'controller': 'tmcm6110', 'index': 5},
                                 {'name': 'BeamStop_X',
                                     'controller': 'tmcm351b', 'index': 0},
                                 {'name': 'BeamStop_Y',
                                     'controller': 'tmcm351b', 'index': 1},
                                 {'name': 'Unknown2', 'controller': 'tmcm351b', 'index': 2}]
        self.config['devices'] = {}

    def save_state(self):
        """Save the current configuration (including that of all devices) to a
        JSON file."""
        for d in self.devices:
            self.config['devices'][d] = self.devices[d]._save_state()
        with open(self.configfile, 'wt', encoding='utf-8') as f:
            json.dump(self.config, f)

    def load_state(self):
        """Load the saved configuration file. This is only useful before
        connecting to devices, because status of the back-end process is
        not updated by Device._load_state()."""
        try:
            with open(self.configfile, 'rt', encoding='utf-8') as f:
                self.config = json.load(f)
        except IOError:
            return
        for d in self.devices:
            self.devices[d]._load_state(self.config['devices'][d])

    def connect_devices(self):
        """Try to connect to all devices. Send error logs on failures. Return
        a list of unsuccessfully connected devices."""

        unsuccessful = []
        if self.xray_source is None:
            self.xray_source = GeniX(
                self.config['connections']['xray_source']['name'])
            self.devices[
                self.config['connections']['xray_source']['name']] = self.xray_source
        try:
            self.xray_source.connect_device(self.config['connections']['xray_source']['host'],
                                            self.config['connections'][
                                                'xray_source']['port'],
                                            self.config['connections']['xray_source']['timeout'])
        except DeviceError:
            logger.error(
                'Cannot connect to X-ray source: ' + traceback.format_exc())
            self.xray_source = None
            del self.devices[self.config['connections']['xray_source']['name']]
            unsuccessful.append(
                self.config['connections']['xray_source']['name'])
        if self.detector is None:
            self.detector = Pilatus(
                self.config['connections']['detector']['name'])
            self.devices[
                self.config['connections']['detector']['name']] = self.detector
        try:
            self.detector.connect_device(self.config['connections']['detector']['host'],
                                         self.config['connections'][
                                             'detector']['port'],
                                         self.config['connections'][
                                             'detector']['timeout'],
                                         self.config['connections']['detector']['poll_timeout'])
        except DeviceError:
            logger.error(
                'Cannot connect to detector: ' + traceback.format_exc())
            self.detector = None
            del self.devices[self.config['connections']['detector']['name']]
            unsuccessful.append(self.config['connections']['detector']['name'])
        for motcont in self.config['connections']['motorcontrollers']:
            cfg = self.config['connections']['motorcontrollers'][motcont]
            if motcont not in self.motorcontrollers:
                if cfg['name'].startswith('tmcm351'):
                    self.motorcontrollers[motcont] = TMCM351(cfg['name'])
                elif cfg['name'].startswith('tmcm6110'):
                    self.motorcontrollers[motcont] = TMCM6110(cfg['name'])
                self.devices[cfg['name']] = self.motorcontrollers[motcont]
            try:
                self.motorcontrollers[motcont].connect_device(
                    cfg['host'], cfg['port'], cfg['timeout'], cfg['poll_timeout'])
            except DeviceError:
                logger.error('Cannot connect to motor driver %s: %s' %
                             (cfg['name'], traceback.format_exc()))
                del self.motorcontrollers[cfg['name']]
                del self.devices[cfg['name']]
                unsuccessful.append(cfg['name'])
        for m in self.config['motors']:
            try:
                self.motors[m['name']] = Motor(
                    self.motorcontrollers[m['controller']], m['index'])
            except KeyError as ke:
                logger.error('Cannot find motor %s' % m['name'])
        for envcont in self.config['connections']['environmentcontrollers']:
            cfg = self.config['connections']['environmentcontrollers'][envcont]
            if envcont not in self.environmentcontrollers:
                if envcont == 'temperature':
                    continue  # TODO
                elif envcont == 'vacuum':
                    self.environmentcontrollers[envcont] = TPG201(cfg['name'])
                self.devices[
                    cfg['name']] = self.environmentcontrollers[envcont]
            try:
                self.environmentcontrollers[envcont].connect_device(
                    cfg['host'], cfg['port'], cfg['timeout'], cfg['poll_timeout'])
            except DeviceError:
                logger.error('Cannot connect to %s controller: %s' %
                             (envcont, traceback.format_exc()))
                del self.environmentcontrollers[envcont]
                del self.devices[cfg['name']]
                unsuccessful.append(cfg['name'])
        return unsuccessful

    def start_services(self):
        self.interpreter = Interpreter(self)
