import time

import numpy as np
from PyQt5 import QtWidgets, QtCore
from matplotlib.axes import Axes
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT, FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from sastool.misc.basicfit import findpeak_single

from .scangraph_ui import Ui_MainWindow
from .signalsmodel import SignalModel


class ScanGraph(QtWidgets.QMainWindow, Ui_MainWindow):
    def __init__(self, *args, **kwargs):
        QtWidgets.QMainWindow.__init__(self, *args, **kwargs)
        self._data = None
        self._cursorposition = 0
        self.setupUi(self)

    def setupUi(self, MainWindow):
        Ui_MainWindow.setupUi(self, MainWindow)
        self.figure = Figure()
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.figureContainer.layout().insertWidget(0, self.canvas)
        self.canvas.setSizePolicy(
            QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding))
        self.figuretoolbar = NavigationToolbar2QT(self.canvas, self.figureContainer)
        self.figureContainer.layout().insertWidget(0, self.figuretoolbar)
        self.axes = self.figure.add_subplot(1, 1, 1)
        self.axes.grid(True, which='both')
        self._curvehandles = []
        self.cursorLeftButton.clicked.connect(
            lambda: self.cursorSlider.triggerAction(self.cursorSlider.SliderSingleStepSub))
        self.cursorRightButton.clicked.connect(
            lambda: self.cursorSlider.triggerAction(self.cursorSlider.SliderSingleStepAdd))
        self.cursorHomeButton.clicked.connect(
            lambda: self.cursorSlider.triggerAction(self.cursorSlider.SliderToMinimum))
        self.cursorEndButton.clicked.connect(lambda: self.cursorSlider.triggerAction(self.cursorSlider.SliderToMaximum))
        self.cursorSlider.valueChanged.connect(self.cursorMoved)
        self.actionCursor_to_Maximum.triggered.connect(self.cursorToMaximum)
        self.actionCursor_to_Minimum.triggered.connect(self.cursorToMinimum)
        self.actionFit_negative_Gaussian.triggered.connect(lambda: self.fit('Gaussian', -1))
        self.actionFit_positive_Gaussian.triggered.connect(lambda: self.fit('Gaussian', +1))
        self.actionFit_negative_Lorentzian.triggered.connect(lambda: self.fit('Lorentzian', -1))
        self.actionFit_positive_Lorentzian.triggered.connect(lambda: self.fit('Lorentzian', +1))
        self.actionAutoScale.toggled.connect(self.autoscale)
        self.actionShowLegend.toggled.connect(self.drawLegend)
        self.hideAllButton.clicked.connect(lambda: self.model.setVisible(None, False))
        self.showAllButton.clicked.connect(lambda: self.model.setVisible(None, True))
        self.actionReplot.triggered.connect(self.replot)
        self.setCursorRange()

    def fit(self, functiontype, sign):
        xmin, xmax, ymin, ymax = self.axes.axis()
        x = self._data[self.abscissaName()]
        y = self._data[self.selectedSignal()]
        idx = np.logical_and(np.logical_and(x >= xmin, x <= xmax),
                             np.logical_and(y >= ymin, y <= ymax))
        if idx.sum() < 5:
            QtWidgets.QMessageBox.critical(
                self, "Fitting error",
                "Error while peak fitting: not enough points in the current view "
                "from the currently selected signal ({})".format(self.selectedSignal()))
            return
        pos, hwhm, baseline, amplitude = findpeak_single(x[idx], y[idx], curve=functiontype, signs=(sign,))
        x = np.linspace(x.min(), x.max(), 1000)
        if functiontype == 'Gaussian':
            y = amplitude.val * np.exp(-0.5 * (x - pos.val) ** 2 / hwhm.val ** 2) + baseline.val
        elif functiontype == 'Lorentzian':
            y = amplitude.val * hwhm.val ** 2 / (hwhm.val ** 2 + (pos.val - x) ** 2) + +baseline.val
        for attr in ['_fitcurvehandle', '_peaktexthandle']:
            try:
                getattr(self, attr).remove()
                delattr(self, attr)
            except AttributeError:
                pass
        self._fitcurvehandle = self.axes.plot(x, y, 'r--')[0]
        if sign < 0:
            va = 'top'
        if sign > 0:
            va = 'bottom'
        self._peaktexthandle = self.axes.text(pos.val, baseline.val + amplitude.val, pos.tostring(), ha='center', va=va)
        self._lastpeakposition = pos
        self.canvas.draw_idle()

    def drawLegend(self):
        if self.actionShowLegend.isChecked():
            handles = [c for c in self._curvehandles if self.model.visible(c.get_label())]
            labels = [c.get_label() for c in handles]
            self.axes.legend(handles, labels, loc='best')
        else:
            self.axes.legend().remove()
        self.canvas.draw_idle()

    def autoscale(self):
        if self.actionAutoScale.isChecked():
            self.axes.relim(True)
            self.axes.autoscale_view(True, True, True)
            self.canvas.draw_idle()

    def cursorToMaximum(self):
        print(self.selectedSignal())
        self.cursorSlider.setValue(np.argmax(self._data[self.selectedSignal()]))

    def cursorToMinimum(self):
        print(self.selectedSignal())
        self.cursorSlider.setValue(np.argmin(self._data[self.selectedSignal()]))

    def selectedSignal(self):
        return self._data.dtype.names[1:][self.signalsTreeView.selectionModel().selectedRows()[0].row()]

    def signalsTreeViewSelectionChanged(self, current: QtCore.QModelIndex,
                                        previous: QtCore.QModelIndex):
        for h in self._curvehandles:
            h.set_lw(1)
        self._curvehandles[current.row()].set_lw(3)
        self.drawLegend()
        self.canvas.draw_idle()

    def cursorMoved(self, position):
        self.cursorLeftButton.setEnabled(position > 0)
        self.cursorRightButton.setEnabled(position < self._datalength - 1)
        self.cursorHomeButton.setEnabled(position > 0)
        self.cursorEndButton.setEnabled(position < self._datalength - 1)
        self.drawScanCursor()

    def drawScanCursor(self):
        if self.isScanRunning():
            return False
        assert isinstance(self.axes, Axes)
        cursorposition = self.cursorSlider.value()
        if hasattr(self, '_cursorhandle'):
            self._cursorhandle.remove()
            del self._cursorhandle
        self._cursorhandle = self.axes.axvline(self._data[self.abscissaName()][cursorposition], color='black',
                                               alpha=0.8, lw=3)
        self.requestRedraw()

    def replot(self):
        t0 = time.monotonic()
        if self._data is None:
            return False
        assert isinstance(self._data, np.ndarray)
        # self.axes.set_color_cycle(None)
        if hasattr(self, '_fitcurvehandle'):
            self._fitcurvehandle.remove()
            del self._fitcurvehandle
        if hasattr(self, '_peaktexthandle'):
            self._peaktexthandle.remove()
            del self._peaktexthandle
        if not hasattr(self, '_curvehandles'):
            self._curvehandles = []
        if not self._curvehandles:
            for signal in self._data.dtype.names[1:]:
                self._curvehandles.extend(
                    self.axes.plot(self._data[self.abscissaName()][:self._datalength],
                                   self._data[signal][:self._datalength], '.-', label=signal))
        else:
            abscissaname = self.abscissaName()
            abscissa = self._data[abscissaname][:self._datalength]
            for signal, handle in zip(self._data.dtype.names[1:], self._curvehandles):
                handle.set_xdata(abscissa)
                handle.set_ydata(self._data[signal][:self._datalength] * self.model.factor(signal))
                handle.set_lw(1)
        try:
            self._curvehandles[self.signalsTreeView.selectedIndexes()[0].row()].set_lw(3)
        except IndexError:
            pass
        self.autoscale()
        self.drawLegend()
        self.drawScanCursor()
        self.requestRedraw()
        print('Replot took {} seconds'.format(time.monotonic() - t0))

    def setCurvesVisibility(self):
        for c in self._curvehandles:
            assert isinstance(c, Line2D)
            c.set_visible(self.model.visible(c.get_label()))
        self.drawLegend()
        self.autoscale()
        self.requestRedraw()

    def setCursorRange(self):
        self.cursorContainer.setEnabled(self._data is not None)
        self.cursorContainer.setVisible(self._data is not None)
        if self._data is None:
            return
        self.cursorSlider.setMinimum(0)
        self.cursorSlider.setMaximum(self._datalength - 1)
        self.cursorSlider.setValue((self._datalength - 1) // 2)

    def setCurve(self, scandata: np.ndarray, datalength: int = None):
        """Set the scan data.

        Inputs:
            scandata: np.ndarray
                A one-dimensional, structured numpy array. The field names are
                the labels of the individual signals. The first field is the
                abscissa.
            datalength: positive integer
                The expected length of the data. If None, it will be set to the
                number of elements in scandata.
                If this instance of ScanGraph is meant to represent a scan
                currently in progress, scandata must be an array with enough
                space to fit all scan points acquired during the measurement,
                and datalength must be the number of already acquired points,
                zero if the scan has just been started and no point has been
                recorded yet. `datalength > len(scandata)` is an error.
        """
        if datalength is None:
            datalength = len(scandata)
        if datalength > len(scandata):
            raise ValueError(
                'Argument `datalength` must not be larger than the number of points in argument `scandata`.')
        self._data = scandata
        self._datalength = datalength
        self.axes.set_xlabel(self.abscissaName())
        self.setCursorRange()
        self.model = SignalModel(signals=self._data.dtype.names[1:])
        self.signalsTreeView.setModel(self.model)
        self.model.dataChanged.connect(self.signalsTreeModelChanged)
        self.signalsTreeView.selectionModel().currentChanged.connect(self.signalsTreeViewSelectionChanged)
        self.signalsTreeView.selectionModel().select(self.model.index(0, 0),
                                                     QtCore.QItemSelectionModel.SelectCurrent | QtCore.QItemSelectionModel.Rows)
        self.setCursorRange()
        self.replot()

    def signalsTreeModelChanged(self, idxfrom: QtCore.QModelIndex, idxto: QtCore.QModelIndex):
        print('dataChanged from signalsTreeModel: from ({}, {}) to ({}, {})'.format(
            idxfrom.row(), idxfrom.column(), idxto.row(), idxto.column()))
        if idxfrom.column() == 0 or idxto.column() == 0:
            self.setCurvesVisibility()
        if idxfrom.column() == 1 or idxto.column() == 1:
            self.replot()
        return True

    def appendScanPoint(self, scanpoint):
        """Append a newly acquired scan point.

        Inputs:
            scanpoint: tuple
                A tuple carrying the newly registered values for all signals,
                including the abscissa."""
        if self._datalength >= len(self._data):
            raise ValueError('Cannot append to scan curve: no space left in the array.')
        self._data[self._datalength] = scanpoint
        self._datalength += 1
        if not self.isScanRunning():
            self.truncateScan()
        self.replot()

    def truncateScan(self):
        """Remove unused space from the data array, thus considering this scan
        finished.
        """
        self._data = self._data[:self._datalength]
        self.setCursorRange()

    def abscissaName(self):
        return self._data.dtype.names[0]

    def isScanRunning(self):
        return self._datalength < len(self._data)

    def __len__(self):
        return self._datalength

    def requestRedraw(self):
        self.canvas.draw_idle()