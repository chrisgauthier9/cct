import logging
import traceback

import pkg_resources
from gi.repository import Gtk, GdkPixbuf

from .devicestatusbar import DeviceStatusBar
from .logtreeview import LogTreeView
from ..accounting import UserManager, ProjectManager
from ..core import ToolWindow, error_message, ToolFrame, question_message
from ..devices import Motors, GeniX, TPG201, HaakePhoenix, Pilatus, DeviceConnections
from ..diagnostics import ResourceUsage
from ..measurement import ScanMeasurement, SingleExposure, TransmissionMeasurement, ScriptMeasurement, CommandHelpDialog
from ..setup import EditConfig, SampleEdit, DefineGeometry, Calibration
from ..toolframes import ResourceUsageFrame, NextFSN, ShutterBeamstop, AccountingFrame
from ..tools import ExposureViewer, CapillaryMeasurement, ScanViewer, MaskEditor, DataReduction, OptimizeGeometry
from ...core.commands.command import CommandError
from ...core.instrument.instrument import Instrument
from ...core.services.interpreter import Interpreter

# initialize the logger for the main window level.
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class CollectingHandler(logging.Handler):
    instance = None

    def __init__(self):
        self.collected = []
        if self.__class__.instance is not None:
            raise RuntimeError('This is a singleton class!')
        super().__init__()
        self.__class__.instance = self

    @classmethod
    def get_default(cls):
        return cls.instance

    def emit(self, record):
        self.collected.append(record)


