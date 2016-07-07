import datetime
import logging
import multiprocessing
import os
import pickle
import resource
import time
import traceback
from typing import List

import psutil

from .motor import Motor
from ..devices.device import DeviceError, Device_ModbusTCP, Device_TCP, Device
from ..services import Interpreter, FileSequence, ExposureAnalyzer, SampleStore, Accounting, WebStateFileWriter

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

from gi.repository import GObject, GLib


class DummyTm(object):
    ru_utime = None
    ru_stime = None
    ru_maxrss = None
    ru_minflt = None
    ru_majflt = None
    ru_inblock = None
    ru_oublock = None
    ru_nvcsw = None
    ru_nivcsw = None


class InstrumentError(Exception):
    pass


def get_telemetry():
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    la = ', '.join([str(f) for f in os.getloadavg()])
    return {'processname': multiprocessing.current_process().name,
            'self': resource.getrusage(resource.RUSAGE_SELF),
            'children': resource.getrusage(resource.RUSAGE_CHILDREN),
            'inqueuelen': 0,
            'freephysmem': vm.available,
            'totalphysmem': vm.total,
            'freeswap': sm.free,
            'totalswap': sm.total,
            'loadavg': la,
            }


class Instrument(GObject.GObject):
    configdir = 'config'
    telemetry_timeout = 0.9
    statusfile_timeout = 30
    memlog_timeout = 60  # write memory usage log
    memlog_file = 'memory.log'

    __gsignals__ = {
        # emitted when all devices have been initialized.
        'devices-ready': (GObject.SignalFlags.RUN_FIRST, None, ()),
        # emitted when telemetry data is obtained from a component. The first
        # argument is the name of the component, the second is the type of the
        # component (service or device), and the third one is the telemetry
        # dictionary.
        'telemetry': (GObject.SignalFlags.RUN_FIRST, None, (object, str, object)),
    }

    def __init__(self, online):
        self._starttime = datetime.datetime.now()
        GObject.GObject.__init__(self)
        self._online = online
        self.devices = {}
        self.services = []
        self.motors = {}
        self.environmentcontrollers = {}
        self.configfile = os.path.join(self.configdir, 'cct.pickle')
        self._initialize_config()
        self._signalconnections = {}
        self._waiting_for_ready = []
        self._telemetries = {}
        self.busy = multiprocessing.Event()
        self.load_state()
        self.create_services()

    def start(self):
        """Start operation"""
        self._telemetry_timeout = GLib.timeout_add(self.telemetry_timeout * 1000,
                                                   self.on_telemetry_timeout)

        memlogfiles = [f for f in os.listdir(self.config['path']['directories']['log']) if
                       f.startswith(self.memlog_file)]
        if not memlogfiles:
            self.memlog_file = self.memlog_file + '.0'
        else:
            maxidx = max([int(f.rsplit('.', 1)[1]) for f in memlogfiles])
            self.memlog_file = self.memlog_file + '.{:d}'.format(maxidx + 1)
        self.memlog_file = os.path.join(self.config['path']['directories']['log'], self.memlog_file)
        with open(self.memlog_file, 'xt', encoding='utf-8') as f:
            f.write('# CCT memory log file created {}\n'.format(datetime.datetime.now()))
            f.write('# Epoch (sec)\tUptime (sec)\tMain (MB)')
            for d in sorted(self.devices):
                f.write('\t {} (MB)'.format(d))
            for s in ['exposureanalyzer']:
                f.write('\t {} (MB)'.format(s))
            f.write('\n')
        self._memlog_timeout = GLib.timeout_add(self.memlog_timeout * 1000,
                                                self.on_memlog_timeout)
        for s in self.services:
            s.start()
        logger.info('Started services.')

    def _initialize_config(self):
        """Create a sane configuration in `self.config` from scratch."""
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
                                              'scan': 'scan',
                                              'images_detector': ['/disk2/images', '/home/det/p2_det/images'],
                                              'status': 'status',
                                              }
        self.config['path']['fsndigits'] = 5
        self.config['path']['prefixes'] = {'crd': 'crd',
                                           'scn': 'scn',
                                           'tra': 'tra',
                                           'tst': 'tst'}
        self.config['geometry'] = {'dist_sample_det': 1000.,
                                   'dist_sample_det.err': 0.,
                                   'dist_source_ph1': 100.,
                                   'dist_ph1_ph2': 100.,
                                   'dist_ph2_ph3': 1.,
                                   'dist_ph3_sample': 2.,
                                   'dist_det_beamstop': 1.,
                                   'pinhole_1': 1000.,
                                   'pinhole_2': 300.,
                                   'pinhole_3': 750.,
                                   'description': 'Generic geometry, please correct values',
                                   'beamstop': 4.,
                                   'wavelength': 0.15418,
                                   'wavelength.err': 0.15418 * 0.03,
                                   'beamposx': 330.,
                                   'beamposy': 257.,
                                   'pixelsize': 0.172,
                                   'mask': 'mask.mat'}
        self.config['connections'] = {}
        self.config['connections']['xray_source'] = {'host': 'genix.credo',
                                                     'port': 502,
                                                     'timeout': 1,
                                                     'name': 'genix',
                                                     'classname': 'GeniX'}
        self.config['connections']['detector'] = {'host': 'pilatus300k.credo',
                                                  'port': 41234,
                                                  'timeout': 0.01,
                                                  'poll_timeout': 0.01,
                                                  'name': 'pilatus',
                                                  'classname': 'Pilatus'}
        self.config['connections']['vacuum'] = {
            'host': 'devices.credo',
            'port': 2006,
            'timeout': 0.1,
            'poll_timeout': 0.1,
            'name': 'tpg201',
            'classname': 'TPG201'}
        self.config['connections']['temperature'] = {
            'host': 'devices.credo',
            'port': 2001,
            'timeout': 0.1,
            'poll_timeout': 0.05,
            'name': 'haakephoenix',
            'classname': 'HaakePhoenix'}
        self.config['connections']['tmcm351a'] = {
            'host': 'devices.credo',
            'port': 2003,
            'timeout': 0.01,
            'poll_timeout': 0.01,
            'name': 'tmcm351a',
            'classname': 'TMCM351'}
        self.config['connections']['tmcm351b'] = {
            'host': 'devices.credo',
            'port': 2004,
            'timeout': 0.01,
            'poll_timeout': 0.01,
            'name': 'tmcm351b',
            'classname': 'TMCM351'}
        self.config['connections']['tmcm6110'] = {
            'host': 'devices.credo',
            'port': 2005,
            'timeout': 0.01,
            'poll_timeout': 0.01,
            'name': 'tmcm6110',
            'classname': 'TMCM6110'}
        self.config['motors'] = {'0': {'name': 'Unknown1', 'controller': 'tmcm351a', 'index': 0},
                                 '1': {'name': 'Sample_X',
                                       'controller': 'tmcm351a', 'index': 1},
                                 '2': {'name': 'Sample_Y',
                                       'controller': 'tmcm351a', 'index': 2},
                                 '3': {'name': 'PH1_X',
                                       'controller': 'tmcm6110', 'index': 0},
                                 '4': {'name': 'PH1_Y',
                                       'controller': 'tmcm6110', 'index': 1},
                                 '5': {'name': 'PH2_X',
                                       'controller': 'tmcm6110', 'index': 2},
                                 '6': {'name': 'PH2_Y',
                                       'controller': 'tmcm6110', 'index': 3},
                                 '7': {'name': 'PH3_X',
                                       'controller': 'tmcm6110', 'index': 4},
                                 '8': {'name': 'PH3_Y',
                                       'controller': 'tmcm6110', 'index': 5},
                                 '9': {'name': 'BeamStop_X',
                                       'controller': 'tmcm351b', 'index': 0},
                                 '10': {'name': 'BeamStop_Y',
                                        'controller': 'tmcm351b', 'index': 1},
                                 '11': {'name': 'Unknown2', 'controller': 'tmcm351b', 'index': 2}}
        self.config['devices'] = {}
        self.config['services'] = {
            'interpreter': {}, 'samplestore': {'list': [], 'active': None}, 'filesequence': {}, 'exposureanalyzer': {}}
        self.config['services']['accounting'] = {'operator': 'CREDOoperator',
                                                 'projectid': 'Project ID',
                                                 'projectname': 'Project name',
                                                 'proposer': 'Main proposer',
                                                 'default_realm': 'MTATTKMFIBNO',
                                                 }

        self.config['scan'] = {'mask': 'mask.mat',
                               'mask_total': 'mask.mat',
                               'columns': ['FSN', 'total_sum', 'sum', 'total_max', 'max', 'total_beamx', 'beamx',
                                           'total_beamy', 'beamy', 'total_sigmax', 'sigmax', 'total_sigmay', 'sigmay',
                                           'total_sigma', 'sigma'],
                               'scanfile': 'credoscan2.spec'}
        self.config['transmission'] = {'empty_sample': 'Empty_Beam', 'nimages': 10, 'exptime': 0.5, 'mask': 'mask.mat'}
        self.config['beamstop'] = {'in': (3, 3), 'out': (3, 10)}
        self.config['calibrants'] = {'Silver behenate': {'Peak #1': {'val': 1.0759, 'err': 0.0007},
                                                         'Peak #2': {'val': 2.1518, 'err': 0.0014},
                                                         'Peak #3': {'val': 3.2277, 'err': 0.0021},
                                                         'Peak #4': {'val': 4.3036, 'err': 0.0028},
                                                         'Peak #5': {'val': 5.3795, 'err': 0.0035},
                                                         'Peak #6': {'val': 6.4554, 'err': 0.0042},
                                                         'Peak #7': {'val': 7.5313, 'err': 0.0049},
                                                         'Peak #8': {'val': 8.6072, 'err': 0.0056},
                                                         'Peak #9': {'val': 9.6831, 'err': 0.0063},
                                                         },
                                     'SBA15': {'(10)': {'val': 0.6839, 'err': 0.0002},
                                               '(11)': {'val': 1.1846, 'err': 0.0003},
                                               '(20)': {'val': 1.3672, 'err': 0.0002},
                                               },
                                     'LaB6': {'(100)': {'val': 15.11501, 'err': 0.00004},
                                              '(110)': {'val': 21.37584, 'err': 0.00004},
                                              '(111)': {'val': 26.18000, 'err': 0.00004}},
                                     }
        self.config['datareduction'] = {'backgroundname': 'Empty_Beam',
                                        'darkbackgroundname': 'Dark',
                                        'absintrefname': 'Glassy_Carbon',
                                        'absintrefdata': 'config/GC_data_nm.dat',
                                        'distancetolerance': 100,  # mm
                                        'mu_air': 1000,  # ToDo
                                        'mu_air.err': 0  # ToDo
                                        }
        self.config['services']['webstatefilewriter'] = {}

    def save_state(self):
        """Save the current configuration (including that of all devices) to a
        pickle file."""
        for d in self.devices:
            self.config['devices'][d] = self.devices[d]._save_state()
        for service in ['interpreter', 'samplestore', 'filesequence', 'exposureanalyzer', 'accounting']:
            self.config['services'][service] = getattr(
                self, service)._save_state()
        with open(self.configfile, 'wb') as f:
            pickle.dump(self.config, f)
        logger.info('Saved state to ' + self.configfile)
        self.exposureanalyzer.sendconfig()

    def _update_config(self, config_orig, config_loaded):
        """Uppdate the config dictionary in `config_orig` with the loaded
        dictionary in `config_loaded`, recursively."""
        for c in config_loaded:
            if c not in config_orig:
                config_orig[c] = config_loaded[c]
            elif isinstance(config_orig[c], dict) and isinstance(config_loaded[c], dict):
                self._update_config(config_orig[c], config_loaded[c])
            else:
                config_orig[c] = config_loaded[c]
        return

    def load_state(self):
        """Load the saved configuration file. This is only useful before
        connecting to devices, because status of the back-end process is
        not updated by Device._load_state()."""
        with open(self.configfile, 'rb') as f:
            config_loaded = pickle.load(f)
        self._update_config(self.config, config_loaded)

    def _connect_signals(self, device: Device):
        """Connect signal handlers of a device.

        device: the device object, an instance of
            cct.core.devices.device.Device
        """
        self._signalconnections[device.name] = [device.connect('startupdone', self.on_ready),
                                                device.connect('disconnect', self.on_disconnect),
                                                device.connect('telemetry', self.on_telemetry, device.name)]

    def _disconnect_signals(self, device: Device):
        """Disconnect signal handlers from a device."""
        try:
            for c in self._signalconnections[device.name]:
                device.disconnect(c)
            del self._signalconnections[device.name]
        except (AttributeError, KeyError):
            pass

    def get_beamstop_state(self):
        """Check if beamstop is in the beam or not.

        Returns a string:
            'in' if the beamstop motors are at their calibrated in-beam position
            'out' if the beamstop motors are at their calibrated out-of-beam position
            'unknown' otherwise
        """
        bsy = self.motors['BeamStop_Y'].where()
        bsx = self.motors['BeamStop_X'].where()
        if abs(bsx - self.config['beamstop']['in'][0]) < 0.001 and abs(bsy - self.config['beamstop']['in'][1]) < 0.001:
            return 'in'
        if abs(bsx - self.config['beamstop']['out'][0]) < 0.001 and abs(
                        bsy - self.config['beamstop']['out'][1]) < 0.001:
            return 'out'
        return 'unknown'

    @property
    def connect_devices(self):
        """Try to connect to all devices. Send error logs on failures. Return
        a list of the names of unsuccessfully connected devices."""
        if not self._online:
            logger.info('Not connecting to hardware: we are not on-line.')

        def get_subclasses(cls) -> List:
            """Recursively get a flat list of subclasses of `cls`"""
            scl = []
            scl.append(cls)
            for c in cls.__subclasses__():
                scl.extend(get_subclasses(c))
            return scl

        # get all the device classes, i.e. descendants of Device.
        device_classes = get_subclasses(Device)

        # initialize all the devices: instantiate classes, connect signal handlers,
        # establish connections to the hardware and load state information. The class
        # instances are added to the dict `self.devices`, the keys being those given in
        # cfg['name'].
        unsuccessful = []
        for entryname in self.config['connections']:
            cfg = self.config['connections'][entryname]  # shortcut
            # avoid establishing another connection to the device.
            if cfg['name'] in self.devices:
                logger.warn('Not connecting {} again.'.format(cfg['name']))
                continue
            # get the appropriate class
            cls = [d for d in device_classes if d.__name__ == cfg['classname']]
            assert (len(cls) == 1)  # a single class must exist.
            cls = cls[0]

            dev = cls(cfg['name'])  # instantiate the class
            assert (isinstance(dev, Device))
            self.devices[cfg['name']] = dev
            try:
                # connect signal handlers
                self._connect_signals(dev)
                # connect to the hardware
                if isinstance(cls, Device_ModbusTCP):
                    dev.connect_device(cfg['host'], cfg['port'], cfg['timeout'])
                elif isinstance(cls, Device_TCP):
                    dev.connect_device(cfg['host'], cfg['port'], cfg['timeout'], cfg['poll_timeout'])
                else:
                    raise TypeError(type(dev))
                # load the state
                try:
                    dev._load_state(self.config['devices'][cfg['name']])
                except KeyError:
                    # skip if there is no saved state to the device
                    pass
                self._waiting_for_ready.append(dev.name)
            except DeviceError:
                # on failure of connecting to the hardware, disconnect signal handlers, delete the instance and
                # note the name of this entry for returning to the caller.
                self._disconnect_signals(dev)
                logger.error(
                    'Cannot connect to device {}: {}'.format(cfg['name'], traceback.format_exc()))
                del self.devices[cfg['name']]
                del dev
                unsuccessful.append(cfg['name'])
        # at this point, all devices are initialized. We will create some shortcuts to special devices:
        try:
            self.xray_source = self.devices[self.config['connections']['xray_source']['name']]
        except KeyError:
            pass
        try:
            self.detector = self.devices[self.config['connections']['detector']['name']]
        except KeyError:
            pass
        try:
            self.environmentcontrollers['vacuum'] = self.devices[self.config['connections']['vacuum']['name']]
        except KeyError:
            pass
        try:
            self.environmentcontrollers['temperature'] = self.devices[self.config['connections']['temperature']['name']]
        except KeyError:
            pass

        # Instantiate motors
        for m in self.config['motors']:
            cfg = self.config['motors'][m]
            try:
                self.motors[cfg['name']] = Motor(self.devices[cfg['controller']],
                                                 cfg['index'])
            except KeyError:
                logger.error('Cannot find controller for motor ' + cfg['name'])

        return unsuccessful

    def on_telemetry(self, device, telemetry, devicename):
        self._telemetries[devicename] = telemetry
        self.emit('telemetry', devicename, 'device', telemetry)

    def on_ready(self, device):
        try:
            self._waiting_for_ready.remove(device._instancename)
        except ValueError:
            pass
        if not self._waiting_for_ready:
            self.emit('devices-ready')
            self.webstatefilewriter.write_statusfile()
            logger.debug('All ready.')
        else:
            logger.debug('Waiting for ready: ' + ', '.join(self._waiting_for_ready))

    def on_disconnect(self, device: Device, because_of_failure: bool):
        logger.debug('Device {} disconnected. Because of failure: {}'.format(
            device.name, because_of_failure))
        if device.name in self._waiting_for_ready:
            logger.warning('Not reconnecting to device ' + device.name + ': disconnected while waiting for get ready.')
            self._waiting_for_ready.remove(device.name)
            self.on_ready(device)  # check if all other devices are ready
            return False
        if not device.ready:
            logger.warning('Not reconnecting to device ' + device.name + ': it has disconnected before ready.')
            return False
        if because_of_failure:
            # attempt to reconnect
            self._waiting_for_ready = [w for w in self._waiting_for_ready if w != device.name]
            for i in range(3):
                try:
                    device.reconnect_device()
                    self._waiting_for_ready.append(device.name)
                    return True
                except Exception as exc:
                    logger.warning('Exception while reconnecting to device {}: {}, {}'.format(
                        device.name, exc, traceback.format_exc()))
                    time.sleep(1)  # a blocking sleep. Keep the other parts of this program from accessing the device.
            if device.name not in self._waiting_for_ready:
                logger.error('Cannot reconnect to device ' + device.name + '.')
            return False

    def create_services(self):
        self.interpreter = Interpreter(self)
        self.interpreter._load_state(self.config['services']['interpreter'])
        self.filesequence = FileSequence(self)
        self.filesequence._load_state(self.config['services']['filesequence'])
        self.samplestore = SampleStore(self)
        self.samplestore._load_state(self.config['services']['samplestore'])
        self.exposureanalyzer = ExposureAnalyzer(self)
        self.exposureanalyzer._load_state(
            self.config['services']['exposureanalyzer'])
        self.accounting = Accounting(self)
        self.accounting._load_state(self.config['services']['accounting'])
        self.webstatefilewriter = WebStateFileWriter(self)
        self.webstatefilewriter._load_state(self.config['services']['webstatefilewriter'])
        self.services.extend([self.interpreter,
                              self.filesequence,
                              self.samplestore,
                              self.exposureanalyzer,
                              self.accounting,
                              self.webstatefilewriter])

    def on_telemetry_timeout(self):
        """Timer function which periodically requests telemetry from all the
        components."""
        self._telemetries['main'] = get_telemetry()
        for s in ['exposureanalyzer']:
            getattr(self, s).get_telemetry()
        self.emit('telemetry', None, 'service', self.get_telemetry(None))
        return True

    def get_telemetry(self, process=None):
        if process is None:
            tm = {}
            for what in ['self', 'children']:
                tm[what] = DummyTm()
                tm[what].ru_utime = sum([self._telemetries[k][what].ru_utime for k in self._telemetries])
                tm[what].ru_stime = sum([self._telemetries[k][what].ru_stime for k in self._telemetries])
                tm[what].ru_maxrss = sum([self._telemetries[k][what].ru_maxrss for k in self._telemetries])
                tm[what].ru_minflt = sum([self._telemetries[k][what].ru_minflt for k in self._telemetries])
                tm[what].ru_majflt = sum([self._telemetries[k][what].ru_majflt for k in self._telemetries])
                tm[what].ru_inblock = sum([self._telemetries[k][what].ru_inblock for k in self._telemetries])
                tm[what].ru_oublock = sum([self._telemetries[k][what].ru_oublock for k in self._telemetries])
                tm[what].ru_nvcsw = sum([self._telemetries[k][what].ru_nvcsw for k in self._telemetries])
                tm[what].ru_nivcsw = sum([self._telemetries[k][what].ru_nivcsw for k in self._telemetries])
            sm = psutil.swap_memory()
            vm = psutil.virtual_memory()
            la = ', '.join([str(f) for f in os.getloadavg()])
            tm['freephysmem'] = vm.available
            tm['totalphysmem'] = vm.total
            tm['freeswap'] = sm.free
            tm['totalswap'] = sm.total
            tm['loadavg'] = la
        else:
            tm = {'self': self._telemetries[process]['self'],
                  'children': self._telemetries[process]['children'],
                  }
        return tm

    def get_telemetrykeys(self):
        return self._telemetries.keys()

    def on_memlog_timeout(self):
        with open(self.memlog_file, 'at', encoding='utf-8') as f:
            tm = self.get_telemetry()
            s = '{:.3f}\t{:.3f}\t{:.3f}'.format(time.time(),
                                                (datetime.datetime.now() - self._starttime).total_seconds(),
                                                (tm['self'].ru_maxrss + tm[
                                                    'children'].ru_maxrss) * resource.getpagesize() / 1024 ** 2)
            for d in sorted(self.devices):
                try:
                    tm = self.get_telemetry(d)
                    s = '{}\t{:.3f}'.format(s, (
                        tm['self'].ru_maxrss + tm['children'].ru_maxrss) * resource.getpagesize() / 1024 ** 2)
                except KeyError:
                    s = '{}\tNaN'.format(s)
            for d in ['exposureanalyzer']:
                try:
                    tm = self.get_telemetry(d)
                    s = '{}\t{:.3f}'.format(s, (
                        tm['self'].ru_maxrss + tm['children'].ru_maxrss) * resource.getpagesize() / 1024 ** 2)
                except KeyError:
                    s = '{}\tNaN'.format(s)
            f.write(s + '\n')
        return True
