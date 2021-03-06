import logging
from typing import Optional, Tuple

import adjustText
import numpy as np
from PyQt5 import QtWidgets
from matplotlib.axes import Axes
from matplotlib.backend_bases import ResizeEvent, MouseEvent, PickEvent
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.text import Text

from .samplepositionchecker_ui import Ui_Form
from .sampleselectorlist import SampleSelectorModel
from ...core.mixins import ToolWindow
from ....core.services.samples import SampleStore, Sample

logger=logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class SamplePositionChecker(QtWidgets.QWidget, Ui_Form, ToolWindow):

    def __init__(self, *args, **kwargs):
        credo = kwargs.pop('credo')
        QtWidgets.QWidget.__init__(self, *args, **kwargs)
        self._connections = []
        self._canvas_connections = []
        self._picked_index = None
        self._samplepos_cache = None
        self._markers = None
        self._texts = None
        self._snapxline = None
        self._snapyline = None
        self.setupToolWindow(credo)
        self.setupUi(self)

    def setupUi(self, Form):
        Ui_Form.setupUi(self, Form)
        self.model = SampleSelectorModel(self.credo)
        self.treeView.setModel(self.model)
        self.figure = Figure(figsize=(4,4))
        self.axes = self.figure.add_subplot(1, 1, 1)
        self.canvas = FigureCanvasQTAgg(self.figure)
        #self.canvas.setMinimumSize(300, 300)
        self.canvas.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.MinimumExpanding)
        self._canvas_connections=[
            self.canvas.mpl_connect('resize_event', self.onCanvasResize),
            self.canvas.mpl_connect('pick_event', self.onCanvasPick),
            self.canvas.mpl_connect('button_release_event', self.onCanvasButtonRelease),
            self.canvas.mpl_connect('motion_notify_event', self.onCanvasMotionNotify),
                                  ]
        self.navtoolbar = NavigationToolbar2QT(self.canvas, None)
        self.verticalLayout.addWidget(self.canvas, 1)
        self.verticalLayout.addWidget(self.navtoolbar)
        self.showLabelsToolButton.toggled.connect(self.onShowLabels)
        ss = self.credo.services['samplestore']
        assert isinstance(ss, SampleStore)
        self._connections = [ss.connect('list-changed', self.onSampleListChanged)]
        self.model.dataChanged.connect(self.replot)
        self.model.modelReset.connect(self.replot)
        self.labelSizeHorizontalSlider.valueChanged.connect(self.replot)
        self.upsideDownToolButton.toggled.connect(self.replot)
        self.rightToLeftToolButton.toggled.connect(self.replot)
        self.loadPersistence()
        self.replot()

    def savePersistence(self):
        self.credo.savePersistence(
            'samplepositionchecker',
            {'show_labels':self.showLabelsToolButton.isChecked(),
             'rtl':self.rightToLeftToolButton.isChecked(),
             'upsidedown':self.upsideDownToolButton.isChecked(),
             'samples':self.model.getSelected()
             })

    def onCanvasPick(self, event:PickEvent):
        logger.debug('Event.ind is: {}, type: {}'.format(event.ind, type(event.ind)))
        logger.debug('onCanvasPick({})'.format(self.model.getSelected()[event.ind[0]]))
        if not self.enableDragSamplesToolButton.isChecked():
            return
        logger.debug('onCanvasPick({}) continuing'.format(self.model.getSelected()[event.ind[0]]))
        line=event.artist
        self._picked_index = event.ind[0]

    def onCanvasButtonRelease(self, event:MouseEvent):
        logger.debug('onCanvasButtonRelease({}, {}, {})'.format(event.xdata, event.ydata, event.button))
        if not (self.enableDragSamplesToolButton.isChecked() and self._picked_index is not None):
            return
        logger.debug('onCanvasButtonRelease({}, {}, {}) continuing'.format(event.xdata, event.ydata, event.button))
        if event.inaxes is self.axes:
            ss=self.credo.services['samplestore']
            assert isinstance(ss, SampleStore)
            sam=ss.get_sample(self.model.getSelected()[self._picked_index])
            assert isinstance(sam, Sample)
            xsnap, ysnap = self.getSnapCoordinates(event.x, event.y)
            sam.positionx.val=xsnap if xsnap is not None else event.xdata
            sam.positiony.val=ysnap if ysnap is not None else event.ydata
            ss.set_sample(sam.title, sam)

        self._picked_index = None
        self._snapxline = None
        self._snapyline = None
        self.replot()

    def snapRadius(self):
        return 3

    def getSnapCoordinates(self, cursorx:float, cursory:float) -> Tuple[Optional[float],Optional[float]]:
        xdata,ydata=self._markers.get_data()
        xdata=[x for i,x in enumerate(xdata) if i!=self._picked_index]
        ydata=[y for i,y in enumerate(ydata) if i!=self._picked_index]
        cdisp=self.axes.transData.transform(np.vstack((xdata,ydata)).T)
        xsnapindex, xsnapdistance = sorted(enumerate(cdisp[:,0]),key=lambda ix:abs(ix[1]-cursorx))[0]
        ysnapindex, ysnapdistance = sorted(enumerate(cdisp[:,1]),key=lambda iy:abs(iy[1]-cursory))[0]
        xpos=None
        ypos=None
        if self.snapXToolButton.isChecked() and abs(xsnapdistance-cursorx)<self.snapRadius():
            xpos = xdata[xsnapindex]
        if self.snapYToolButton.isChecked() and abs(ysnapdistance-cursory)<self.snapRadius():
            ypos = ydata[ysnapindex]
        return xpos, ypos


    def onCanvasMotionNotify(self, event:MouseEvent):
        if not (self.enableDragSamplesToolButton.isChecked() and self._picked_index is not None):
            return
        if event.inaxes is not self.axes:
            return
        logger.debug('onCanvasMotionNotify({}, {}, {})'.format(event.xdata, event.ydata, event.button))
        if self._snapxline is not None:
            try:
                self._snapxline.remove()
            except ValueError:
                pass
        if self._snapyline is not None:
            try:
                self._snapyline.remove()
            except ValueError:
                pass
        snapx, snapy = self.getSnapCoordinates(event.x, event.y)
        if snapx is not None:
            self._snapxline=self.axes.axvline(snapx, linestyle='--',color='black',linewidth=0.7)
        else:
            snapx = event.xdata
        if snapy is not None:
            self._snapyline=self.axes.axhline(snapy, linestyle='--',color='black',linewidth=0.7)
        else:
            snapy = event.ydata
        xdata,ydata=self._markers.get_data()
        xdata[self._picked_index]=snapx
        ydata[self._picked_index]=snapy
        self._markers.set_data(xdata,ydata)
        if self._texts is not None:
            t=self._texts[self._picked_index]
            assert isinstance(t, Text)
            t.set_position([snapx,snapy])
            adjustText.adjust_text(self._texts)
        self.canvas.draw_idle()

    def loadPersistence(self):
        data=self.credo.loadPersistence('samplepositionchecker')
        if not data:
            return
        try:
            self.showLabelsToolButton.setChecked(data['show_labels'])
        except KeyError:
            pass
        try:
            self.rightToLeftToolButton.setChecked(data['rtl'])
        except KeyError:
            pass
        try:
            self.upsideDownToolButton.setChecked(data['upsidedown'])
        except KeyError:
            pass
        try:
            self.model.setSelected(data['samples'])
        except KeyError:
            pass

    def onCanvasResize(self, event:ResizeEvent):
        logger.debug('onCanvasResize()')
        self.figure.tight_layout()
        adjustText.adjust_text(self._texts)
        self.canvas.draw()

    def cleanup(self):
        for c in self._connections:
            self.credo.services['samplestore'].disconnect(c)
        self._connections = []
        for c in self._canvas_connections:
            self.canvas.mpl_disconnect(c)
        self._canvas_connections = []
        super().cleanup()

    def onSampleListChanged(self, ss: SampleStore):
        self.model.update()
        self.replot()
        return False

    def onShowLabels(self):
        self.replot()

    def replot(self):
        assert isinstance(self.axes, Axes)
        self.axes.clear()
        for c in range(self.treeView.model().columnCount()):
            self.treeView.resizeColumnToContents(c)
        try:
            xmin = self.credo.motors['Sample_X'].get_variable('softleft')
            ymin = self.credo.motors['Sample_Y'].get_variable('softleft')
            xmax = self.credo.motors['Sample_X'].get_variable('softright')
            ymax = self.credo.motors['Sample_Y'].get_variable('softright')
            self.axes.add_patch(Rectangle([xmin, ymin], xmax - xmin, ymax - ymin, fill=True, color='lightgray'))
        except KeyError:
            pass
        self.axes.grid(True, which='both')
        self.axes.axis('equal')
        samples = self.model.getSelected()
        if not samples:
            self.canvas.draw()
            return False
        ss = self.credo.services['samplestore']
        assert isinstance(ss, SampleStore)
        self._samplepos_cache = np.array(
            [[s.positionx.val, s.positiony.val, s.positionx.err, s.positiony.err] for s in ss if s.title in samples])
        self._markers=self.axes.plot(self._samplepos_cache[:, 0], self._samplepos_cache[:, 1], 'bo', picker=5)[0]
        if self.showLabelsToolButton.isChecked():
            self._texts=[]
            for s, x, y in zip(samples, self._samplepos_cache[:, 0], self._samplepos_cache[:, 1]):
                self._texts.append(self.axes.text(x, y, ' ' + s + ' ', ha='left', va='center',
                               fontdict={'size': self.labelSizeHorizontalSlider.value()}))
        else:
            self._texts=None
        xmin, xmax, ymin, ymax = self.axes.axis()
        if self.upsideDownToolButton.isChecked():
            self.axes.axis(ymin=max(ymax, ymin), ymax=min(ymin, ymax))
        else:
            self.axes.axis(ymin=min(ymax, ymin), ymax=max(ymin, ymax))
        if self.rightToLeftToolButton.isChecked():
            self.axes.axis(xmin = max(xmax, xmin), xmax = min(xmax, xmin))
        else:
            self.axes.axis(xmin=min(xmax, xmin), xmax=max(xmin, xmax))
        self.figure.tight_layout()
        self.axes.relim()
        self.axes.autoscale_view()
        adjustText.adjust_text(self._texts)
        self.canvas.draw()
