"""API for Lutron QSE network interface (QSE-CI-NWK-E)."""

import datetime
import logging
import socket
import telnetlib
import time
from threading import Thread, Lock
from typing import List

from pylutron_qse.devices import (ALL_STATES, Device, Roller)

_LOG = logging.getLogger('qse')
_LOG.setLevel(logging.DEBUG)

# Timeout and retry contants.
_TIMEOUT = 3
_LOGIN_ATTEMPT_INTERVAL = datetime.timedelta(seconds=60)
_DEVICE_DISCOVERY_INTERVAL = datetime.timedelta(seconds=10 * 60)
_MONITOR_INTERVAL = datetime.timedelta(milliseconds=50)

# Misc QSE strings.
_DELIMITER = b','
_DEFAULT_COMPONENT = b'0'
_DEFAULT_USERNAME = 'nwk'
_EOL = b'\r\n'
_ID_ALL_DEVICES = b'ALL_DEVICES'
_LOGIN_PROMPT = b'login: '
_LOGIN_SUCCESS = b'connection established' + _EOL
_PROMPT = b'QSE>'

# Operations
_OP_COMMAND = b'#'
_OP_REQUEST = b'?'
_OP_RESPONSE = b'~'

# Commands
_CMD_DETAILS = b'DETAILS'
_CMD_DEVICE = b'DEVICE'

# Details request/response strings
_DETAILS_INTEGRATION_ID = b'INTEGRATIONID:'
_DETAILS_INTEGRATION_ID_NOT_SET = b'(Not Set)'
_DETAILS_PRODUCT = b'PRODUCT:'
_DETAILS_PRODUCT_ROLLERS = [b'ROLLER(1)']
_DETAILS_SERIAL_NUMBER = b'SN:'


def _command_from_parts(cmd_parts):
    return _DELIMITER.join(cmd_parts) + _EOL


