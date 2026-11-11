#!/usr/bin/python3
"""Mouse Tailor: a GNOME settings app for Logitech mice driven by LogiOps."""

import os
import shutil
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import logid_config as cfg  # noqa: E402
import widgets as w  # noqa: E402
from battery import BatteryMonitor  # noqa: E402
from dialogs import ActionPicker  # noqa: E402

APP_ID = "io.github.mousetailor.MouseTailor"
VERSION = "1.1.0"
INSTALLED_HELPER = "/usr/libexec/mouse-tailor/mouse-tailor-helper"
SOURCE_HELPER = os.path.join(HERE, "..", "data", "mouse-tailor-helper")
DETECT_SECONDS = 6


def helper_argv(*args):
    """pkexec command for the root helper: the installed one, or the copy next to the source."""
    if os.path.exists(INSTALLED_HELPER):
        return ["pkexec", INSTALLED_HELPER, *args]
    return ["pkexec", "/bin/sh", os.path.abspath(SOURCE_HELPER), *args]


def service_state():
    if not shutil.which("logid"):
        return "missing"
    result = subprocess.run(["systemctl", "is-active", "logid"], capture_output=True, text=True)
    return "running" if result.stdout.strip() == "active" else "stopped"


def device_icon(name):
    return "input-mouse-symbolic" if cfg.has_scroll_wheel(name) else "input-keyboard-symbolic"


class TopBarExtension:
    """The Mouse Tailor GNOME Shell extension, which shows batteries in the top bar and Quick Settings."""

    UUID = "mouse-tailor@mousetailor.github.io"

    def installed(self):
        roots = ["/usr/share/gnome-shell/extensions", os.path.join(GLib.get_user_data_dir(), "gnome-shell", "extensions")]
        return any(os.path.isdir(os.path.join(root, self.UUID)) for root in roots)

    def _call(self, method):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            return bus.call_sync("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions", method,
                                 GLib.Variant("(s)", (self.UUID,)), None, Gio.DBusCallFlags.NONE, 2000, None).unpack()
        except GLib.Error:
            return None

    def _shell_settings(self):
        source = Gio.SettingsSchemaSource.get_default()
        return Gio.Settings(schema_id="org.gnome.shell") if source.lookup("org.gnome.shell", True) else None

    def state(self):
        """missing, unavailable (not GNOME), pending (installed since login), enabled or disabled."""
        if not self.installed():
            return "missing"
        info = self._call("GetExtensionInfo")
        if info is None:
            return "unavailable"
        if not info[0]:
            return "pending"
        return "enabled" if info[0].get("state") == 1 else "disabled"

    def will_enable(self):
        settings = self._shell_settings()
        return bool(settings) and self.UUID in settings.get_strv("enabled-extensions")

    def set_enabled(self, enabled):
        if self.state() in ("enabled", "disabled"):
            self._call("EnableExtension" if enabled else "DisableExtension")
            return
        # GNOME Shell only finds newly installed extensions at login; list it so it starts then.
        settings = self._shell_settings()
        if settings:
            uuids = [u for u in settings.get_strv("enabled-extensions") if u != self.UUID]
            settings.set_strv("enabled-extensions", uuids + ([self.UUID] if enabled else []))


def toolbar_view(window, content, title=None):
    """Wraps a page's content with a header bar that has its own Apply button."""
    header = Adw.HeaderBar()
    if title is not None:
        header.set_title_widget(Adw.WindowTitle(title=title))
    header.pack_end(window.new_apply_button())
    view = Adw.ToolbarView(content=content)
    view.add_top_bar(header)
    return view