class MainWindow(object):
    toolwindow_registry = [
        ('sampleeditor', SampleEdit, 'samplesetup', 'setup_sampleedit.glade', {}),
        ('definegeometry', DefineGeometry, 'definegeometry', 'setup_definegeometry.glade', {}),
        ('editconfig', EditConfig, 'editconfig', 'setup_editconfig.glade', {}),
        ('calibration', Calibration, 'calibration', 'setup_calibration.glade', {}),
        ('xraysource', GeniX, 'genix', 'devices_genix.glade', {}),
        ('detector', Pilatus, 'pilatus', 'devices_pilatus.glade', {}),
        ('motors', Motors, 'motoroverview', 'devices_motors.glade', {}),
        ('vacgauge', TPG201, 'vacgauge', 'devices_tpg201.glade', {}),
        ('temperaturestage', HaakePhoenix, 'haakephoenix', 'devices_haakephoenix.glade', {}),
        ('connections', DeviceConnections, 'deviceconnections', 'devices_connection.glade', {}),
        ('scanmeasurement', ScanMeasurement, 'scan', 'measurement_scan.glade', {}),
        ('singleexposure', SingleExposure, 'singleexposure', 'measurement_singleexposure.glade', {}),
        ('transmission', TransmissionMeasurement, 'measuretransmission',
         'measurement_transmission.glade', {}),
        ('scriptmeasurement', ScriptMeasurement, 'script', 'measurement_script.glade', {}),
        ('maskeditor', MaskEditor, 'maskeditor', 'tools_maskeditor.glade', {}),
        ('imgviewer', ExposureViewer, 'calibration', 'setup_calibration.glade', {}),
        ('viewscans', ScanViewer, 'scanviewer', 'tools_scanviewer.glade', {}),
        ('capillarymeasurement', CapillaryMeasurement, 'capillarymeasurement',
         'tools_capillarymeasurement.glade', {}),
        ('datareduction', DataReduction, 'datareduction', 'tools_datareduction.glade', {}),
        ('resourceusage', ResourceUsage, 'resourceusagewindow', 'diagnostics_resourceusage.glade',
         {}),
        ('commandhelp', CommandHelpDialog, 'commandhelpbrowser', 'help_commandhelpbrowser.glade',
         {'insert': 'on_insert_command'}),
        ('users', UserManager, 'usermanager', 'accounting_usermanager.glade', {}),
        ('projects', ProjectManager, 'projectmanager', 'accounting_projectmanager.glade', {}),
        ('optimizegeometry', OptimizeGeometry, 'optimizegeometry', 'tools_optimizegeometry.glade', {}),
    ]

    class LogHandler(logging.Handler):
        def __init__(self, mainwindow):
            super().__init__()
            self.mw = mainwindow

        def emit(self, record):
            message = self.format(record)
            # GLib.idle_add(lambda msg=message, rec=record: self.mw.writelogline(msg, rec) and False)
            self.mw.writelogline(message, record)

    def __init__(self, instrument: Instrument):
        # initialize the main window
        self.builder = Gtk.Builder.new_from_file(
            pkg_resources.resource_filename('cct', 'resource/glade/mainwindow.glade'))
        self.builder.set_application(Gtk.Application.get_default())
        self.widget = self.builder.get_object('mainwindow')
        self.builder.connect_signals(self)
        self.widget.set_show_menubar(True)
        self.widget.connect('delete-event', self.on_delete_event)
        self.widget.set_default_icon_list([GdkPixbuf.Pixbuf.new_from_file_at_size(
            pkg_resources.resource_filename('cct', 'resource/icons/scalable/cctlogo.svg'),
            sz, sz) for sz in [16, 32, 48, 64, 128, 256]])
        self.widget.show_all()

        # Initialize the log textbuffer
        self._logtags = self.builder.get_object('log_texttags')
        self._logbuffer = self.builder.get_object('logbuffer')
        self._logbuffer.create_mark(
            'log_end', self._logbuffer.get_end_iter(), False)
        self._logview = self.builder.get_object('logtext')

        self._logview2 = LogTreeView()
        self.builder.get_object('logviewer_stack').add_titled(self._logview2.widget, 'treelogviewer', 'Log tree')

        # initialize custom log handler for the root logger. This is responsible for printing
        # all log records in the main window.
        self._loghandler = self.LogHandler(self)
        self._loghandler.setLevel(logging.DEBUG)
        logging.root.addHandler(self._loghandler)
        self._loghandler.setFormatter(logging.Formatter(
            '%(asctime)s: %(levelname)s: %(message)s  (Origin: %(name)s:%(lineno)d)'))

        ch = CollectingHandler.get_default()
        for record in ch.collected:
            self._loghandler.emit(record)
        logging.root.removeHandler(ch)
        del ch.collected

        self._toolwindows = {}
        self._toolwindow_connections = {}
        self.instrument = instrument
        self._instrumentconnections = [
            self.instrument.connect('shutdown', self.on_instrument_shutdown),
            self.instrument.connect('device-connected', lambda i, d: self.set_menu_sensitivity()),
            self.instrument.connect('device-disconnected', lambda i, d, b: self.set_menu_sensitivity()),
        ]
        if self.instrument.online:
            self.instrument.connect_devices()
        logger.debug('Mainwindow: devices connected.')
        self._devicestatus = DeviceStatusBar(self.instrument)
        logger.debug('DeviceStatusBar initialized')
        self.builder.get_object('devicestatus_box').pack_start(self._devicestatus, True, True, 0)

        self._toolframes = {}
        for framename, cls, gladefile, mainwidget in [
            ('resourceusage', ResourceUsageFrame, 'toolframe_telemetry.glade', 'telemetryframe'),
            ('nextfsn', NextFSN, 'toolframe_nextfsn.glade', 'nextfsnframe'),
            ('shutterbeamstop', ShutterBeamstop, 'toolframe_shutter.glade', 'shutterframe'),
            ('accounting', AccountingFrame, 'toolframe_accounting.glade', 'accountingframe')
        ]:
            try:
                self._toolframes[framename] = cls(gladefile, mainwidget, self.instrument)
                self.builder.get_object('toolbox').pack_end(self._toolframes[framename].widget, False, True, 0)
            except Exception:
                logger.error('Cannot open toolframe ' + framename)
            logger.debug('Initializing toolframes done.')
        self.widget.show_all()
        self.widget.set_title('Credo Control Tool v{}'.format(pkg_resources.get_distribution('cct').version))
        logger.debug('Connecting to interpreter')
        interpreter = self.instrument.services['interpreter']
        self._interpreterconnections = [
            interpreter.connect('cmd-return', self.on_interpreter_cmd_return),
            interpreter.connect('cmd-fail', self.on_interpreter_cmd_fail),
            interpreter.connect('pulse', self.on_interpreter_cmd_pulse),
            interpreter.connect('progress', self.on_interpreter_cmd_progress),
            interpreter.connect('cmd-message', self.on_interpreter_cmd_message),
            interpreter.connect('idle-changed', self.on_interpreter_idle_changed),
        ]
        self._commandhistory = []
        self._historyindex = None
        self.on_change_logviewer(self.builder.get_object('menuitem_advancedlogviewer'))
        self.set_menu_sensitivity()

    def on_change_logviewer(self, checkmenuitem: Gtk.CheckMenuItem):
        if checkmenuitem.get_active():
            self.builder.get_object('logviewer_stack').set_visible_child_name('treelogviewer')
        else:
            self.builder.get_object('logviewer_stack').set_visible_child_name('textlogviewer')

    def on_command_entry_keyevent(self, entry: Gtk.Entry, event):
        if event.hardware_keycode == 111:
            # cursor up key
            if self._commandhistory:
                if self._historyindex is None:
                    self._historyindex = len(self._commandhistory)
                self._historyindex = max(0, self._historyindex - 1)
                entry.set_text(self._commandhistory[self._historyindex])
            return True  # inhibit further processing of this key event
        elif event.hardware_keycode == 116:
            # cursor down key
            if self._commandhistory:
                if self._historyindex is None:
                    self._historyindex = -1
                self._historyindex = min(self._historyindex + 1, len(self._commandhistory) - 1)
                entry.set_text(self._commandhistory[self._historyindex])
            return True  # inhibit further processing of this key event
        return False

    def on_interpreter_idle_changed(self, interpreter: Instrument, idle: bool):
        if not idle:
            self.builder.get_object('command_entry').set_sensitive(idle)
            if self.builder.get_object('execute_button').get_label() == 'Execute':
                self.builder.get_object('execute_button').set_sensitive(idle)
        if idle:
            self.builder.get_object('command_entry').set_sensitive(idle)
            self.builder.get_object('execute_button').set_sensitive(idle)

    def on_command_execute(self, button: Gtk.Button):
        if button.get_label() == 'Execute':
            cmd = self.builder.get_object('command_entry').get_text()
            try:
                self.instrument.services['interpreter'].execute_command(cmd)
            except CommandError as ce:
                error_message(self.widget, 'Cannot execute command', str(ce))
            else:
                button.set_label('Stop')
                if (not self._commandhistory) or (self._commandhistory and self._commandhistory[-1] != cmd):
                    self._commandhistory.append(self.builder.get_object('command_entry').get_text())
        elif button.get_label() == 'Stop':
            self.instrument.services['interpreter'].kill()
        else:
            raise ValueError(button.get_label())

    # noinspection PyUnusedLocal
    def on_interpreter_cmd_return(self, interpreter: Interpreter, commandname: str, returnvalue: object):
        self.builder.get_object('command_entry').set_sensitive(True)
        self.builder.get_object('command_entry').set_progress_fraction(0)
        self.builder.get_object('command_entry').set_text('')
        self.builder.get_object('command_entry').grab_focus()
        self.builder.get_object('execute_button').set_label('Execute')
        self._historyindex = None
        self.builder.get_object('statusbar').pop(1)

    # noinspection PyUnusedLocal,PyMethodMayBeStatic
    def on_interpreter_cmd_fail(self, interpreter, commandname, exc, tb):
        logger.error('Command {} failed: {} {}'.format(commandname, str(exc), tb))

    # noinspection PyUnusedLocal
    def on_interpreter_cmd_message(self, interpreter, commandname, message):
        self.builder.get_object('statusbar').pop(1)
        self.builder.get_object('statusbar').push(1, message)
        logger.info('Command {} :: {}'.format(commandname, message))

    # noinspection PyUnusedLocal
    def on_interpreter_cmd_pulse(self, interpreter, commandname, message):
        self.builder.get_object('command_entry').progress_pulse()
        self.builder.get_object('statusbar').pop(1)
        self.builder.get_object('statusbar').push(1, message)

    # noinspection PyUnusedLocal
    def on_interpreter_cmd_progress(self, interpreter, commandname, message, fraction):
        self.builder.get_object('command_entry').set_progress_fraction(fraction)
        self.builder.get_object('statusbar').pop(1)
        self.builder.get_object('statusbar').push(1, message)

    def on_delete_event(self, window, event):
        return self.on_quit()

    def writelogline(self, message: str, record: logging.LogRecord):
        assert hasattr(record, 'message')
        if record.levelno >= logging.CRITICAL:
            tag = self._logtags.lookup('critical')
        elif record.levelno >= logging.ERROR:
            tag = self._logtags.lookup('error')
        elif record.levelno >= logging.WARNING:
            tag = self._logtags.lookup('warning')
        else:
            tag = self._logtags.lookup('normal')
        enditer = self._logbuffer.get_end_iter()
        self._logbuffer.insert_with_tags(enditer, message + '\n', tag)
        self._logview.scroll_to_mark(
            self._logbuffer.get_mark('log_end'), 0.1, False, 0, 0)
        if record.levelno >= logging.INFO:
            self.builder.get_object('statusbar').pop(0)
            self.builder.get_object('statusbar').push(0, record.message.split('\n')[0])
        self._logview2.add_logentry(record)
        return False

    def construct_and_run_dialog(self, windowclass, toplevelname, gladefile, windowtitle, connections):
        assert issubclass(windowclass, ToolWindow)
        key = str(windowclass) + str(toplevelname)
        logger.debug('Construct & run dialog: ' + gladefile)
        if key not in self._toolwindows:
            logger.debug('Constructing needed for dialog ' + gladefile)
            try:
                self._toolwindows[key] = windowclass(gladefile, toplevelname, self.instrument, windowtitle)
            except ToolFrame.DeviceException as ex:
                error_message(self.widget, 'Could not open window {}'.format(windowtitle),
                              'Missing required device: {}'.format(ex.args[0]))
                return
            except Exception as exc:
                error_message(self.widget, 'Could not open window {}'.format(windowtitle),
                              '{}\n{}'.format(str(exc), traceback.format_exc()))
                return
            # if self._toolwindows[key].widget.destroyed():
            #    logger.error('Error while constructing dialog ' + gladefile)
            #    del self._toolwindows[key]
            logger.debug('Successful construction of dialog ' + gladefile)
            assert key not in self._toolwindow_connections
            logger.debug('Connecting signals for dialog ' + gladefile)
            try:
                self._toolwindow_connections[key] = [
                    self._toolwindows[key].connect('destroy', self.on_toolwindow_destroyed, key)]
                for signal in connections:
                    self._toolwindow_connections[key].append(
                        self._toolwindows[key].connect(signal, getattr(self, connections[signal])))
            except Exception as exc:
                logger.error('Error connecting signals to dialog ' + gladefile)
                try:
                    for c in self._toolwindow_connections[key]:
                        self._toolwindows[key].disconnect(c)
                    self._toolwindows[key].destroy()
                    raise
                finally:
                    del self._toolwindow_connections[key]
                    del self._toolwindows[key]
            logger.debug('Dialog should be up and running: ' + gladefile)
        logger.debug('Presenting dialog ' + gladefile)
        return self._toolwindows[key].widget.present()

    def on_toolwindow_destroyed(self, toolwindow: ToolWindow, key):
        logger.debug('Dialog destroyed: ' + toolwindow.gladefile)
        assert key in self._toolwindow_connections
        for c in self._toolwindow_connections[key]:
            toolwindow.disconnect(c)
        del self._toolwindow_connections[key]
        del self._toolwindows[key]
        logger.debug('Mainwindow keeps no reference for dialog ' + toolwindow.gladefile)

    def on_quit(self):
        if self.instrument.is_busy():
            if not question_message(self.widget, 'Confirm quit', 'The instrument is busy. Do you still want to quit?'):
                return True
        logger.info('Shutdown requested.')
        self.instrument.save_state()
        self.instrument.shutdown()
        return True

    def on_instrument_shutdown(self, instrument):
        logger.info('Instrument shutdown finished.')
        for c in self._instrumentconnections:
            instrument.disconnect(c)
        self._instrumentconnections = []
        logging.root.removeHandler(self._loghandler)
        self.widget.destroy()
        Gtk.Application.get_default().quit()

    def on_menu(self, menuitem: Gtk.MenuItem):
        name = menuitem.get_name()
        if not (name.startswith('menuitem') or name.startswith('toolitem')):
            raise ValueError('Invalid menu item name: {}'.format(name))
        name = name.split('_', 1)[1]
        if name == 'quit':
            return self.on_quit()
        elif name == 'savesettings':
            self.instrument.save_state()

        elif name == 'about':
            builder = Gtk.Builder.new_from_file(
                pkg_resources.resource_filename('cct', 'resource/glade/help_about.glade'))
            ad = builder.get_object('aboutdialog')
            ad.set_version(pkg_resources.get_distribution('cct').version)
            ad.set_logo(GdkPixbuf.Pixbuf.new_from_file_at_size(
                pkg_resources.resource_filename('cct', 'resource/icons/scalable/cctlogo.svg'), 256, 256))
            ad.run()
            ad.destroy()
            del ad
        else:
            for nm, cls, toplevelname, gladefile, connections in self.toolwindow_registry:
                if nm != name:
                    continue
                self.construct_and_run_dialog(cls, toplevelname, gladefile, menuitem.get_label().replace('_', ''),
                                              connections)
                return False
            raise ValueError(name)

    def on_insert_command(self, commandhelpdialog: CommandHelpDialog, command: str):
        self.builder.get_object('command_entry').set_text(command)

    def on_toolbar(self, toolbutton):
        return self.on_menu(toolbutton)

    def set_menu_sensitivity(self):
        for nm, cls, toplevelname, gladefile, connections in self.toolwindow_registry:
            requirementsmet = cls.requirements_met(self.instrument)
            for what in ['menuitem', 'toolitem']:
                try:
                    self.builder.get_object(what + '_' + nm).set_sensitive(requirementsmet)
                except AttributeError:
                    pass
