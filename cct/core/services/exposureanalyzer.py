import logging
import multiprocessing
import os
import pickle
import queue
import traceback

import numpy as np
from gi.repository import GLib, GObject
from sastool.io.twodim import readcbf
from sastool.misc.easylsq import nonlinear_odr
from scipy.io import loadmat

from .service import Service, ServiceError
from ..utils.errorvalue import ErrorValue
from ..utils.geometrycorrections import solidangle, angledependentabsorption, angledependentairtransmission
from ..utils.io import write_legacy_paramfile
from ..utils.pathutils import find_in_subfolders
from ..utils.sasimage import SASImage

logger=logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class DataReductionEnd(Exception):
    pass


def flatten_dict(d):
    flat = {}
    for k in d:
        if not isinstance(d[k], dict):
            flat[k] = d[k]
        else:
            d1 = flatten_dict(d[k])
            for k1 in d1:
                flat[k + '.' + k1] = d1[k1]
    return flat


def get_statistics(matrix, masktotal=None, mask=None):
    """Calculate different statistics of a detector image, such as sum, max,
    center of gravity, etc."""
    assert (isinstance(matrix, np.ndarray))
    if mask is None:
        mask = 1
    if masktotal is None:
        masktotal = 1
    result = {}
    matrixorig = matrix
    for prefix, mask in [('total_', masktotal), ('', mask)]:
        matrix = matrixorig * mask
        x = np.arange(matrix.shape[0])
        y = np.arange(matrix.shape[1])
        result[prefix + 'sum'] = (matrix).sum()
        result[prefix + 'max'] = (matrix).max()
        result[prefix + 'beamx'] = (matrix * x[:, np.newaxis]).sum() / result[prefix + 'sum']
        result[prefix + 'beamy'] = (matrix * y[np.newaxis, :]).sum() / result[prefix + 'sum']
        result[prefix + 'sigmax'] = (
                                        (matrix * (x[:, np.newaxis] - result[prefix + 'beamx']) ** 2).sum() /
                                        result[prefix + 'sum']) ** 0.5
        result[prefix + 'sigmay'] = (
                                        (matrix * (y[np.newaxis, :] - result[prefix + 'beamy']) ** 2).sum() /
                                        result[prefix + 'sum']) ** 0.5
        result[prefix + 'sigma'] = (result[prefix + 'sigmax'] ** 2 + result[prefix + 'sigmay'] ** 2) ** 0.5
    return result


class ExposureAnalyzer(Service):
    """This service works as a separate process. Every time a new exposure is
    finished, it must be `.submit()`-ted to this process, which will carry out
    computationally more intensive tasks in the background. The tasks to be
    done depend on the filename prefix:

    crd: carry out the on-line data reduction on the image
    scn: calculate various statistics on the image and return a tuple of them,
        to be added to the scan dataset
    """
    __gsignals__ = {
        'error': (GObject.SignalFlags.RUN_FIRST, None, (str, int, object, str)),
        'scanpoint': (GObject.SignalFlags.RUN_FIRST, None, (str, int, object)),
        'datareduction-done': (GObject.SignalFlags.RUN_FIRST, None, (str, int, object)),
        'transmdata': (GObject.SignalFlags.RUN_FIRST, None, (str, int, object)),
        'image': (GObject.SignalFlags.RUN_FIRST, None, (str, int, object, object, object)),
        'idle': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, *args, **kwargs):
        Service.__init__(self, *args, **kwargs)
        self._backendprocess = multiprocessing.Process(
            target=self._backgroundworker, daemon=True)
        self._queue_to_backend = multiprocessing.Queue()
        self._queue_to_frontend = multiprocessing.Queue()
        self._handler = GLib.idle_add(self._idle_function)
        # A copy of the config hierarchy will be inherited by the back-end
        # process. Note that updates to instrument.config won't affect us,
        # since we are running in a different process
        self._config = self.instrument.config
        self._backendprocess.start()
        self._working=0

    def get_mask(self, maskname):
        if not hasattr(self, '_masks'):
            self._masks = {}
        try:
            return self._masks[maskname]
        except KeyError:
            if not os.path.isabs(maskname):
                filename = find_in_subfolders(self._config['path']['directories']['mask'],
                                              maskname)
            else:
                filename = maskname
            m = loadmat(filename)
            self._masks[maskname] = m[
                [k for k in m.keys() if not k.startswith('__')][0]].view(bool)
            return self._masks[maskname]

    def _backgroundworker(self):
        while True:
            prefix, fsn, filename, args = self._queue_to_backend.get()
