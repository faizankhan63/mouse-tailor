// Mouse Tailor: battery levels of mice and other wireless devices in the top bar and Quick Settings.
// Battery data comes from UPower over D-Bus, the same way GNOME Shell's own power indicator gets it.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';

const APP_ID = 'io.github.mousetailor.MouseTailor';

const UPowerProxy = Gio.DBusProxy.makeProxyWrapper(`
<node>
  <interface name="org.freedesktop.UPower">
    <method name="EnumerateDevices"><arg type="ao" direction="out"/></method>
    <signal name="DeviceAdded"><arg type="o"/></signal>
    <signal name="DeviceRemoved"><arg type="o"/></signal>
  </interface>
</node>`);

const DeviceProxy = Gio.DBusProxy.makeProxyWrapper(`
<node>
  <interface name="org.freedesktop.UPower.Device">
    <property name="Model" type="s" access="read"/>
    <property name="Type" type="u" access="read"/>
    <property name="PowerSupply" type="b" access="read"/>
    <property name="IsPresent" type="b" access="read"/>
    <property name="Percentage" type="d" access="read"/>
    <property name="BatteryLevel" type="u" access="read"/>
    <property name="State" type="u" access="read"/>
  </interface>
</node>`);

// UPower device types that are never peripherals: line power, the laptop's battery, UPS.
const SYSTEM_TYPES = new Set([1, 2, 3]);
const TYPE_ICONS = {5: 'input-mouse-symbolic', 6: 'input-keyboard-symbolic', 12: 'input-gaming-symbolic',
    17: 'audio-headset-symbolic', 19: 'audio-headphones-symbolic'};

// UPower's BatteryLevel: devices that only report a coarse level instead of a percentage.
const LEVEL_NONE = 1;
const LEVELS = {3: ['Low', 0.25], 4: ['Critical', 0.05], 6: ['Normal', 0.5], 7: ['High', 0.75], 8: ['Full', 1]};
const STATE_CHARGING = 1;

function describe(device) {
    if (device.BatteryLevel === LEVEL_NONE || !(device.BatteryLevel in LEVELS))
        return [`${Math.round(device.Percentage)}%`, device.Percentage / 100];
    return LEVELS[device.BatteryLevel];
}

function statusText(device) {
    const [text] = describe(device);
    return device.State === STATE_CHARGING ? `${text}, charging` : text;
}

// Same icon naming as GNOME Shell's own power indicator, so the bolt only shows while charging.
function batteryIcon(device) {
    const fill = Math.min(100, 10 * Math.round(describe(device)[1] * 10));
    return `battery-level-${fill}${device.State === STATE_CHARGING ? '-charging' : ''}-symbolic`;
}

function openApp() {
    Main.panel.closeQuickSettings();
    Gio.DesktopAppInfo.new(`${APP_ID}.desktop`)?.launch([], null);
}

function isLow(device) {
    return describe(device)[1] <= 0.25 && device.State !== STATE_CHARGING;
}

const BatteryToggle = GObject.registerClass(
class BatteryToggle extends QuickMenuToggle {
    _init(extension) {
        super._init({
            title: 'Mouse Tailor',
            iconName: 'input-mouse-symbolic',
            toggleMode: true,
        });
        this._extension = extension;
        this.menu.setHeader('input-mouse-symbolic', 'Device batteries', 'The switch shows them in the top bar');

        this._devicesSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._devicesSection);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Mouse Tailor Settings', openApp);
    }

    update(devices) {
        this._devicesSection.removeAll();
        for (const device of devices) {
            const item = new PopupMenu.PopupImageMenuItem(device.Model || 'Wireless device',
                TYPE_ICONS[device.Type] ?? 'battery-symbolic');
            item.connect('activate', openApp);
            const level = new St.Label({
                text: statusText(device),
                x_expand: true,
                x_align: Clutter.ActorAlign.END,
                style_class: isLow(device) ? 'mouse-tailor-level mouse-tailor-low' : 'mouse-tailor-level',
            });
            item.add_child(level);
            item.add_child(new St.Icon({icon_name: batteryIcon(device), style_class: 'popup-menu-icon'}));
            this._devicesSection.addMenuItem(item);
        }
        const lowest = devices[0];
        this.subtitle = lowest ? `${lowest.Model} · ${statusText(lowest)}` : null;
        this.visible = devices.length > 0;
    }
});

