import logging

from PyQt5 import QtWidgets, QtGui

from ...core.mixins import ToolWindow
from ....core.devices import Device
from ....core.instrument.instrument import Instrument
from ....core.instrument.privileges import PRIV_CONNECTDEVICES

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class DeviceConnections(QtWidgets.QWidget, ToolWindow):
    required_privilege = PRIV_CONNECTDEVICES

    def __init__(self, *args, **kwargs):
        credo = kwargs.pop('credo')
        QtWidgets.QWidget.__init__(self, *args, **kwargs)
        self.setupToolWindow(credo)
        self._connectbuttons = {}
        self._disconnectbuttons = {}
        self.setupUi(self)

    @classmethod
    def testRequirements(cls, credo: Instrument):
        return super().testRequirements(credo) and credo.online

    def setupUi(self, Form):
        assert isinstance(self.credo, Instrument)
        self.layout = QtWidgets.QGridLayout(self)
        for i, d in enumerate(sorted(self.credo.devices)):
            dev = self.credo.devices[d]
            assert isinstance(dev, Device)
            self.layout.addWidget(QtWidgets.QLabel(dev.name, self), i, 0)
            self._connectbuttons[d] = QtWidgets.QPushButton('Connect', self)
            self.layout.addWidget(self._connectbuttons[d], i, 1)
            self._connectbuttons[d].clicked.connect(self.onButtonClicked)
            self._disconnectbuttons[d] = QtWidgets.QPushButton('Disconnect', self)
            self.layout.addWidget(self._disconnectbuttons[d], i, 2)
            self._disconnectbuttons[d].clicked.connect(self.onButtonClicked)
        self.setWindowTitle('Connect/disconnect devices')
        self.setWindowIcon(QtGui.QIcon.fromTheme('network-idle'))

    def onButtonClicked(self):
        assert isinstance(self.credo, Instrument)
        for devname in self._disconnectbuttons:
            if self._disconnectbuttons[devname] is self.sender():
                dev = self.credo.get_device(devname)
                assert isinstance(dev, Device)
                try:
                    dev.disconnect_device()
                except Exception as exc:
                    logger.error('Error while disconnecting from device {}: {}'.format(devname, exc))
        for devname in self._connectbuttons:
            if self._connectbuttons[devname] is self.sender():
                dev = self.credo.get_device(devname)
                assert isinstance(dev, Device)
                try:
                    dev.reconnect_device()
                except Exception as exc:
                    logger.error('Error while reconnecting to device {}: {}'.format(devname, exc))
