#!/bin/sh
# Installs Mouse Tailor system-wide (asks for your password), or removes it.
#   ./install.sh                 install into /usr
#   ./install.sh --uninstall     remove it again
#   ./install.sh --destdir DIR   lay the files out under DIR (used by build-deb.sh)
set -e

ID=io.github.mousetailor.MouseTailor
UUID=mouse-tailor@mousetailor.github.io
src=$(dirname "$(readlink -f "$0")")

FILES="
usr/bin/mouse-tailor
usr/libexec/mouse-tailor/mouse-tailor-helper
usr/share/polkit-1/actions/$ID.policy
usr/share/applications/$ID.desktop
usr/share/metainfo/$ID.metainfo.xml
"
DIRS="usr/share/mouse-tailor usr/libexec/mouse-tailor usr/share/gnome-shell/extensions/$UUID"

stage() {
    dest=$1
    for f in mouse_tailor.py logid_config.py battery.py widgets.py dialogs.py style.css; do
        install -Dm644 "$src/src/$f" "$dest/usr/share/mouse-tailor/$f"
    done
    install -Dm755 "$src/data/mouse-tailor"             "$dest/usr/bin/mouse-tailor"
    install -Dm755 "$src/data/mouse-tailor-helper"      "$dest/usr/libexec/mouse-tailor/mouse-tailor-helper"
    install -Dm644 "$src/data/$ID.policy"               "$dest/usr/share/polkit-1/actions/$ID.policy"
    install -Dm644 "$src/data/$ID.desktop"              "$dest/usr/share/applications/$ID.desktop"
    install -Dm644 "$src/data/$ID.metainfo.xml"         "$dest/usr/share/metainfo/$ID.metainfo.xml"

    ext="$dest/usr/share/gnome-shell/extensions/$UUID"
    for f in extension.js metadata.json stylesheet.css; do
        install -Dm644 "$src/extension/$f" "$ext/$f"
    done
    install -Dm644 "$src/extension/schemas/org.gnome.shell.extensions.mouse-tailor.gschema.xml" \
        "$ext/schemas/org.gnome.shell.extensions.mouse-tailor.gschema.xml"
    glib-compile-schemas "$ext/schemas"
}

case "$1" in
--destdir)
    stage "$2"
    ;;
--uninstall)
    for f in $FILES; do sudo rm -f "/$f"; done
    for d in $DIRS; do sudo rm -rf "/${d:?}"; done
    echo "Mouse Tailor removed. Your settings are still in ~/.config/mouse-tailor and /etc/logid.cfg."
    ;;
"")
    for cmd in logid pkexec glib-compile-schemas; do
        command -v "$cmd" >/dev/null || echo "Warning: '$cmd' isn't installed; see the README for dependencies."
    done
    tmp=$(mktemp -d)
    stage "$tmp"
    sudo cp -r "$tmp"/usr /
    rm -rf "$tmp"
    sudo update-desktop-database -q /usr/share/applications 2>/dev/null || true
    echo "Installed. Open \"Mouse Tailor\" from your app menu."
    echo "To see batteries in the top bar, turn it on in Mouse Tailor, then log out and back in."
    ;;
*)
    echo "usage: $0 [--uninstall | --destdir DIR]" >&2
    exit 2
    ;;
esac
