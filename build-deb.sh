#!/bin/sh
# Builds dist/mouse-tailor_VERSION_all.deb for Ubuntu 24.04+ and Debian 13+.
# The package maintainer defaults to your git name and email; override with
#   MAINTAINER="Your Name <you@example.com>" ./build-deb.sh
set -e

src=$(dirname "$(readlink -f "$0")")
version=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$src/src/mouse_tailor.py")
maintainer=${MAINTAINER:-"$(git config user.name) <$(git config user.email)>"}
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT

"$src/install.sh" --destdir "$root"
mkdir -p "$root/DEBIAN"
cat > "$root/DEBIAN/control" <<EOF
Package: mouse-tailor
Version: $version
Architecture: all
Maintainer: $maintainer
Section: utils
Priority: optional
Installed-Size: $(du -sk "$root/usr" | cut -f1)
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0 (>= 4.14), gir1.2-adw-1 (>= 1.5), python3-evdev, logiops, pkexec | policykit-1, systemd
Recommends: upower, gnome-shell (>= 46)
Conflicts: mouse-setup
Replaces: mouse-setup
Description: Buttons, gestures, scrolling and battery for Logitech mice
 A GNOME settings app for Logitech mice on Linux. Choose what each button
 does, set up hold-and-swipe gestures, change scroll speed and direction,
 and see battery levels in the app, the top bar and Quick Settings.
 It is a front end for LogiOps (logid), which does the work in the background.
 .
 Not affiliated with or endorsed by Logitech.
EOF

mkdir -p "$src/dist"
dpkg-deb --root-owner-group --build "$root" "$src/dist/mouse-tailor_${version}_all.deb"
