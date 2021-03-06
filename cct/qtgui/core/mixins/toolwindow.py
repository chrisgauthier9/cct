import logging
import weakref

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
from ....core.instrument.instrument import Instrument
from ....core.services.interpreter import Interpreter
from ....core.commands import Command
from ....core.instrument.privileges import PRIV_LAYMAN
from ....core.devices import Device, Motor
from typing import Union, Type, Any, Optional
from PyQt5 import QtCore, QtGui, QtWidgets


class ToolWindow(object):
    required_devices = []
    required_privilege = PRIV_LAYMAN
    classname = None

    def setupToolWindow(self, credo:Optional[Instrument], required_devices=[]):
        """An initialization method with a similar task as __init__()"""
        self._busy = False
        assert isinstance(self, QtWidgets.QWidget)
        if credo is not None:
            try:
                self.credo = weakref.proxy(credo)
            except TypeError:
                self.credo = credo
            assert isinstance(self.credo, Instrument)  # this works even if self.credo is a weakproxy to Instrument
            self._device_connections = {}
            for d in self.required_devices + required_devices:
                self.requireDevice(d)
            self._privlevelconnection = self.credo.services['accounting'].connect('privlevel-changed',
                                                                                  self.onPrivLevelChanged)
            self._credoconnections = [self.credo.connect('config-changed', self.updateUiFromConfig)]
            self._interpreterconnections = []
        else:
            self.credo = None
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)

    def savePersistence(self):
        # do not do anything by default, it is up to the subclass to redefine this.
        # you typically want to call self.credo.savePersistence(name, dict)
        return

    @classmethod
    def testRequirements(cls, credo: Instrument, not_ready_is_ok: bool = False):
        """Return True if the instrument is in a state when this window can be opened. If this
        class method returns False, the window won't be opened or will be closed or disabled if
        it is already open."""
        if credo is None:
            return True
        if not credo.services['accounting'].has_privilege(cls.required_privilege):
            return False
        for r in cls.required_devices:
            try:
                if not (credo.get_device(r).ready or not_ready_is_ok):
                    return False
            except KeyError:
                return False
        return True

    def requireDevice(self, devicename: str):
        if self.credo is None:
            return
        assert isinstance(self.credo, Instrument)
        try:
            device = self.credo.get_device(devicename)
        except KeyError:
            logger.error('Required device {} not found'.format(devicename))
            raise
        assert isinstance(device, (Device, Motor))
        if device in self._device_connections:
            # do not require the same device twice.
            return
        self._device_connections[device] = [
            device.connect('variable-change', self.onDeviceVariableChange),
            device.connect('error', self.onDeviceError),
            device.connect('disconnect', self.onDeviceDisconnect),
            device.connect('ready', self.onDeviceReady)
        ]
        if isinstance(device, Motor):
            self._device_connections[device].extend([
                device.connect('position-change', self.onMotorPositionChange),
                device.connect('stop', self.onMotorStop),
                device.connect('start', self.onMotorStart),
            ])
        logger.debug('Required device {} successfully in toolwindow {}'.format(devicename, self.windowTitle()))

    def onPrivLevelChanged(self, accountingservice, privlevel):
        assert isinstance(self, QtWidgets.QWidget)
        if privlevel < self.required_privilege:
            self.cleanup()
            self.close()

    def onDeviceVariableChange(self, device: Union[Device, Motor], variablename: str, newvalue):
        return False

    def onDeviceError(self, device: Union[Device, Motor], variablename: str, exception: Exception,
                      formatted_traceback: str):
        return False

    def onDeviceDisconnect(self, device: Union[Device, Motor], abnormal_disconnection: bool):
        logger.debug('Device disconnect')
        if self._busy:
            self.setEnabled(False)
        else:
            self.close()
            self.cleanup()
        return False

    def onDeviceReady(self, device: Union[Device, Motor]):
        self.setEnabled(True)
        return False

    def onMotorPositionChange(self, motor: Motor, newposition: float):
        return False

    def onMotorStop(self, motor: Motor, targetpositionreached: bool):
        return False

    def onMotorStart(self, motor: Motor):
        return False

    def unrequireDevice(self, device: Union[str, Device, Motor]):
        if self.credo is None:
            return
        if isinstance(device, str):
            device = self.credo.get_device(device)
        try:
            for cid in self._device_connections[device]:
                device.disconnect(cid)
        except KeyError:
            logger.error('Cannot unrequire device {}'.format(device.name))
        finally:
            logger.debug('Unrequired device {}'.format(device.name))
            del self._device_connections[device]

    def cleanup(self):
        try:
            logger.debug('Cleanup() called on ToolWindow {} ({})'.format(self.objectName(), self.windowTitle()))
            self.savePersistence()
        except RuntimeError:
            # this happens when the underlying C++ object has already been destroyed
            pass
        self.cleanupAfterCommand()
        if self.credo is not None:
            for d in list(self._device_connections.keys()):
                self.unrequireDevice(d)
            if self._privlevelconnection is not None:
                self.credo.services['accounting'].disconnect(self._privlevelconnection)
                self._privlevelconnection = None
            for c in self._credoconnections:
                self.credo.disconnect(c)
            self._credoconnections = []
        try:
            logger.debug('Cleanup() finished on ToolWindow {} ({})'.format(self.objectName(), self.windowTitle()))
        except RuntimeError:
            # this happens when the underlying C++ object has already been destroyed
            pass

    def event(self, event: QtCore.QEvent):
        if event.type() == QtCore.QEvent.ActivationChange:
            self.activationChangeEvent(event)
        return QtWidgets.QWidget.event(self, event)

    def activationChangeEvent(self, event: QtCore.QEvent):
        pass

    #        assert isinstance(self, QtWidgets.QWidget)
    #        if self.windowState() & QtCore.Qt.WindowActive:
    #            logger.debug('ToolWindow {} activation changed: it is now active. State: {}, {}'.format(self.objectName(),
    #                                                                                                    self.isActiveWindow(),
    #                                                                                                    self.windowState() & 0xffff))
    #        else:
    #            logger.debug(
    #                'ToolWindow {} activation changed: it is now not active. State: {}, {}'.format(self.objectName(),

    def closeEvent(self, event: QtGui.QCloseEvent):
        assert isinstance(self, QtWidgets.QWidget)
        logger.debug('CloseEvent received for ToolWindow {}'.format(self.objectName()))
        if self._busy:
            result = QtWidgets.QMessageBox.question(
                self, "Really close?",
                "The process behind this window is still working. If you close this window now, "
                "you can break something. Are you <b>really</b> sure?")
            logger.debug('Question result: {}'.format(result))
            if result != QtWidgets.QMessageBox.Yes:
                event.ignore()
                logger.debug('Phew!')
                return
            else:
                logger.debug("Closing window {} forced.".format(self.objectName()))
        self.cleanup()
        if isinstance(self, QtWidgets.QDockWidget):
            return QtWidgets.QDockWidget.closeEvent(self, event)
        elif isinstance(self, QtWidgets.QMainWindow):
            return QtWidgets.QMainWindow.closeEvent(self, event)
        else:  # isinstance(self, QtWidgets.QWidget)
            return QtWidgets.QWidget.closeEvent(self, event)

    def setBusy(self):
        assert isinstance(self, QtWidgets.QWidget)
        self._busy = True

    def setIdle(self):
        self._busy = False

    def updateUiFromConfig(self, credo):
        """This is called whenever the configuration changed"""
        pass

    def isBusy(self):
        return self._busy

    def cleanupAfterCommand(self):
        if self.credo is not None:
            for c in self._interpreterconnections:
                self.credo.services['interpreter'].disconnect(c)
            self._interpreterconnections = []
            try:
                self.progressBar.setVisible(False)
                self.adjustSize()
            except (AttributeError, RuntimeError):
                pass

    def onCmdReturn(self, interpreter: Interpreter, cmdname: str, retval):
        self.cleanupAfterCommand()

    def onCmdFail(self, interpreter: Interpreter, cmdname: str, exception: Exception, traceback: str):
        pass

    def onCmdProgress(self, interpreter: Interpreter, cmdname: str, description: str, fraction: float):
        try:
            self.progressBar.setVisible(True)
            self.progressBar.setMinimum(0)
            self.progressBar.setMaximum(1000)
            self.progressBar.setValue(1000 * fraction)
            self.progressBar.setFormat(description)
        except (AttributeError, RuntimeError):
            pass

    def onCmdPulse(self, interpreter: Interpreter, cmdname: str, description: str):
        try:
            self.progressBar.setVisible(True)
            self.progressBar.setMinimum(0)
            self.progressBar.setMaximum(0)
            self.progressBar.setValue(0)
            self.progressBar.setFormat(description)
        except (AttributeError, RuntimeError):
            pass

    def onCmdMessage(self, interpreter: Interpreter, cmdname: str, message: str):
        pass

    def onCmdDetail(self, interpreter: Interpreter, cmdname: str, detail: Any):
        pass

    def onInterpreterFlag(self, interpreter: Interpreter, flag: str, state: bool):
        pass

    def onInterpreterNewFlag(self, interpreter: Interpreter, flag: str):
        pass

    def executeCommand(self, command: Type[Command], *args, **kwargs):
        if self.credo is None:
            return None
        logger.debug('executeCommand({}, {}, {})'.format(command.name, args, kwargs))
        interpreter = self.credo.services['interpreter']
        if self._interpreterconnections:
            raise ValueError(
                'Cannot run another command: either the previous command is still running or it has not been cleaned up yet.')
        self._interpreterconnections = [interpreter.connect('cmd-return', self.onCmdReturn),
                                        interpreter.connect('cmd-fail', self.onCmdFail),
                                        interpreter.connect('cmd-detail', self.onCmdDetail),
                                        interpreter.connect('progress', self.onCmdProgress),
                                        interpreter.connect('pulse', self.onCmdPulse),
                                        interpreter.connect('cmd-message', self.onCmdMessage),
                                        interpreter.connect('flag', self.onInterpreterFlag),
                                        interpreter.connect('newflag', self.onInterpreterNewFlag),
                                        ]
        assert isinstance(interpreter, Interpreter)
        try:
            return interpreter.execute_command(command, args, kwargs)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, 'Error executing command',
                                           'Cannot execute command {}: {}'.format(command.name, exc.args[0]))
            self.cleanupAfterCommand()
