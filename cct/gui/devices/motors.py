from ..core.toolwindow import ToolWindow, error_message
from ...core.devices.device import DeviceError
from gi.repository import Gtk, GLib
import logging
logger=logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class Motors(ToolWindow):
    def _init_gui(self, *args):
        self._model=self._builder.get_object('motorlist')
        self._motorconnections=[]
        for m in sorted(self._instrument.motors):
            mot=self._instrument.motors[m]
            lims=mot.get_limits()
            self._model.append((m, '%.3f'%lims[0], '%.3f'%lims[1], '%.3f'%mot.where(), '%.3f'%mot.speed(), mot.leftlimitswitch(),
                                mot.rightlimitswitch(), '%d'%mot.load(), ', '.join(mot.decode_error_flags())))
            self._motorconnections.append((mot,mot.connect('variable-change', self.on_motor_variable_change, m)))
        self._view=self._builder.get_object('motortreeview')
        columns=[
            Gtk.TreeViewColumn('Motor name',Gtk.CellRendererText(), text=0),
            Gtk.TreeViewColumn('Left limit',Gtk.CellRendererText(), text=1),
            Gtk.TreeViewColumn('Right limit',Gtk.CellRendererText(), text=2),
            Gtk.TreeViewColumn('Position', Gtk.CellRendererText(), text=3),
            Gtk.TreeViewColumn('Speed', Gtk.CellRendererText(), text=4),
            Gtk.TreeViewColumn('Left switch', Gtk.CellRendererToggle(), active=5),
            Gtk.TreeViewColumn('Right switch', Gtk.CellRendererToggle(), active=6),
            Gtk.TreeViewColumn('Load', Gtk.CellRendererText(), text=7),
            Gtk.TreeViewColumn('Driver error flags', Gtk.CellRendererText(), text=8)
        ]
        for c in columns:
            c.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            self._view.append_column(c)

    def on_motor_variable_change(self, motor, var, value, motorname):
        for row in self._model:
            if row[0]==motorname:
                if var=='softleft':
                    row[1]='%.3f'%value
                elif var=='softright':
                    row[2]='%.3f'%value
                elif var=='actualposition':
                    row[3]='%.3f'%value
                elif var=='actualspeed':
                    row[4]='%.3f'%value
                elif var=='leftswitchstatus':
                    row[5]=value
                elif var=='rightswitchstatus':
                    row[6]=value
                elif var=='load':
                    row[7]='%d'%value
                elif var=='drivererror':
                    row[8]=', '.join(motor.decode_error_flags(value))
        return False

    def on_move(self, button):
        model, treeiter=self._builder.get_object('motortreeview').get_selection().get_selected()
        motorname=model[treeiter][0]
        movewindow=MotorMover('devices_motors_move.glade','motormover', self._instrument, self._application, motorname)
        movewindow._window.show_all()

    def on_config(self, button):
        model, treeiter=self._builder.get_object('motortreeview').get_selection().get_selected()
        motorname=model[treeiter][0]
        configwindow=MotorConfig('devices_motors_config.glade','motorconfig', self._instrument, self._application, motorname)
        configwindow._window.show_all()

