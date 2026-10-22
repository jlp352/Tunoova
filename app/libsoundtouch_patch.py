#!/usr/bin/env python3
"""
Patched version of libsoundtouch utils to fix compatibility issues with newer zeroconf versions

Repris de Examples/RadioPodcastPlayer/libsoundtouch_patch.py : libsoundtouch
(derniere publication 2019) utilise une API zeroconf obsolete, ce module la
rend compatible avec les versions actuelles - utilise par app/cast_service.py
pour la decouverte reseau des enceintes Bose SoundTouch (CDC §7/§8/§10).
"""

import socket
from zeroconf import ServiceBrowser, Zeroconf
from libsoundtouch import soundtouch_device


class PatchedSoundtouchDeviceListener:
    """
    Patched device listener that includes the required update_service method
    and fixes the address/addresses compatibility issue
    """

    def __init__(self):
        self.devices = []

    def remove_service(self, zeroconf, type, name):
        pass

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        if info:
            try:
                # Fix for newer zeroconf versions - use addresses instead of address
                if hasattr(info, 'addresses') and info.addresses:
                    address = socket.inet_ntoa(info.addresses[0])
                elif hasattr(info, 'address'):
                    # Fallback for older versions
                    address = socket.inet_ntoa(info.address)
                else:
                    return

                port = info.port
                device = soundtouch_device(address, port)
                self.devices.append(device)
            except Exception as e:
                # Silently ignore devices that can't be connected to
                pass

    def update_service(self, zeroconf, type, name):
        """
        Required method for newer zeroconf versions.
        Can be empty if we don't need to handle service updates.
        """
        pass


def discover_devices_patched(timeout=5):
    """
    Patched version of discover_devices that uses the fixed listener
    """
    zeroconf = Zeroconf()
    listener = PatchedSoundtouchDeviceListener()

    try:
        browser = ServiceBrowser(zeroconf, "_soundtouch._tcp.local.", listener)

        # Wait for discovery
        import time
        time.sleep(timeout)

        return listener.devices
    finally:
        zeroconf.close()
