"""Dialogs for choosing what a button does: the preset list and the shortcut recorder."""

from gi.repository import Adw, Gdk, Gtk

import logid_config as cfg

STATE_MODIFIERS = [
    (Gdk.ModifierType.CONTROL_MASK, cfg.CTRL),
    (Gdk.ModifierType.ALT_MASK, cfg.ALT),
    (Gdk.ModifierType.SHIFT_MASK, cfg.SHIFT),
    (Gdk.ModifierType.SUPER_MASK, cfg.SUPER),
]


class ShortcutRecorder(Adw.Dialog):
    """Captures a key combination and hands it back as evdev key names."""

    def __init__(self, on_done):
        super().__init__(title="Record Shortcut", content_width=420)
        self.on_done = on_done
        self.keys = []
        self.held_modifiers = []
        self.modifier_only = False

        self.preview = Gtk.Label(label="Waiting for keys…", css_classes=["title-2"])
        hint = Gtk.Label(
            label="Press the key combination you want. Shortcuts the desktop already uses, "
                  "such as Alt+Tab or Super, can't be recorded here; pick them from the list instead.",
            wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dim-label"],
        )
        self.use_button = Gtk.Button(label="Use Shortcut", css_classes=["suggested-action", "pill"],
                                     sensitive=False, halign=Gtk.Align.CENTER)
        self.use_button.connect("clicked", self._use)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        box.append(self.preview)
        box.append(hint)
        box.append(self.use_button)

        view = Adw.ToolbarView(content=box)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._pressed)
        keys.connect("key-released", self._released)
        self.add_controller(keys)

    def _show(self, keys):
        self.keys = keys
        self.preview.set_label(" + ".join(cfg.key_label(k) for k in keys))
        self.use_button.set_sensitive(True)

    def _pressed(self, _ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape and not state & Gtk.accelerator_get_default_mod_mask():
            return False  # let Escape close the dialog
        name = cfg.evdev_key_name(keycode - 8)  # X keycodes are evdev codes offset by 8
        if not name:
            return True
        if name in cfg.MODIFIER_KEYS:
            if name not in self.held_modifiers:
                self.held_modifiers.append(name)
            self.modifier_only = True
            self.preview.set_label(" + ".join(cfg.key_label(k) for k in self.held_modifiers) + " + …")
            return True
        self.modifier_only = False
        self._show([key for mask, key in STATE_MODIFIERS if state & mask] + [name])
        return True

    def _released(self, _ctrl, _keyval, keycode, _state):
        name = cfg.evdev_key_name(keycode - 8)
        if name in cfg.MODIFIER_KEYS:
            if self.modifier_only and self.held_modifiers:
                self._show(list(self.held_modifiers))  # a modifier on its own, e.g. Ctrl
            self.held_modifiers.clear()
            self.modifier_only = False

    def _use(self, _button):
        self.on_done(self.keys)
        self.close()


class ActionPicker(Adw.Dialog):
    """Lists the preset actions, plus recording a custom shortcut."""

    def __init__(self, title, current, allow_nothing, on_done):
        super().__init__(title=title, content_width=440, content_height=620)
        self.on_done = on_done
        page = Adw.PreferencesPage()

        top = Adw.PreferencesGroup()
        record = Adw.ActionRow(title="Record a custom shortcut…", activatable=True)
        record.add_prefix(Gtk.Image(icon_name="input-keyboard-symbolic"))
        record.connect("activated", self._record)
        top.add(record)
        if allow_nothing:
            top.add(self._row("Nothing", None, [], current))
        page.add(top)

        groups = {}
        for category, label, keys in cfg.PRESETS:
            if category not in groups:
                groups[category] = Adw.PreferencesGroup(title=category)
                page.add(groups[category])
            groups[category].add(self._row(label, " + ".join(cfg.key_label(k) for k in keys), keys, current))

        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)

    def _row(self, title, subtitle, keys, current):
        row = Adw.ActionRow(title=title, activatable=True)
        if subtitle:
            row.set_subtitle(subtitle)
        if keys == current:
            row.add_suffix(Gtk.Image(icon_name="object-select-symbolic"))
        row.connect("activated", lambda _r: self._pick(keys))
        return row

    def _pick(self, keys):
        self.on_done(keys)
        self.close()

    def _record(self, _row):
        root = self.get_root()
        self.close()
        ShortcutRecorder(self.on_done).present(root)