class MotorConfig(ToolWindow):
    def _init_gui(self, motorname):
        self._hide_on_close=False
        self._motorname=motorname
        motor=self._instrument.motors[motorname]
        self._builder.get_object('frametitle').set_label('Configure motor %s (%s/#%d)'%(motorname,motor._controller._instancename,
                                                                                        motor._index))
        limits=motor.get_limits()
        self._builder.get_object('leftlimit_adjustment').set_value(motor.get_variable('softleft'))
        self._builder.get_object('rightlimit_adjustment').set_value(motor.get_variable('softright'))
        self._builder.get_object('calibration_adjustment').set_value(motor.where())
        self._builder.get_object('drivingcurrent_adjustment').set_upper(motor._controller._top_rms_current)
        self._builder.get_object('drivingcurrent_adjustment').set_value(motor.get_variable('maxcurrent'))
        self._builder.get_object('standbycurrent_adjustment').set_upper(motor._controller._top_rms_current)
        self._builder.get_object('standbycurrent_adjustment').set_value(motor.get_variable('standbycurrent'))
        self._builder.get_object('freewheelingdelay_adjustment').set_value(motor.get_variable('freewheelingdelay'))
        self._builder.get_object('leftswitchenable_checkbutton').set_active(motor.get_variable('leftswitchenable'))
        self._builder.get_object('rightswitchenable_checkbutton').set_active(motor.get_variable('rightswitchenable'))
        self._builder.get_object('rampdiv_adjustment').set_value(motor.get_variable('rampdivisor'))
        self._builder.get_object('pulsediv_adjustment').set_value(motor.get_variable('pulsedivisor'))
        self._builder.get_object('microstep_adjustment').set_value(motor._controller._max_microsteps)
        self._builder.get_object('microstep_adjustment').set_value(motor.get_variable('microstepresolution'))

    def on_apply(self, button):
        tobechanged={}
        motor=self._instrument.motors[self._motorname]
        for widgetname, variablename in [('leftlimit_adjustment', 'softleft'),
                                         ('rightlimit_adjustment', 'softright'),
                                         ('drivingcurrent_adjustment', 'maxcurrent'),
                                         ('standbycurrent_adjustment', 'standbycurrent'),
                                         ('freewheelingdelay_adjustment', 'freewheelingdelay'),
                                         ('leftswitchenable_checkbutton', 'leftswitchenable'),
                                         ('rightswitchenable_checkbutton', 'rightswitchenable'),
                                         ('rampdiv_adjustment', 'rampdivisor'),
                                         ('pulsediv_adjustment', 'pulsedivisor'),
                                         ('microstep_adjustment', 'microstepresolution'),
                                         ('calibration_adjustment', 'actualposition')]:
            widget=self._builder.get_object(widgetname)
            if widgetname.endswith('_adjustment'):
                newvalue=widget.get_value()
            elif widgetname.endswith('_checkbutton'):
                newvalue=widget.get_active()
            else:
                raise NotImplementedError(widgetname)
            oldvalue=motor.get_variable(variablename)
            if oldvalue != newvalue:
                tobechanged[variablename]=newvalue
        if tobechanged:
            md=Gtk.MessageDialog(parent=self._window, flags=Gtk.DialogFlags.MODAL|Gtk.DialogFlags.DESTROY_WITH_PARENT,
                                 type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.YES_NO,
                                 message_format='Please confirm changes to motor %s'%self._motorname)
            md.format_secondary_markup('The following parameters will be changed. <b>ARE YOU REALLY SURE?</b>:\n'+'\n'.join('    '+variablename+' to '+str(tobechanged[variablename]) for variablename in sorted(tobechanged)))
            result=md.run()
            if result==Gtk.ResponseType.YES:
                if 'actualposition' in tobechanged:
                    logger.info('Calibrating motor %s to %f.'%(self._motorname, tobechanged['actualposition']))
                    try:
                        motor.calibrate(tobechanged['actualposition'])
                    except DeviceError as de:
                        error_message(self._window, 'Calibration failed',str(de))
                    del tobechanged['actualposition']
                for k in tobechanged:
                    motor.set_variable(k, tobechanged[k])
                if tobechanged:
                    logger.info('Updated motor parameters: %s' % ', '.join(k for k in sorted(tobechanged)))
            else:
                logger.debug('Change cancelled.')
            md.destroy()
        self._window.destroy()



class MotorMover(ToolWindow):
    def _init_gui(self, motorname):
        self._hide_on_close=False
        motorselector=self._builder.get_object('motorselector')
        for i,m in enumerate(sorted(self._instrument.motors)):
            motorselector.append_text(m)
            if m==motorname:
                motorselector.set_active(i)
        self._window.connect('map', self.on_map)
        self._window.connect('unmap', self.on_unmap)
        motorselector.connect('changed', self.on_motorselector_changed)
        GLib.idle_add(lambda ms=motorselector:self.on_motorselector_changed(ms))

    def _breakdown_motorconnection(self):
        if hasattr(self, '_motorconnection'):
            for c in self._motorconnection:
                self._motor.disconnect(c)
            del self._motorconnection
            del self._motor

    def _establish_motorconnection(self):
        self._breakdown_motorconnection()
        self._motor=self._instrument.motors[self._builder.get_object('motorselector').get_active_text()]
        self._motorconnection=[self._motor.connect('variable-change', self.on_motor_variable_change),
                               self._motor.connect('stop', self.on_motor_stop)]

    def on_map(self, window):
        self._establish_motorconnection()

    def on_unmap(self, window):
        self._breakdown_motorconnection()

    def on_move(self, button):
        if button.get_label()=='Move':
            self._builder.get_object('move_button').set_label('Stop')
            try:
                target=self._builder.get_object('target_spin').get_value()
                if self._builder.get_object('relative_checkbutton').get_active():
                    self._motor.moverel(target)
                else:
                    self._motor.moveto(target)
            except:
                button.set_label('Move')
                raise
            self._make_insensitive('Motor is moving', widgets=['close_button', 'motorselector', 'target_spin', 'relative_checkbutton'])
        else:
            self._motor.stop()

    def on_motor_variable_change(self, motor, var, value):
        if var=='actualposition':
            self._builder.get_object('currentpos_label').set_text('%.3f'%value)
        return False

    def on_motor_stop(self, motor, target_reached):
        self._builder.get_object('move_button').set_label('Move')
        self._make_sensitive()


    def on_motorselector_changed(self, combobox):
        if self._window.get_visible():
            self._establish_motorconnection()
            self._builder.get_object('currentpos_label').set_text('%.3f'%self._motor.where())
            self.adjust_limits()
        return False

    def adjust_limits(self):
        lims=self._motor.get_limits()
        where=self._motor.where()
        if self._builder.get_object('relative_checkbutton').get_active():
            lims=[l-where for l in lims]
            where=0
        adj=self._builder.get_object('target_adjustment')
        adj.set_lower(lims[0])
        adj.set_upper(lims[1])
        adj.set_value(where)

    def on_relative_toggled(self, checkbutton):
        self.adjust_limits()