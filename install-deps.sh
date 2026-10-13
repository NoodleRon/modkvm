#!/bin/sh
# Installs Portabotty's system dependencies (Debian/Ubuntu). See README.md > Dependencies.
set -e
sudo apt update
sudo apt install -y bluez python3-dbus python3-gi python3-evdev ffmpeg v4l-utils python3-pil python3-numpy qrencode
