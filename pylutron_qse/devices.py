"""Devices supported by Lutron QSE network interface (QSE-CI-NWK-E)."""

import datetime
import logging
from typing import Callable, Optional

_LOG = logging.getLogger('devices')
_LOG.setLevel(logging.DEBUG)

# Timeout and retry contants.
_SIMULTANEOUS_EVENT_INTERVAL = datetime.timedelta(milliseconds=100)

# Actions/States
_ACTION_STOP = b'20'
_STATE_LEVEL = b'14'
_STATE_MOVEMENT = b'21'
_STATE_MOVEMENT_STOPPED_VALUE = b'0'

ROLLER_STATES = [_STATE_LEVEL]
ALL_STATES = set(ROLLER_STATES)


class Device(object):
    """Base class for devices."""

    def __init__(self, qse, serial_number, integration_id=None):
        """Create a device instance."""
        self._qse = qse
        self._serial_number = serial_number
        self._integration_id = integration_id
        self._callbacks = []

    def __str__(self):
        """Generate debug string for device."""
        return 'Device "%s"' % self._debug_name()

    def _debug_name(self):
        if self._integration_id:
            return self._integration_id.decode('ascii')
        return self._serial_number.decode('ascii')

    @property
    def serial_number(self) -> str:
        """Return serial number for device."""
        return self._serial_number.decode('ascii')

    @property
    def integration_id(self) -> Optional[str]:
        """Return integration id for device which may be None."""
        if self._integration_id:
            return self._integration_id.decode('ascii')
        return None

    def connected(self) -> bool:
        """Return true if the devices connection is active."""
        return self._qse.connected()

    def add_subscriber(self, callback: Callable[[], None]) -> None:
        """Receive a callback when device state changes."""
        self._callbacks.append(callback)

    def _notify_subscribers(self):
        for callback in self._callbacks:
            callback()

    def _handle_response(self, response):
        pass


class Roller(Device):
    """Roller device."""

    def __init__(self, qse, serial_number, integration_id=None):
        """Create a Roller instance."""
        super().__init__(qse, serial_number, integration_id)
        self._target_level = 0
        self._current_level = 0
        self._moving = False
        self._last_level_update = None
        self._last_movement_update = None

    def __str__(self):
        """Generate debug string for a roller instance."""
        return 'Roller "%s" target_level: %d current_level: %d moving: %s' % (
            self._debug_name(), self._target_level, self._current_level,
            self._moving)

    @property
    def target_level(self) -> int:
        """Return the target level of the roller."""
        return self._target_level

    @property
    def current_level(self) -> int:
        """Return the current level of the roller."""
        return self._current_level

    @property
    def opening(self) -> bool:
        """Return True if the roller is currently opening."""
        return self._moving and self._target_level > self._current_level

    @property
    def closing(self) -> bool:
        """Return True if the roller is currently closing."""
        return self._moving and self._target_level < self._current_level

    def set_target_level(self, level: int) -> None:
        """Set the target level of the roller."""
        # pylint: disable=protected-access
        level_bytes = bytes(str(level), encoding='ascii')
        self._qse._make_device_request(
            self._serial_number, _STATE_LEVEL, level_bytes)

    def close(self) -> None:
        """Close the roller."""
        self.set_target_level(0)

    def open(self) -> None:
        """Open the roller."""
        self.set_target_level(100)

    def stop(self) -> None:
        """Stop the roller."""
        # pylint: disable=protected-access
        self._qse._make_device_request(self._serial_number, _ACTION_STOP)

    def _set_state(self, target_level, current_level, moving):
        if (self._target_level != target_level or
                self._current_level != current_level or
                self._moving != moving):
            self._target_level = target_level
            self._current_level = current_level
            self._moving = moving
            _LOG.info('New state: ' + str(self))
            self._notify_subscribers()

    def _handle_response(self, response):
        assert len(response) >= 5
        id_or_sn = response[1]
        state = response[3]
        value = response[4]
        assert (id_or_sn == self._serial_number or
                id_or_sn == self._integration_id)
        if state == _STATE_LEVEL:
            new_level = int(round(float(value)))
            self._handle_level(new_level)
        if state == _STATE_MOVEMENT:
            self._handle_movement(value)

    def _handle_level(self, new_level):
        first_level_update = self._last_level_update is None
        self._last_level_update = datetime.datetime.now()
        if (first_level_update or new_level == self._current_level or
                self._received_simultaneous_stop()):
            self._set_state(new_level, new_level, False)
        else:
            self._set_state(new_level, self._current_level, True)

    def _handle_movement(self, value):
        self._last_movement_update = datetime.datetime.now()
        if value in _STATE_MOVEMENT_STOPPED_VALUE:
            self._set_state(self._target_level, self._target_level, False)

    def _received_simultaneous_stop(self):
        if not self._last_movement_update:
            return False
        now = datetime.datetime.now()
        return (now - self._last_movement_update <
                _SIMULTANEOUS_EVENT_INTERVAL)