class ButtonPage(Adw.NavigationPage):
    """Edits what one button does."""

    def __init__(self, window, device, cid, gestures_supported):
        super().__init__(title=cfg.control_name(cid))
        self.window = window
        self.button = cfg.get_button(device, cid)
        self.modes = [m for m in cfg.MODES if gestures_supported or m != "gestures"]
        if self.button["mode"] not in self.modes:
            self.button["mode"] = "default"

        mode_group = Adw.PreferencesGroup()
        self.mode_row = Adw.ComboRow(title="Action",
                                     subtitle=f"Default: {cfg.control_default(cid) or 'what the device normally does'}",
                                     model=Gtk.StringList.new([cfg.MODE_LABELS[m] for m in self.modes]))
        self.mode_row.set_selected(self.modes.index(self.button["mode"]))
        self.mode_row.connect("notify::selected", self._mode_changed)
        mode_group.add(self.mode_row)

        shortcut_group = Adw.PreferencesGroup(title="Shortcut")
        self.shortcut_row = Adw.ActionRow(title="When pressed", activatable=True)
        self.shortcut_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.shortcut_row.connect("activated", self._pick_shortcut)
        shortcut_group.add(self.shortcut_row)

        gesture_group = Adw.PreferencesGroup(
            title="Gestures",
            description="Tap the button, or hold it down and move the mouse. "
                        "Repeat keeps firing while you keep moving, which suits volume or zoom.")
        self.gesture_rows = {}
        for direction, label, _ in cfg.DIRECTIONS:
            row = Adw.ActionRow(title=label, activatable=True)
            if direction != "None":
                repeat = Gtk.CheckButton(label="Repeat", valign=Gtk.Align.CENTER,
                                         active=self.button["gestures"].get(direction, {}).get("repeat", False))
                repeat.connect("toggled", self._repeat_toggled, direction)
                row.add_suffix(repeat)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", self._pick_gesture, direction, label)
            gesture_group.add(row)
            self.gesture_rows[direction] = row

        self.shortcut_reveal = w.reveal(shortcut_group, self.button["mode"] == "shortcut")
        self.gesture_reveal = w.reveal(gesture_group, self.button["mode"] == "gestures")
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for part in (mode_group, self.shortcut_reveal, self.gesture_reveal):
            column.append(part)
        self.set_child(toolbar_view(window, w.page(column)))
        self.refresh()

    def refresh(self):
        mode = self.button["mode"]
        self.shortcut_reveal.set_reveal_child(mode == "shortcut")
        self.gesture_reveal.set_reveal_child(mode == "gestures")
        self.shortcut_row.set_subtitle(cfg.describe_keys(self.button["keys"]) or "Not set yet")
        for direction, row in self.gesture_rows.items():
            keys = self.button["gestures"].get(direction, {}).get("keys")
            row.set_subtitle(cfg.describe_keys(keys) or "Nothing")

    def _mode_changed(self, row, _pspec):
        self.button["mode"] = self.modes[row.get_selected()]
        self.window.settings_changed()

    def _gesture(self, direction):
        return self.button["gestures"].setdefault(direction, {"keys": [], "repeat": False})

    def _pick_shortcut(self, _row):
        def done(keys):
            self.button["keys"] = keys
            self.window.settings_changed()
        ActionPicker("Shortcut", self.button["keys"], False, done).present(self.window)

    def _pick_gesture(self, _row, direction, label):
        def done(keys):
            self._gesture(direction)["keys"] = keys
            self.window.settings_changed()
        ActionPicker(label, self._gesture(direction)["keys"], True, done).present(self.window)

    def _repeat_toggled(self, check, direction):
        self._gesture(direction)["repeat"] = check.get_active()
        self.window.settings_changed()


