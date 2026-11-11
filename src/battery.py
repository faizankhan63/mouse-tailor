"""Battery levels of wireless devices, read from UPower over D-Bus."""

from dataclasses import dataclass

from gi.repository import Gio, GLib, GObject

import logid_config as cfg

UPOWER = "org.freedesktop.UPower"

# UPower device types that are never peripherals: line power, the laptop's battery, UPS.
SYSTEM_TYPES = {1, 2, 3}
# UPower's BatteryLevel for devices that only report a coarse level instead of a percentage.
LEVEL_NONE = 1
LEVELS = {3: ("Low", 0.25), 4: ("Critical", 0.05), 6: ("Normal", 0.5), 7: ("High", 0.75), 8: ("Full", 1.0)}
STATE_CHARGING = 1


@dataclass
class Battery:
    model: str
    text: str        # "Full", "62%"
    fraction: float  # 0..1, approximate for devices that only report a level
    charging: bool

    @property
    def low(self):
        return self.fraction <= 0.25 and not self.charging

    @property
    def summary(self):
        return f"{self.text}, charging" if self.charging else self.text


class BatteryMonitor(GObject.Object):
    """Keeps a live list of peripheral batteries and emits "changed" when any of them changes."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self._devices = {}
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, UPOWER,
                                  "/org/freedesktop/UPower", UPOWER, None, self._upower_ready)

    def _upower_ready(self, _source, result):
        try:
            self._upower = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return  # No UPower: battery levels just don't show.
        self._upower.connect("g-signal", self._upower_signal)
        self._upower.call("EnumerateDevices", None, Gio.DBusCallFlags.NONE, -1, None, self._enumerated)

    def _enumerated(self, proxy, result):
        try:
            (paths,) = proxy.call_finish(result).unpack()
        except GLib.Error:
            return
        for path in paths:
            self._add(path)

    def _upower_signal(self, _proxy, _sender, signal, params):
        (path,) = params.unpack()
        if signal == "DeviceAdded":
            self._add(path)
        elif signal == "DeviceRemoved" and self._devices.pop(path, None):
            self.emit("changed")

    def _add(self, path):
        def ready(_source, result):
            try:
                proxy = Gio.DBusProxy.new_for_bus_finish(result)
            except GLib.Error:
                return
            proxy.connect("g-properties-changed", lambda *_: self.emit("changed"))
            self._devices[path] = proxy
            self.emit("changed")

        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, UPOWER, path,
                                  f"{UPOWER}.Device", None, ready)

    @staticmethod
    def _battery(proxy):
        def prop(name, default=None):
            value = proxy.get_cached_property(name)
            return value.unpack() if value is not None else default

        if prop("Type") in SYSTEM_TYPES or prop("PowerSupply", True) or not prop("IsPresent", False):
            return None
        level = prop("BatteryLevel", 0)
        if level == LEVEL_NONE or level not in LEVELS:
            percentage = prop("Percentage", 0.0)
            text, fraction = f"{round(percentage)}%", percentage / 100
        else:
            text, fraction = LEVELS[level]
        return Battery(prop("Model", "") or "Wireless device", text, fraction, prop("State") == STATE_CHARGING)

    def batteries(self):
        return [b for b in map(self._battery, self._devices.values()) if b]

    def for_device(self, name):
        """The battery of a LogiOps device, matched by name ("M720 Triathlon" in UPower)."""
        words = cfg._words(name)
        for battery in self.batteries():
            model = cfg._words(battery.model)
            if model and words and (model <= words or words <= model):
                return battery
        return None