#            logger.debug(
#                'Exposureanalyzer background process got work: %s, %d, %s, %s' % (prefix, fsn, filename, str(args)))
            cbfdata = readcbf(
                os.path.join(self._config['path']['directories']['images'], filename))[0]
            if prefix == 'exit':
                break
            elif prefix == self._config['path']['prefixes']['crd']:
                # data reduction needed
                try:
                    mask = self.get_mask(self._config['geometry']['mask'])
                    im = self.datareduction(cbfdata, mask, args[0])
                    self.savecorrected(prefix, fsn, im)
                    self._queue_to_frontend.put_nowait(
                        ((prefix, fsn), 'datareduction-done', (im,)))
                    self._queue_to_frontend.put_nowait(
                        ((prefix, fsn), 'image', (cbfdata, mask) + args))
                except Exception as exc:
                    self._queue_to_frontend.put_nowait(
                        ((prefix, fsn), 'error', (exc, traceback.format_exc())))
                    logger.error('Error in data reduction: %s, %s' % (str(exc), traceback.format_exc()))
            elif prefix == self._config['path']['prefixes']['tra']:
                # transmission measurement
                try:
                    transmmask = self.get_mask(self._config['transmission']['mask'])
                except (IOError, OSError, IndexError) as exc:
                    # could not load a mask file
                    self._queue_to_frontend.put_nowait(
                        ((prefix, fsn), 'error', (exc, traceback.format_exc())))
                else:
                    self._queue_to_frontend.put_nowait(
                        ((prefix, fsn), 'transmdata', args + ((cbfdata * transmmask).sum(),)))
            elif prefix == self._config['path']['prefixes']['scn']:
                try:
                    scanmask = self.get_mask(self._config['scan']['mask'])
                    scanmasktotal = self.get_mask(self._config['scan']['mask_total'])
                except (IOError, OSError, IndexError) as exc:
                    # could not load a mask file
                    self._queue_to_frontend.put_nowait(
                        ((prefix, fsn), 'error', (exc, traceback.format_exc())))
                else:
                    # scan point, we have to calculate something.
                    stat = get_statistics(cbfdata, scanmasktotal, scanmask)
                    stat['FSN'] = fsn
                    resultlist = tuple([args] + [stat[k]
                                                 for k in self._config['scan']['columns']])
                    self._queue_to_frontend.put_nowait(
                        ((prefix, fsn), 'scanpoint', resultlist))
            else:
                try:
                    mask = self.get_mask(self._config['geometry']['mask'])
                except (IOError, OSError, IndexError) as exc:
                    # could not load a mask file
                    self._queue_to_frontend.put_nowait(
                        ((prefix, fsn), 'error', (exc, traceback.format_exc())))
                else:
                    self._queue_to_frontend.put_nowait(
                        ((prefix, fsn), 'image', (cbfdata, mask) + args))

    def _idle_function(self):
        try:
            prefix_fsn, what, arguments = self._queue_to_frontend.get_nowait()
            self._working-=1
        except queue.Empty:
            return True
        if what == 'error':
            self.emit(
                'error', prefix_fsn[0], prefix_fsn[1], arguments[0], arguments[1])
        elif what == 'scanpoint':
