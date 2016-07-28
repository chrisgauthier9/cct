from gi.repository import Gtk


def error_message(parentwindow: Gtk.Window, message: str, reason: str):
    return messagedialog(parentwindow, Gtk.MessageType.ERROR, message, reason)


def question_message(parentwindow: Gtk.Window, question: str, detail: str):
    return messagedialog(parentwindow, Gtk.MessageType.QUESTION, question, detail)


def info_message(parentwindow: Gtk.Window, info: str, detail: str):
    return messagedialog(parentwindow, Gtk.MessageType.INFO, info, detail)


def messagedialog(parentwindow: Gtk.Window, messagetype: Gtk.MessageType, title: str, detail: str):
    md = Gtk.Dialog(parent=parentwindow, title=title, modal=True, destroy_with_parent=True)
    ca = md.get_content_area()
    hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    ca.pack_start(hb, True, True, 0)
    img = Gtk.Image()
    hb.pack_start(img, False, True, 0)
    if messagetype == Gtk.MessageType.QUESTION:
        img.set_from_icon_name('dialog-question', Gtk.IconSize.DIALOG)
        md.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'No', Gtk.ResponseType.NO, 'Yes', Gtk.ResponseType.YES)
    elif messagetype == Gtk.MessageType.ERROR:
        img.set_from_icon_name('dialog-error', Gtk.IconSize.DIALOG)
        md.add_buttons('OK', Gtk.ResponseType.OK)
    elif messagetype == Gtk.MessageType.WARNING:
        img.set_from_icon_name('dialog-warning', Gtk.IconSize.DIALOG)
        md.add_buttons('OK', Gtk.ResponseType.OK)
    elif messagetype == Gtk.MessageType.INFO:
        img.set_from_icon_name('dialog-information', Gtk.IconSize.DIALOG)
        md.add_buttons('OK', Gtk.ResponseType.OK)
    l = Gtk.Label(label=detail)
    hb.pack_start(l)
    result = md.run()
    md.destroy()
    if result == Gtk.ResponseType.YES or result == Gtk.ResponseType.OK:
        return True
    elif result == Gtk.ResponseType.NO:
        return False
    else:
        return None