const BatteryIndicator = GObject.registerClass(
class BatteryIndicator extends SystemIndicator {
    _init(extension) {
        super._init();
        this._settings = extension.getSettings();

        // Top bar: the device's icon, its battery icon, and a percentage when the device reports one.
        this._deviceIcon = this._addIndicator();
        this._batteryIcon = this._addIndicator();
        this._label = new St.Label({style_class: 'mouse-tailor-panel-label', y_align: Clutter.ActorAlign.CENTER});
        this.add_child(this._label);

        this._toggle = new BatteryToggle(extension);
        this._toggle.connect('destroy', () => (this._toggle = null));
        this._settings.bind('show-in-top-bar', this._toggle, 'checked', Gio.SettingsBindFlags.DEFAULT);
        this._settings.connectObject('changed::show-in-top-bar', () => this._sync(), this);
        this.quickSettingsItems.push(this._toggle);

        this.connect('destroy', () => this._onDestroy());
        this._devices = new Map();
        this._upower = new UPowerProxy(Gio.DBus.system, 'org.freedesktop.UPower', '/org/freedesktop/UPower',
            (proxy, error) => {
                if (this._destroyed)
                    return;
                if (error) {
                    console.error(`Mouse Tailor: can't reach UPower: ${error.message}`);
                    return;
                }
                this._signals = [
                    proxy.connectSignal('DeviceAdded', (_p, _sender, [path]) => this._addDevice(path)),
                    proxy.connectSignal('DeviceRemoved', (_p, _sender, [path]) => this._removeDevice(path)),
                ];
                proxy.EnumerateDevicesRemote(result => {
                    if (!this._destroyed)
                        result?.[0]?.forEach(path => this._addDevice(path));
                });
            });
        this._sync();
    }

    _addDevice(path) {
        if (this._devices.has(path))
            return;
        const proxy = new DeviceProxy(Gio.DBus.system, 'org.freedesktop.UPower', path, (p, error) => {
            if (!error && !this._destroyed)
                this._sync();
        });
        proxy.connectObject('g-properties-changed', () => this._sync(), this);
        this._devices.set(path, proxy);
    }

    _removeDevice(path) {
        this._devices.get(path)?.disconnectObject(this);
        this._devices.delete(path);
        this._sync();
    }

    _peripherals() {
        return [...this._devices.values()]
            .filter(d => d.Type !== undefined && !SYSTEM_TYPES.has(d.Type) && !d.PowerSupply && d.IsPresent)
            .sort((a, b) => describe(a)[1] - describe(b)[1]);
    }

    _sync() {
        if (this._destroyed)
            return;
        const devices = this._peripherals();
        this._toggle.update(devices);

        const lowest = devices[0];
        const show = Boolean(lowest) && this._settings.get_boolean('show-in-top-bar');
        this._deviceIcon.visible = show;
        this._batteryIcon.visible = show;
        this._label.visible = show && lowest.BatteryLevel === LEVEL_NONE;
        if (!lowest)
            return;
        this._deviceIcon.icon_name = TYPE_ICONS[lowest.Type] ?? 'battery-symbolic';
        this._batteryIcon.icon_name = batteryIcon(lowest);
        this._label.text = describe(lowest)[0];
        for (const actor of [this._deviceIcon, this._batteryIcon, this._label]) {
            if (isLow(lowest))
                actor.add_style_class_name('mouse-tailor-low');
            else
                actor.remove_style_class_name('mouse-tailor-low');
        }
    }

    // Runs however the indicator goes away: disable() or the shell shutting down.
    _onDestroy() {
        this._destroyed = true;
        this._signals?.forEach(id => this._upower.disconnectSignal(id));
        for (const proxy of this._devices.values())
            proxy.disconnectObject(this);
        this._devices.clear();
        this._upower = null;
        this._settings.disconnectObject(this);
        // When the shell shuts down it destroys the Quick Settings tile itself, first.
        this._toggle?.destroy();
    }
});

export default class MouseTailorExtension extends Extension {
    enable() {
        this._indicator = new BatteryIndicator(this);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    disable() {
        this._indicator.destroy();
        this._indicator = null;
    }
}