#            logger.debug('Emitting scanpoint with arguments: %s'%str(arguments))
            self.emit('scanpoint', prefix_fsn[0], prefix_fsn[1], arguments)
        elif what == 'datareduction':
            self.emit('datareduction-done', prefix_fsn[0], prefix_fsn[1], arguments[0])
        elif what == 'transmdata':
            self.emit('transmdata', prefix_fsn[0], prefix_fsn[1], arguments)
        elif what == 'image':
            self.emit('image', prefix_fsn[0], prefix_fsn[1], arguments[0], arguments[1], arguments[2])
        if self._working==0:
            self.emit('idle')
        return True

    def submit(self, fsn, filename, prefix, args):
#        logger.debug('Submitting to exposureanalyzer: %s, %d, %s, %s'%(prefix,fsn,filename,str(args)))
        self._queue_to_backend.put_nowait((prefix, fsn, filename, args))
        self._working+=1

    def prescaling(self, im):
        im /= im.params['exposure']['exptime']
        transmission = ErrorValue(im.params['sample']['transmission.val'], im.params['sample']['transmission.err'])
        im /= transmission
        im.params['datareduction']['history'].append('Divided by exposure time')
        im.params['datareduction']['history'].append('Divided by transmission: %s' % str(transmission))
        logger.debug('Done prescaling FSN %d' % im.params['exposure']['fsn'])
        return im

    def subtractbackground(self, im):
        if im.params['sample']['title'] == self._config['datareduction']['backgroundname']:
            self._lastbackground = im
            logger.debug('Done bgsub FSN %d: this is background' % im.params['exposure']['fsn'])
            raise DataReductionEnd()
        if (abs(im.params['geometry']['truedistance'] -
                    self._lastbackground.params['geometry']['truedistance']) <
                self._config['datareduction']['distancetolerance']):
            im -= self._lastbackground
            im.params['datareduction']['history'].append(
                'Subtracted background FSN #%d' % self._lastbackground.params['exposure']['fsn'])
            im.params['datareduction']['emptybeamFSN'] = self._lastbackground.params['exposure']['fsn']
        else:
            raise ServiceError('Last seen background measurement does not match the exposure under reduction.')
        logger.debug('Done bgsub FSN %d' % im.params['exposure']['fsn'])
        return im

    def correctgeometry(self, im):
        tth = im.twotheta_rad
        im *= solidangle(tth.val, tth.err, im.params['geometry']['truedistance'],
                         im.params['geometry']['truedistance.err'], im.params['geometry']['pixelsize'])
        im.params['datareduction']['history'].append('Corrected for solid angle')
        im *= angledependentabsorption(tth.val, tth.err, im.params['sample']['transmission.val'],
                                       im.params['sample']['transmission.err'])
        im.params['datareduction']['history'].append('Corrected for angle-dependent absorption')
        if 'vacuum_pressure' in im.params['environment']:
            im *= angledependentairtransmission(tth.val, tth.err, im.params['environment']['vacuum_pressure'],
                                                im.params['geometry']['truedistance'],
                                                im.params['geometry']['truedistance.err'],
                                                self._config['datareduction']['mu_air'],
                                                self._config['datareduction']['mu_air.err'])
            im.params['datareduction']['history'].append(
                'Corrected for angle-dependent air absorption. Pressure: %f mbar' % (
                im.params['environment']['vacuum_pressure']))
        else:
            im.params['datareduction']['history'].append(
                'Skipped angle-dependent air absorption correction: no pressure value.')
        logger.debug('Done correctgeometry FSN %d' % im.params['exposure']['fsn'])
        return im

    def dividebythickness(self, im):
        im /= ErrorValue(im.params['sample']['thickness.val'], im.params['sample']['thickness.err'])
        logger.debug('Done dividebythickness FSN %d' % im.params['exposure']['fsn'])
        return im

    def absolutescaling(self, im):
        if im.params['sample']['title'] == self._config['datareduction']['absintrefname']:
            dataset = np.loadtxt(self._config['datareduction']['absintrefdata'])
            logger.debug('Q-range of absint dataset: %g to %g, %d points.' % (
            dataset[:, 0].min(), dataset[:, 0].max(), len(dataset[:, 0])))
            q, dq, I, dI, area = im.radial_average(qrange=dataset[:, 0], raw_result=True)
            logger.debug(
                'Results of radial average: %s, %s, %s, %s, %s' % (str(q), str(dq), str(I), str(dI), str(area)))
            logger.debug('Sum pixels: ' + str(area.sum()))
            dataset = dataset[area > 0, :]
            I = I[area > 0]
            dI = dI[area > 0]
            q = q[area > 0]
            logger.debug('Common q-range: %g to %g, %d points.' % (q.min(), q.max(), len(q)))
            scalingfactor, stat = nonlinear_odr(I, dataset[:, 1], dI, dataset[:, 2], lambda x, a: a * x, [1])
            scalingfactor = ErrorValue(scalingfactor.val,
                                       scalingfactor.err)  # convert from sastool's ErrorValue to ours
            logger.debug('Scaling factor: %s' % scalingfactor)
            logger.debug('Chi2: %f' % stat['Chi2_reduced'])
            self._lastabsintref = im
            self._absintscalingfactor = scalingfactor
            self._absintstat = stat
            self._absintqrange = q
            im.params['datareduction']['history'].append(
                'Determined absolute intensity scaling factor: %s. Reduced Chi2: %f. DoF: %d. This corresponds to beam flux %s photons*eta/sec' % (
                self._absintscalingfactor, self._absintstat['Chi2_reduced'], self._absintstat['DoF'],
                1 / self._absintscalingfactor))
            logger.debug('History:\n  ' + '\n  '.join(h for h in im.params['datareduction']['history']))
        if abs(im.params['geometry']['truedistance'] - self._lastabsintref.params['geometry']['truedistance']) < \
                self._config['datareduction']['distancetolerance']:
            im *= self._absintscalingfactor
            im.params['datareduction']['history'].append(
                'Using absolute intensity factor %s from measurement FSN #%d for absolute intensity calibration.' % (
                    self._absintscalingfactor, self._lastabsintref.params['exposure']['fsn']))
            im.params['datareduction']['absintrefFSN'] = self._lastabsintref.params['exposure']['fsn']
            im.params['datareduction']['flux'] = (1 / self._absintscalingfactor).val
            im.params['datareduction']['flux.err'] = (1 / self._absintscalingfactor).err
            im.params['datareduction']['absintchi2'] = self._absintstat['Chi2_reduced']
            im.params['datareduction']['absintdof'] = self._absintstat['DoF']
            im.params['datareduction']['absintfactor'] = self._absintscalingfactor.val
            im.params['datareduction']['absintfactor.err'] = self._absintscalingfactor.err
            im.params['datareduction']['absintqmin'] = self._absintqrange.min()
            im.params['datareduction']['absintqmax'] = self._absintqrange.max()
        else:
            raise ServiceError(
                'S-D distance of the last seen absolute intensity reference measurement does not match the exposure under reduction.')
        logger.debug('Done absint FSN %d' % im.params['exposure']['fsn'])
        return im

    def savecorrected(self, prefix, fsn, im):
        npzname = os.path.join(self._config['path']['directories']['eval2d'],
                               prefix + '_%%0%dd' % self._config['path']['fsndigits'] % fsn + '.npz')
        np.savez_compressed(npzname, Intensity=im.val, Error=im.err)
        picklefilename = os.path.join(self._config['path']['directories']['eval2d'],
                                      prefix + '_%%0%dd' % self._config['path']['fsndigits'] % fsn + '.pickle')
        with open(picklefilename, 'wb') as f:
            pickle.dump(im.params, f)
        write_legacy_paramfile(picklefilename[:-len('.pickle')] + '.param', im.params)
        logger.debug('Done savecorrected FSN %d' % im.params['exposure']['fsn'])

    def datareduction(self, intensity, mask, params):
        im = SASImage(intensity, intensity ** 0.5, params, mask)
        im.params['datareduction'] = {'history': []}
        try:
            self.prescaling(im)
            self.subtractbackground(im)
            self.correctgeometry(im)
            self.dividebythickness(im)
            self.absolutescaling(im)
        except DataReductionEnd:
            pass
        return im
