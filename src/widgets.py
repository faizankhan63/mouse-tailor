"""Custom widgets: battery ring and bar, device cards, the Apply button, and page scaffolding."""

import math
import os

import gi

gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

# Libadwaita's success / warning / error colours, used for battery levels.
LEVEL_COLORS = [(0.25, (0.88, 0.11, 0.14)), (0.5, (0.90, 0.65, 0.04)), (1.01, (0.18, 0.76, 0.49))]


def load_css():
    provider = Gtk.CssProvider()
    provider.load_from_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css"))
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def level_color(fraction):
    return next(color for limit, color in LEVEL_COLORS if fraction < limit)


def animate(widget, start, end, callback, duration=900):
    """Runs callback(value) from start to end with an ease-out curve. Respects reduced-motion settings."""
    animation = Adw.TimedAnimation.new(widget, start, end, duration, Adw.CallbackAnimationTarget.new(callback))
    animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
    animation.play()
    return animation


def page(*children, max_width=640):
    """A scrolling, centred column of widgets, the layout every page uses."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                  margin_top=24, margin_bottom=32, margin_start=12, margin_end=12)
    for child in children:
        box.append(child)
    return Gtk.ScrolledWindow(child=Adw.Clamp(child=box, maximum_size=max_width), vexpand=True,
                              hscrollbar_policy=Gtk.PolicyType.NEVER)


def reveal(child, revealed):
    """Wraps a widget so showing and hiding it slides instead of jumping. Put these in a column with no
    spacing: the gap above the widget is part of what slides, so a hidden one takes no room."""
    child.set_margin_top(24)
    return Gtk.Revealer(child=child, reveal_child=revealed, transition_duration=250,
                        transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)


class BatteryRing(Gtk.Widget):
    """A ring around the device badge that fills up to the battery level. Drawn with GSK paths, so no cairo."""

    LINE = 7

    def __init__(self, size):
        super().__init__()
        self.size = size
        self.fraction = None
        self.shown = 0.0
        self.animation = None

    def do_measure(self, _orientation, _for_size):
        return self.size, self.size, -1, -1

    def set_fraction(self, fraction):
        if fraction == self.fraction:
            return
        self.fraction = fraction
        if self.animation:
            self.animation.skip()
        self.animation = animate(self, self.shown, fraction or 0.0, self._step, duration=1100)

    def _step(self, value):
        self.shown = value
        self.queue_draw()

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - self.LINE / 2 - 1
        stroke = Gsk.Stroke.new(self.LINE)
        stroke.set_line_cap(Gsk.LineCap.ROUND)

        track = Gsk.PathBuilder.new()
        track.add_circle(Graphene.Point().init(cx, cy), radius)
        fg = self.get_color()
        snapshot.append_stroke(track.to_path(), stroke, Gdk.RGBA(red=fg.red, green=fg.green, blue=fg.blue, alpha=0.1))
        if self.fraction is None or self.shown <= 0.005:
            return

        # Clockwise from the top. A full ring is drawn as a circle, since an arc can't end where it starts.
        level = Gsk.PathBuilder.new()
        fill = min(self.shown, 1.0)
        if fill >= 0.999:
            level.add_circle(Graphene.Point().init(cx, cy), radius)
        else:
            angle = -math.pi / 2 + 2 * math.pi * fill
            level.move_to(cx, cy - radius)
            level.svg_arc_to(radius, radius, 0, fill > 0.5, True, cx + radius * math.cos(angle),
                             cy + radius * math.sin(angle))
        red, green, blue = level_color(self.fraction)
        snapshot.append_stroke(level.to_path(), stroke, Gdk.RGBA(red=red, green=green, blue=blue, alpha=1))


class DeviceBadge(Gtk.Overlay):
    """The device icon in an accent-coloured circle, optionally inside a battery ring."""

    def __init__(self, icon_name, size=96, ring=True):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        inner = int(size * 0.72) if ring else size
        badge = Gtk.Image(icon_name=icon_name, pixel_size=int(inner * 0.45), css_classes=["device-badge"],
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        badge.set_size_request(inner, inner)
        self.ring = BatteryRing(size) if ring else None
        self.set_child(self.ring or badge)
        if self.ring:
            self.add_overlay(badge)


class BatteryBar(Gtk.Box):
    """A slim level bar with the level as text, animating when the level changes."""

    def __init__(self):
        super().__init__(spacing=10, css_classes=["battery-bar"])
        self.bar = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True, valign=Gtk.Align.CENTER)
        self.bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_LOW)
        self.bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH)
        self.bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_FULL)
        self.bar.add_offset_value("low", 0.26)
        self.bar.add_offset_value("high", 1.0)
        self.label = Gtk.Label(css_classes=["caption", "numeric", "dim-label"])
        self.icon = Gtk.Image(icon_name="battery-level-100-symbolic", css_classes=["dim-label"])
        self.append(self.icon)
        self.append(self.bar)
        self.append(self.label)
        self.animation = None

    def set_battery(self, battery):
        self.set_visible(battery is not None)
        if not battery:
            return
        self.label.set_label(battery.summary)
        fill = min(100, 10 * round(battery.fraction * 10))
        self.icon.set_from_icon_name(f"battery-level-{fill}{'-charging' if battery.charging else ''}-symbolic")
        if self.animation:
            self.animation.skip()
        self.animation = animate(self, self.bar.get_value(), battery.fraction, self.bar.set_value)


class DeviceCard(Gtk.Button):
    """A device on the main page: icon, name, connection status and battery."""

    def __init__(self, name, icon_name):
        super().__init__(css_classes=["card", "device-card"], hexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        top = Gtk.Box(spacing=14)
        top.append(DeviceBadge(icon_name, size=52, ring=False))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        titles.append(Gtk.Label(label=name, xalign=0, wrap=True, css_classes=["heading"]))
        status = Gtk.Box(spacing=6)
        self.dot = Gtk.Box(css_classes=["status-dot"], valign=Gtk.Align.CENTER)
        self.status = Gtk.Label(xalign=0, css_classes=["caption", "dim-label"])
        status.append(self.dot)
        status.append(self.status)
        titles.append(status)
        top.append(titles)
        top.append(Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim-label"], valign=Gtk.Align.CENTER))
        box.append(top)

        self.summary = Gtk.Label(xalign=0, wrap=True, css_classes=["caption", "dim-label"])
        box.append(self.summary)
        self.battery = BatteryBar()
        box.append(self.battery)
        self.set_child(box)

    def update(self, connected, summary, battery):
        self.dot.set_css_classes(["status-dot", "connected" if connected else "offline"])
        self.status.set_label("Connected" if connected else "Not connected right now")
        self.summary.set_label(summary)
        self.battery.set_battery(battery)


class AddDeviceCard(Gtk.Button):
    """The dashed "Find devices" card."""

    def __init__(self):
        super().__init__(css_classes=["card", "add-card"], hexpand=True)
        box = Gtk.Box(spacing=14)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Gtk.Image(icon_name="list-add-symbolic", pixel_size=22), "icon")
        self.stack.add_named(Gtk.Spinner(spinning=True, width_request=22, height_request=22), "busy")
        badge = Gtk.Box(css_classes=["add-badge"], valign=Gtk.Align.CENTER)
        badge.append(self.stack)
        box.append(badge)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(label="Find devices", xalign=0, css_classes=["heading"])
        titles.append(self.title)
        titles.append(Gtk.Label(label="Reads the buttons of each connected Logitech device. Asks for your password.",
                                xalign=0, wrap=True, css_classes=["caption", "dim-label"]))
        box.append(titles)
        self.set_child(box)

    def set_busy(self, busy):
        self.stack.set_visible_child_name("busy" if busy else "icon")
        self.title.set_label("Looking for devices…" if busy else "Find devices")
        self.set_sensitive(not busy)


class ApplyButton(Gtk.Button):
    """Apply, which turns into a spinner while working and a check mark when done."""

    def __init__(self, on_click):
        super().__init__(css_classes=["suggested-action", "apply-button"])
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=200,
                               hhomogeneous=True)
        self.stack.add_named(Gtk.Label(label="Apply"), "idle")
        self.stack.add_named(Gtk.Spinner(spinning=True), "busy")
        self.stack.add_named(Gtk.Image(icon_name="object-select-symbolic"), "done")
        self.set_child(self.stack)
        self.connect("clicked", lambda _b: on_click())

    def set_state(self, state):
        self.stack.set_visible_child_name(state)


def stagger(widgets, delay=70):
    """Fades widgets in one after another; widgets are Gtk.Revealers created hidden."""
    for i, revealer in enumerate(widgets):
        GLib.timeout_add(80 + i * delay, lambda r=revealer: r.set_reveal_child(True) and False)