class QSE(object):
    """
    Lutron QSE network interface (QSE-CI-NWK-E).

    See Lutron integration protocol (search: "QS Standalone"):
    http://www.lutron.com/TechnicalDocumentLibrary/040249.pdf
    """

    def __init__(self, hostname=None, username=_DEFAULT_USERNAME):
        """Consructor for QSE."""
        self._hostname = hostname
        self._username = username
        self._telnet = None
        self._telnet_lock = Lock()
        self._devices = {}
        self._last_login_attempt = None
        self._last_device_discovery = None

        # Initial login and device discovery.
        self._telnet = self._lock_and_do(self._login)
        self._lock_and_do_if_connected(self._load_devices)

        # Start background monitoring thread.
        monitor = Thread(target=self._monitor)
        monitor.setDaemon(True)
        monitor.start()

    def connected(self) -> bool:
        """Return True if connection to QSE is active."""
        return self._telnet is not None

    def devices(self) -> List[Device]:
        """Return all devices."""
        return self._devices.values()

    def rollers(self) -> List[Roller]:
        """Return all roller devices."""
        return [r for r in self._devices.values() if isinstance(r, Roller)]

    def _lock_and_do_if_connected(self, callback, *args):
        return self._lock_and_do(callback, True, *args)

    def _lock_and_do(self, callback, test_connected=False, *args):
        self._telnet_lock.acquire()
        if test_connected and not self._telnet:
            self._telnet_lock.release()
            return
        result = callback(*args)
        self._telnet_lock.release()
        return result

    def _login(self):
        """Open connection to telnet."""
        self._last_login_attempt = datetime.datetime.now()
        _LOG.debug('Logging in to Lutron QSE')
        try:
            telnet = telnetlib.Telnet(self._hostname, timeout=_TIMEOUT)
            response = telnet.read_until(_LOGIN_PROMPT, timeout=_TIMEOUT)
            assert response == _LOGIN_PROMPT, response
            telnet.write(bytes(self._username, encoding='ascii') + _EOL)
            response = telnet.read_until(_LOGIN_SUCCESS, timeout=_TIMEOUT)
            assert response == _LOGIN_SUCCESS, response
        except (socket.error, socket.gaierror, socket.timeout, EOFError):
            _LOG.error('Failed to connect to Lutron QSE.'
                       'Retry in %d seconds.', _LOGIN_ATTEMPT_INTERVAL)
            return None
        _LOG.info("Logged in to Lutron QSE")
        return telnet

    def _close(self):
        """Close connection to telnet."""
        _LOG.debug("Closing connection to Lutron QSE")
        self._telnet.close()
        self._telnet = None
        _LOG.info("Connection to Lutron QSE closed")

    def _load_devices(self):
        """Load all devices."""
        self._last_device_discovery = datetime.datetime.now()
        # Details query to initialize all devices.
        result = self._exec(_command_from_parts(
            [_OP_REQUEST + _CMD_DETAILS, _ID_ALL_DEVICES]))
        for response in result:
            assert isinstance(response, list), response
            assert response, response
            self._init_device(response)

        # Query all device states.
        for state in ALL_STATES:
            result = self._exec(_command_from_parts(
                [_OP_REQUEST + _CMD_DEVICE, _ID_ALL_DEVICES,
                 _DEFAULT_COMPONENT, state]))
            for response in result:
                assert isinstance(response, list), response
                assert response, response
                if response[0] != _OP_RESPONSE + _CMD_DEVICE:
                    _LOG.warning(
                        'Ignoring unexpected response: ' + str(response))
                    continue
                self._route_device_response(response)

    def _monitor(self):
        _LOG.debug('Entering background monitoring thread.')
        while True:
            now = datetime.datetime.now()
            # Try login if we are not connected.
            if not self._telnet:
                next_login = self._last_login_attempt + _LOGIN_ATTEMPT_INTERVAL
                if now < next_login:
                    time.sleep((next_login - now).total_seconds())
                    continue
                else:
                    self._telnet = self._lock_and_do(self._login)

            # Discover devices.
            if now > self._last_device_discovery + _DEVICE_DISCOVERY_INTERVAL:
                self._lock_and_do_if_connected(self._load_devices)

            # Read events.
            self._lock_and_do_if_connected(self._read_events)
            time.sleep(float(_MONITOR_INTERVAL.microseconds) / 1e6)
        _LOG.debug('Exiting background monitoring thread.')

    def _read_events(self):
        """Read events."""
        result = self._read_if_available()
        for response in result:
            assert isinstance(response, list), response
            assert response, response
            if response[0] == _OP_RESPONSE + _CMD_DEVICE:
                self._route_device_response(response)

    def _init_device(self, response):
        if not response or response[0] != _OP_RESPONSE + _CMD_DETAILS:
            _LOG.warning('Ignoring unexpected response: ' + str(response))
            return
        assert len(response) >= 5, response
        assert response[1].startswith(_DETAILS_SERIAL_NUMBER), response
        assert response[2].startswith(_DETAILS_INTEGRATION_ID), response
        assert response[4].startswith(_DETAILS_PRODUCT), response
        serial_number = response[1][len(_DETAILS_SERIAL_NUMBER):]
        integration_id = response[2][len(_DETAILS_INTEGRATION_ID):]
        product = response[4][len(_DETAILS_PRODUCT):]
        if serial_number in self._devices:
            return
        if integration_id == _DETAILS_INTEGRATION_ID_NOT_SET:
            integration_id = None

        if product in _DETAILS_PRODUCT_ROLLERS:
            device = Roller(self, serial_number, integration_id)
        else:
            device = Device(self, serial_number, integration_id)
        assert device
        self._devices[serial_number] = device

    def _route_device_response(self, response):
        assert len(response) >= 2, response
        assert response[0] == _OP_RESPONSE + _CMD_DEVICE, response
        id_or_sn = response[1]
        if id_or_sn in self._devices:
            # Serial number
            # pylint: disable=protected-access
            self._devices[id_or_sn]._handle_response(response)
            return
        else:
            # Integration id
            id_string = id_or_sn.decode('ascii')
            devices_with_id = [d for d in self._devices.values()
                               if d.integration_id == id_string]
            if devices_with_id:
                assert len(devices_with_id) == 1
                # pylint: disable=protected-access
                devices_with_id[0]._handle_response(response)
                return
        _LOG.debug('Event for unknown device: ' + str(response))

    def _make_device_request(self, serial_number, action, value=None):
        """Change the state of a device."""
        cmd_parts = [_OP_COMMAND + _CMD_DEVICE,
                     serial_number, _DEFAULT_COMPONENT, action]
        if value:
            cmd_parts.append(value)
        cmd = _command_from_parts(cmd_parts)
        result = self._lock_and_do_if_connected(self._exec, cmd)
        for response in result:
            assert isinstance(response, list), response
            assert response, response
            if response[0] == _OP_RESPONSE + _CMD_DEVICE:
                self._route_device_response(response)

    def _exec(self, cmd):
        """Issue a command and parse the response.

        Caller must hold _telnet_lock.
        """
        self._flush()
        if not self._telnet:
            return []
        self._write(cmd)
        if not self._telnet:
            return []
        result = self._read_until_prompt()
        return result

    def _flush(self):
        """Flush unread data.

        Caller must hold _telnet_lock.
        """
        try:
            while True:
                data = self._telnet.read_eager()
                if not data:
                    break
        except (socket.error, EOFError):
            _LOG.error('Connection to Lutron QSE lost.')
            self._close()

    def _write(self, data):
        """Write data to telnet.

        Caller must hold _telnet_lock.
        """
        try:
            self._telnet.write(data)
        except (socket.error, EOFError):
            _LOG.error('Connection to Lutron QSE lost.')
            self._close()
            return
        except socket.timeout:
            _LOG.error('Timeout while attempting to write to Lutron QSE.')
            return
        _LOG.debug('Wrote: ' + str(data))

    def _read_until_prompt(self, initial_data=None):
        """Read until we encounter a prompt and no additional data available.

        Blocks until data is available (or timeout expires).
        Caller must hold _telnet_lock.
        """
        data = bytearray(initial_data) if initial_data else bytearray()
        try:
            # Read until we encounter a prompt and no additional data.
            while not data.endswith(_PROMPT):
                data.extend(self._telnet.read_until(_PROMPT, timeout=_TIMEOUT))
                data.extend(self._telnet.read_eager())
                if not data:
                    break

        except (socket.error, EOFError):
            _LOG.error('Connection to Lutron QSE lost')
            self._close()
            return []
        except socket.timeout:
            pass

        # Parse the data into responses and comma-delimined chunks.
        result = []
        responses = data.split(_EOL)
        for response in responses:
            response = response.replace(_PROMPT, b'')
            if not response:
                continue
            result.append([bytes(b) for b in response.split(_DELIMITER)])
        _LOG.debug('Read: ' + str(result))
        return result

    def _read_if_available(self):
        """Read data if available.

        Caller must hold _telnet_lock.
        """
        try:
            data = self._telnet.read_eager()
        except (socket.error, EOFError):
            _LOG.error('Connection to Lutron QSE lost')
            self._close()
            return []
        if not data:
            return []
        return self._read_until_prompt(initial_data=data)