class DevicePage(Adw.NavigationPage):
    """One device: battery, scroll wheel settings and buttons."""

    def __init__(self, window, name):
        super().__init__(title=cfg.short_name(name))
        self.window = window
        self.name = name
        self.device = window.settings["devices"][name]
        self.rows = {}
        sections = []

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.badge = w.DeviceBadge(device_icon(name), size=128)
        hero.append(self.badge)
        hero.append(Gtk.Label(label=cfg.short_name(name), wrap=True, justify=Gtk.Justification.CENTER,
                              css_classes=["hero-title"]))
        self.hero_status = Gtk.Label(css_classes=["dim-label"])
        hero.append(self.hero_status)
        sections.append(hero)

        if cfg.has_scroll_wheel(name):
            wheel = Adw.PreferencesGroup(title="Scroll wheel")
            wheel.add(self._speed_row())
            invert = Adw.SwitchRow(title="Reverse scroll direction",
                                   subtitle="Content moves the way your finger moves, like on a phone",
                                   active=cfg.scroll_inverted(self.device))
            invert.connect("notify::active", self._invert_changed)
            wheel.add(invert)
            sections.append(wheel)

        buttons = Adw.PreferencesGroup(title="Buttons", description="Choose a button to change what it does.")
        for cid, gestures in cfg.visible_controls(self.device):
            row = Adw.ActionRow(title=cfg.control_name(cid), activatable=True, subtitle_lines=2)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, c=cid, g=gestures: window.nav.push(ButtonPage(window, self.device, c, g)))
            buttons.add(row)
            self.rows[cid] = row
        if not self.rows:
            buttons.set_description("LogiOps didn't report any buttons on this device that can be changed.")
        sections.append(buttons)

        more = Adw.PreferencesGroup()
        reset = Adw.ButtonRow(title="Reset buttons") if hasattr(Adw, "ButtonRow") else \
            Adw.ActionRow(title="Reset buttons", activatable=True)
        reset.connect("activated", self._confirm, "Reset all buttons?",
                      "Nothing changes on the device until you press Apply.", "Reset", self._reset)
        more.add(reset)
        remove = Adw.ButtonRow(title="Remove device", css_classes=["destructive-action"]) if hasattr(Adw, "ButtonRow") \
            else Adw.ActionRow(title="Remove device", activatable=True, css_classes=["error"])
        remove.connect("activated", self._confirm, f"Remove {cfg.short_name(name)}?",
                       "Its buttons and scroll wheel go back to normal when you press Apply. "
                       "Use Find devices to add it again.", "Remove", self._remove)
        more.add(remove)
        sections.append(more)

        self.set_child(toolbar_view(window, w.page(*sections), title=""))
        self.refresh()

    def _speed_row(self):
        row = Adw.PreferencesRow(activatable=False, focusable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["speed-row"])
        header = Gtk.Box()
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        titles.append(Gtk.Label(label="Scroll speed", xalign=0))
        titles.append(Gtk.Label(label="How far one click of the wheel scrolls. Faster speeds turn off smooth scrolling.",
                                xalign=0, wrap=True, css_classes=["caption", "dim-label"]))
        header.append(titles)
        self.speed_value = Gtk.Label(css_classes=["speed-value"], valign=Gtk.Align.START)
        header.append(self.speed_value)
        box.append(header)

        speeds = cfg.SCROLL_SPEEDS
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, speeds[0], speeds[-1], 1)
        scale.set_round_digits(0)
        scale.set_draw_value(False)
        for speed in speeds:
            scale.add_mark(speed, Gtk.PositionType.BOTTOM, "Normal" if speed == 1 else f"{speed}×")
        scale.set_value(cfg.scroll_speed(self.device))
        scale.connect("value-changed", self._speed_changed)
        box.append(scale)
        row.set_child(box)
        return row

    def refresh(self):
        for cid, row in self.rows.items():
            row.set_subtitle(cfg.describe_button(cid, cfg.get_button(self.device, cid)))
        if cfg.has_scroll_wheel(self.name):
            self.speed_value.set_label(cfg.describe_speed(cfg.scroll_speed(self.device)))
        battery = self.window.batteries.for_device(self.name)
        connected = cfg.is_connected(self.name, cfg.connected_devices())
        parts = ["Connected" if connected else "Not connected right now"]
        if battery:
            parts.append(f"Battery: {battery.summary}")
        self.hero_status.set_label(" · ".join(parts))
        self.badge.ring.set_fraction(battery.fraction if battery else None)

    def _speed_changed(self, scale):
        speed = int(round(scale.get_value()))
        if speed != cfg.scroll_speed(self.device):
            self.device["scroll_speed"] = speed
            self.window.settings_changed()

    def _invert_changed(self, row, _pspec):
        self.device["scroll_invert"] = row.get_active()
        self.window.settings_changed()

    def _confirm(self, _row, heading, body, label, action):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", label)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, response: response == "ok" and action())
        dialog.present(self.window)

    def _reset(self):
        self.device["buttons"] = {}
        self.window.settings_changed()
        self.window.toast("Buttons reset. Press Apply to use them.")

    def _remove(self):
        if ("scroll_speed" in self.device or "scroll_invert" in self.device) \
                and self.name not in self.window.settings["scroll_restore"]:
            # Without an entry in the config, logid would leave the wheel set the way it was.
            self.window.settings["scroll_restore"].append(self.name)
        del self.window.settings["devices"][self.name]
        self.window.nav.pop()
        self.window.settings_changed()
        self.window.toast(f"Removed {cfg.short_name(self.name)}. Press Apply to finish.")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Mouse Tailor", default_width=600, default_height=780)
        self.settings = cfg.load_settings()
        self.apply_buttons = []
        self.cards = {}
        self.extension = TopBarExtension()
        self.batteries = BatteryMonitor()
        self.batteries.connect("changed", lambda _m: self.refresh())
        self._add_known_devices()

        self.status_dot = Gtk.Box(css_classes=["status-dot"], valign=Gtk.Align.CENTER)
        self.status_label = Gtk.Label(css_classes=["caption"])
        pill = Gtk.Box(spacing=8, css_classes=["status-pill"], halign=Gtk.Align.CENTER)
        pill.append(self.status_dot)
        pill.append(self.status_label)

        heading = Gtk.Label(label="Your devices", xalign=0, css_classes=["heading"])
        self.cards_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                     max_children_per_line=2, min_children_per_line=1,
                                     column_spacing=14, row_spacing=14)
        self.add_card = w.AddDeviceCard()
        self.add_card.connect("clicked", self._find_devices)
        self.add_reveal = Gtk.Revealer(child=self.add_card, transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                       transition_duration=400)
        devices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        devices.append(heading)
        devices.append(self.cards_box)
        devices.append(self.add_reveal)

        top_bar = Adw.PreferencesGroup(title="Top bar")
        self.top_bar_row = Adw.SwitchRow(title="Battery in the top bar and Quick Settings")
        self.top_bar_row.add_prefix(Gtk.Image(icon_name="battery-level-80-symbolic"))
        self._top_bar_handler = self.top_bar_row.connect("notify::active", self._top_bar_toggled)
        top_bar.add(self.top_bar_row)

        more = Adw.PreferencesGroup()
        preview = Adw.ActionRow(title="Preview config file", subtitle=str(cfg.LOGID_CFG), activatable=True)
        preview.add_prefix(Gtk.Image(icon_name="text-x-generic-symbolic"))
        preview.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        preview.connect("activated", self._preview)
        more.add(preview)
        about = Adw.ActionRow(title="About Mouse Tailor", activatable=True)
        about.add_prefix(Gtk.Image(icon_name="help-about-symbolic"))
        about.connect("activated", self._about)
        more.add(about)

        self.banner = Adw.Banner(title="Your current logid.cfg was written by hand. Apply replaces it "
                                       "(the first Apply keeps a copy as logid.cfg.bak).")
        self.banner.set_revealed(cfg.has_handwritten_cfg())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.banner)
        content.append(w.page(pill, devices, top_bar, more))

        self.nav = Adw.NavigationView()
        self.nav.add(Adw.NavigationPage(title="Mouse Tailor", child=toolbar_view(self, content)))
        self.toasts = Adw.ToastOverlay(child=self.nav)
        self.set_content(self.toasts)
        self.refresh()
        # Cards fade in one after another, then the "Find devices" card.
        w.stagger([*(card.get_parent() for card in self.cards.values()), self.add_reveal])

    # Apply buttons: one per page header, all kept in the same state.

    def new_apply_button(self):
        button = w.ApplyButton(self._apply)
        button.set_sensitive(getattr(self, "can_apply", False))
        self.apply_buttons.append(button)
        return button

    def _set_apply_state(self, state):
        self.apply_buttons = [b for b in self.apply_buttons if b.get_root()]
        for button in self.apply_buttons:
            button.set_state(state)

    def _add_known_devices(self):
        connected = cfg.connected_devices()
        for name, controls in cfg.KNOWN_DEVICES.items():
            if name not in self.settings["devices"] and cfg.is_connected(name, connected):
                self.settings["devices"][name] = cfg.new_device(controls)

    def refresh(self):
        state = service_state()
        self.status_label.set_label({
            "missing": "LogiOps isn't installed",
            "stopped": "LogiOps is stopped. Press Apply to start it",
            "running": "LogiOps is running",
        }[state])
        self.status_dot.set_css_classes(["status-dot", "connected" if state == "running" else "offline"])

        self._sync_cards()
        self._sync_top_bar()

        pending = not cfg.is_applied(self.settings) or state == "stopped"
        self.can_apply = pending and state != "missing"
        self.apply_buttons = [b for b in self.apply_buttons if b.get_root()]
        for button in self.apply_buttons:
            button.set_sensitive(self.can_apply)
            button.set_tooltip_text("Save these settings to your devices" if pending else "Everything is applied")

        stack = self.nav.get_navigation_stack()
        for i in range(stack.get_n_items()):
            page = stack.get_item(i)
            if hasattr(page, "refresh"):
                page.refresh()

    def _sync_cards(self):
        names = list(self.settings["devices"])
        for name in [n for n in self.cards if n not in names]:
            self.cards_box.remove(self.cards.pop(name).get_parent().get_parent())
        connected = cfg.connected_devices()
        for name in names:
            card = self.cards.get(name)
            if card is None:
                card = w.DeviceCard(cfg.short_name(name), device_icon(name))
                card.connect("clicked", lambda _c, n=name: self.nav.push(DevicePage(self, n)))
                revealer = Gtk.Revealer(child=card, transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                        transition_duration=400)
                self.cards_box.append(revealer)
                if self.get_mapped():  # a device found later fades in on its own
                    w.stagger([revealer])
                revealer.get_parent().set_focusable(False)
                self.cards[name] = card
            card.update(cfg.is_connected(name, connected), cfg.describe_device(self.settings["devices"][name]),
                        self.batteries.for_device(name))

    def _sync_top_bar(self):
        state = self.extension.state()
        self.top_bar_row.set_visible(state != "unavailable")
        self.top_bar_row.set_sensitive(state != "missing")
        on = state == "enabled" or (state == "pending" and self.extension.will_enable())
        with GObjectBlock(self.top_bar_row, self._top_bar_handler):
            self.top_bar_row.set_active(on)
        self.top_bar_row.set_subtitle({
            "missing": "Not installed with this copy of Mouse Tailor",
            "pending": "Log out and back in to finish turning it on" if on else
                       "Turns on after you log out and back in",
            "enabled": "Shows each device's battery. Also adds a Mouse Tailor tile to Quick Settings",
            "disabled": "Shows each device's battery. Also adds a Mouse Tailor tile to Quick Settings",
        }.get(state, ""))

    def _top_bar_toggled(self, row, _pspec):
        self.extension.set_enabled(row.get_active())
        GLib.timeout_add(400, lambda: self._sync_top_bar() and False)

    def settings_changed(self):
        cfg.save_settings(self.settings)
        self.refresh()

    def toast(self, text, timeout=3):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=timeout))

    def _run_helper(self, args, on_success, on_finish=None):
        proc = Gio.Subprocess.new(helper_argv(*args),
                                  Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)

        def finished(proc, result):
            _ok, output, _err = proc.communicate_utf8_finish(result)
            status = proc.get_exit_status()
            ok = status == 0
            if status in (126, 127):
                self.toast("Cancelled: the password wasn't entered.")
            elif not ok:
                lines = (output or "").strip().splitlines()
                self.toast(f"Something went wrong: {lines[-1] if lines else f'exit code {status}'}", timeout=8)
            else:
                on_success(output or "")
            if on_finish:
                on_finish(ok)
            self.refresh()

        proc.communicate_utf8_async(None, None, finished)

    def _find_devices(self, _card):
        self.add_card.set_busy(True)
        self.toast("Looking for devices. Move your mouse so it wakes up.", timeout=DETECT_SECONDS)

        def found(output):
            devices = cfg.parse_detection(output)
            for name, controls in devices.items():
                device = self.settings["devices"].setdefault(name, cfg.new_device(controls))
                device["controls"] = controls
                if name in self.settings["scroll_restore"]:
                    self.settings["scroll_restore"].remove(name)
            cfg.save_settings(self.settings)
            if devices:
                self.toast("Found " + ", ".join(cfg.short_name(n) for n in devices))
            else:
                self.toast("No devices found. Check they're switched on and try again.", timeout=5)

        self._run_helper(["detect", str(DETECT_SECONDS)], found, lambda _ok: self.add_card.set_busy(False))

    def _apply(self):
        tmp = os.path.join(GLib.get_user_runtime_dir(), "mouse-tailor-logid.cfg")
        with open(tmp, "w") as f:
            f.write(cfg.to_logid_cfg(self.settings))
        for button in self.apply_buttons:
            button.set_sensitive(False)
        self._set_apply_state("busy")
        self.applied_at = time.strftime("%Y-%m-%d %H:%M:%S")

        def finished(ok):
            if not ok:
                self._set_apply_state("idle")

        # Give logid a moment to find the devices, then look for complaints about the config.
        self._run_helper(["apply", tmp], lambda _out: GLib.timeout_add(1500, self._check_log), finished)

    def _check_log(self):
        log = subprocess.run(["journalctl", "-u", "logid", "--since", self.applied_at, "-o", "cat", "--no-pager"],
                             capture_output=True, text=True).stdout
        problems = [line for line in log.splitlines()
                    if line.startswith(("[ERROR]", "[WARN]")) and "Unsupported device" not in line]
        if problems:
            self.toast(f"Applied, but LogiOps reported: {problems[0]}", timeout=8)
            self._set_apply_state("idle")
        else:
            self.toast("Applied. Your devices are using the new settings.")
            self._set_apply_state("done")
            GLib.timeout_add(1600, lambda: self._set_apply_state("idle") and False)
        self.banner.set_revealed(False)
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _preview(self, _row):
        text = Gtk.TextView(editable=False, monospace=True, cursor_visible=False,
                            top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        text.get_buffer().set_text(cfg.to_logid_cfg(self.settings))
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=text, vexpand=True))
        view.add_top_bar(Adw.HeaderBar())
        Adw.Dialog(title="Config Preview", child=view, content_width=640, content_height=560).present(self)

    def _about(self, _row):
        Adw.AboutDialog(
            application_name="Mouse Tailor", application_icon="input-mouse", version=VERSION,
            comments="Tailor your Logitech mouse: buttons, gestures, scrolling and battery. "
                     "Mouse Tailor is a front end for LogiOps (github.com/PixlOne/logiops).\n\n"
                     "Not affiliated with or endorsed by Logitech.",
            license_type=Gtk.License.MIT_X11,
            website="https://github.com/faizankhan63/mouse-tailor",
            issue_url="https://github.com/faizankhan63/mouse-tailor/issues",
        ).present(self)


class GObjectBlock:
    """Blocks a signal handler for the duration of a with-block, so code changes don't look like user changes."""

    def __init__(self, obj, handler):
        self.obj, self.handler = obj, handler

    def __enter__(self):
        self.obj.handler_block(self.handler)

    def __exit__(self, *_):
        self.obj.handler_unblock(self.handler)


class MouseTailor(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        w.load_css()

    def do_activate(self):
        window = self.get_active_window() or MainWindow(self)
        window.present()


if __name__ == "__main__":
    sys.exit(MouseTailor().run(sys.argv))
