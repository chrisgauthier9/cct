import sys
import warnings
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np

from ..misc.arithmetic import ArithmeticBase
from ..misc.easylsq import nonlinear_leastsquares, nonlinear_odr
from ..misc.errorvalue import ErrorValue


class Curve(ArithmeticBase):
    _q_rel_tolerance = 0.05  # relative tolerance when comparing two q values: if 2*(q1-q2)/(q1+q2) > _q_rel_tolerance then q1 ~= q2

    def __init__(self, q: np.ndarray, Intensity: np.ndarray, Error: Optional[np.ndarray] = None,
                 qError: Optional[np.ndarray] = None):
        self.q = q
        assert (Intensity.shape == q.shape)
        self.Intensity = Intensity
        if Error is None:
            Error = np.zeros_like(self.q)
        assert (Error.shape == q.shape)
        self.Error = Error
        if qError is None:
            qError = np.zeros_like(self.q)
        assert qError.shape == q.shape
        self.qError = qError

    def trim(self, qmin=None, qmax=None, Imin=None, Imax=None, isfinite=True):
        idx = np.ones(self.q.shape, np.bool)
        if qmin is not None:
            idx &= (self.q >= qmin)
        if qmax is not None:
            idx &= (self.q <= qmax)
        if Imin is not None:
            idx &= (self.Intensity >= Imin)
        if Imax is not None:
            idx &= (self.Intensity <= Imax)
        if isfinite:
            idx &= np.isfinite(self.q) & np.isfinite(self.Intensity)
        return type(self)(self.q[idx], self.Intensity[idx],
                          self.Error[idx], self.qError[idx])

    def fit(self, fitfunction, parinit, *args, **kwargs):
        result = list(nonlinear_leastsquares(self.q, self.Intensity, self.Error, fitfunction, parinit, *args, **kwargs))
        result.append(type(self)(self.q, result[-1]['func_eval'], np.zeros_like(self.q), np.zeros_like(self.q)))
        return result

    def odr(self, fitfunction, parinit, *args, **kwargs):
        result = list(
            nonlinear_odr(self.q, self.Intensity, self.qError, self.Error, fitfunction, parinit, *args, **kwargs))
        result.append(type(self)(self.q, result[-1]['func_eval'], np.zeros_like(self.q), np.zeros_like(self.q)))
        return result

    def peakfit(self, peaktype='Gaussian'):
        raise NotImplementedError

    def sanitize(self):
        idx = (self.q > 0) & np.isfinite(self.Intensity) & np.isfinite(self.q)
        return type(self)(self.q[idx], self.Intensity[idx], self.Error[idx], self.qError[idx])

    def __len__(self):
        return len(self.q)

    def loglog(self, *args, **kwargs):
        if 'axes' in kwargs:
            ax = kwargs['axes']
            del kwargs['axes']
        else:
            ax = plt.gca()
        c = self.trim(qmin=sys.float_info.epsilon, Imin=sys.float_info.epsilon, isfinite=True)
        return ax.loglog(c.q, c.Intensity, *args, **kwargs)

    def plot(self, *args, **kwargs):
        if 'axes' in kwargs:
            ax = kwargs['axes']
            del kwargs['axes']
        else:
            ax = plt.gca()
        c = self.trim(isfinite=True)
        return ax.plot(c.q, c.Intensity, *args, **kwargs)

    def semilogx(self, *args, **kwargs):
        if 'axes' in kwargs:
            ax = kwargs['axes']
            del kwargs['axes']
        else:
            ax = plt.gca()
        c = self.trim(qmin=sys.float_info.epsilon, isfinite=True)
        return ax.semilogx(c.q, c.Intensity, *args, **kwargs)

    def semilogy(self, *args, **kwargs):
        if 'axes' in kwargs:
            ax = kwargs['axes']
            del kwargs['axes']
        else:
            ax = plt.gca()
        c = self.trim(Imin=sys.float_info.epsilon, isfinite=True)
        return ax.semilogy(c.q, c.Intensity, *args, **kwargs)

    def errorbar(self, *args, **kwargs):
        if 'axes' in kwargs:
            ax = kwargs['axes']
            del kwargs['axes']
        else:
            ax = plt.gca()
        return ax.errorbar(self.q, self.Intensity, self.Error, self.qError, *args, **kwargs)

    def _check_q_compatible(self, other):
        if len(self) != len(other):
            raise ValueError('Curves have different lengths')
        if not all([x < self._q_rel_tolerance for x in np.abs(self.q - other.q) / np.abs(self.q + other.q) * 2]):
            raise ValueError('Some of the q values differ more than %.2f %%' % (self._q_rel_tolerance * 100))

    def __iadd__(self, other):
        if isinstance(other, Curve):
            self._check_q_compatible(other)
            self.q = 0.5 * (self.q + other.q)
            self.qError = (self.qError ** 2 + other.qError ** 2) ** 0.5 / 4.
            self.Intensity = self.Intensity + other.Intensity
            self.Error = (self.Error ** 2 + other.Error ** 2) ** 0.5
        elif isinstance(other, ErrorValue):
            self.Intensity = self.Intensity + other.val
            self.Error = (self.Error ** 2 + other.err ** 2) ** 0.5
        elif isinstance(other, float) or isinstance(other, int) or isinstance(other, np.ndarray):
            self.Intensity = self.Intensity + other
        else:
            return NotImplemented

    def __imul__(self, other):
        if isinstance(other, Curve):
            self._check_q_compatible(other)
            self.q = 0.5 * (self.q + other.q)
            self.qError = (self.qError ** 2 + other.qError ** 2) ** 0.5 / 4.
            self.Error = (self.Error ** 2 * other.Intensity ** 2 + other.Error ** 2 * self.Intensity ** 2) ** 0.5
            self.Intensity = self.Intensity * other.Intensity
        elif isinstance(other, ErrorValue):
            self.Intensity = self.Intensity * other.val
            self.Error = (self.Error ** 2 * other.val ** 2 + self.Intensity ** 2 * other.err ** 2) ** 0.5
        elif isinstance(other, float) or isinstance(other, int) or isinstance(other, np.ndarray):
            self.Intensity = self.Intensity * other
        else:
            return NotImplemented

    def __reciprocal__(self):
        return type(self)(self.q, 1 / self.Intensity, self.Error / self.Intensity ** 2, self.qError)

    def __neg__(self):
        return type(self)(self.q, -self.Intensity, self.Error, self.qError)

    def save(self, filename):
        data = np.stack((self.q, self.Intensity, self.Error, self.qError), 1)
        np.savetxt(filename, data, header='q\tIntensity\tError\tqError')

    def __getitem__(self, item):
        return type(self)(self.q[item], self.Intensity[item], self.Error[item], self.qError[item])

    def interpolate(self, newq, **kwargs):
        return type(self)(newq,
                          np.interp(newq, self.q, self.Intensity, **kwargs),
                          np.interp(newq, self.q, self.Error, **kwargs),
                          np.interp(newq, self.q, self.qError, **kwargs))

    @classmethod
    def merge(cls, first, last, qsep=None):
        if not (isinstance(first, cls) and isinstance(last, cls)):
            raise ValueError('Cannot merge types %s and %s together, only %s is supported.' % (
                type(first), type(last), cls))
        if qsep is not None:
            first = first.trim(qmax=qsep)
            last = last.trim(qmin=qsep)
        data = np.concatenate(first.as_structarray(), last.as_structarray())
        data = np.sort(data, order='q')
        return cls(data['q'], data['Intensity'], data['Error'], data['qError'])

    def as_structarray(self):
        data = np.zeros(len(self), dtype=[('q', np.double),
                                          ('Intensity', np.double),
                                          ('Error', np.double),
                                          ('qError', np.double)])
        data['q'] = self.q
        data['Intensity'] = self.Intensity
        data['Error'] = self.Error
        data['qError'] = self.qError
        return data

    def scalefactor(self, other, qmin=None, qmax=None, Npoints=None):
        """Calculate a scaling factor, by which this curve is to be multiplied to best fit the other one.

        Inputs:
            other: the other curve (an instance of GeneralCurve or of a subclass of it)
            qmin: lower cut-off (None to determine the common range automatically)
            qmax: upper cut-off (None to determine the common range automatically)
            Npoints: number of points to use in the common x-range (None defaults to the lowest value among
                the two datasets)

        Outputs:
            The scaling factor determined by interpolating both datasets to the same abscissa and calculating
                the ratio of their integrals, calculated by the trapezoid formula. Error propagation is
                taken into account.
        """
        if qmin is None:
            qmin = max(self.q.min(), other.q.min())
        if qmax is None:
            xmax = min(self.q.max(), other.q.max())
        data1 = self.trim(qmin, qmax)
        data2 = other.trim(qmin, qmax)
        if Npoints is None:
            Npoints = min(len(data1), len(data2))
        commonx = np.linspace(
                max(data1.q.min(), data2.q.min()), min(data2.q.max(), data1.q.max()), Npoints)
        data1 = data1.interpolate(commonx)
        data2 = data2.interpolate(commonx)
        return nonlinear_odr(data1.Intensity, data2.Intensity, data1.Error, data2.Error, lambda x, a: a * x, [1])[0]

    def unite(self, other, qmin=None, qmax=None, qsep=None,
              Npoints=None, scaleother=True, verbose=False, return_factor=False):
        if not isinstance(other, type(self)):
            raise ValueError(
                    'Argument `other` should be an instance of class %s' % type(self))
        if scaleother:
            factor = other.scalefactor(self, qmin, qmax, Npoints)
            retval = type(self).merge(self, factor * other, qsep)
        else:
            factor = self.scalefactor(other, qmin, qmax, Npoints)
            retval = type(self).merge(factor * self, other, qsep)
        if verbose:
            print("Uniting two datasets.")
            print("   xmin   : ", qmin)
            print("   xmax   : ", qmax)
            print("   xsep   : ", qsep)
            print("   Npoints: ", Npoints)
            print("   Factor : ", factor)
        if return_factor:
            return retval, factor
        else:
            return retval

    @classmethod
    def average(cls, *curves):
        q = np.stack([c.q for c in curves], axis=1)
        I = np.stack([c.Intensity for c in curves], axis=1)
        dq = np.stack([c.qError for c in curves], axis=1)
        dI = np.stack([c.Error for c in curves], axis=1)
        # the biggest problem here is qError==0 or Error==0
        for a, name in [(dq, 'q'), (dI, 'I')]:
            if (a == 0).sum():
                warnings.warn('Some %s errors are zeros, trying to fix them.' % name)
                for i in range(q.shape[1]):
                    try:
                        a[i, :][a[i, :] == 0] = a[i, :][a[i, :] != 0].mean()
                    except:
                        a[i, :][a[i, :] == 0] = 1
        I = (I / dI ** 2).sum(axis=1) / (1 / dI ** 2).sum(axis=1)
        dI = 1 / (1 / dI ** 2).sum(axis=1) ** 0.5
        q = (q / dq ** 2).sum(axis=1) / (q / dq ** 2).sum(axis=1)
        dq = 1 / (1 / dq ** 2).sum(axis=1) ** 0.5
        return cls(q, I, dI, dq)
