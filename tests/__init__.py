"""Tests for pylutron_qse."""

import telnetlib
import unittest
from unittest import mock

from pylutron_qse.qse import QSE

HOSTNAME = 'QSE'

DETAILS_RESPONSE = (
    b'~DETAILS,SN:0x00294bad,INTEGRATIONID:(Not Set),FAMILY:SHADES(3),'
    b'PRODUCT:ROLLER(1),CODE:0.106,BOOT:2.10,HW:8.6\r\n'
    b'~DETAILS,SN:0x00294c2f,INTEGRATIONID:Master Bedroom,FAMILY:SHADES(3),'
    b'PRODUCT:ROLLER(1),CODE:0.106,BOOT:2.10,HW:8.6\r\n'
    b'~DETAILS,SN:0x002a280e,INTEGRATIONID:(Not Set),FAMILY:KEYPAD(1),'
    b'PRODUCT:QSWS2(1),CODE:2.50,BOOT:2.3,HW:1.1\r\n'
    b'~DETAILS,SN:0x00311eab,INTEGRATIONID:(Not Set),'
    b'FAMILY:CONTROL_INTERFACE(6),PRODUCT:QSE(1),CODE:8.21,BOOT:2.9,HW:1.0\r\n'
    b'QSE>')

DEVICE_RESPONSE_1 = b'QSE>'
DEVICE_RESPONSE_2 = (
    b'~DEVICE,0x00294bad,0,14,99.63\r\nQSE>~DEVICE,Master Bedroom,0,14,0.23'
    b'\r\nQSE>QSE>')


class TestQSE(unittest.TestCase):
    """Tests for pylutron_qse."""

    @mock.patch.object(telnetlib, 'Telnet')
    @mock.patch.object(QSE, '_monitor')
    @mock.patch.object(QSE, '_load_devices')
    def test_login(
            self, mock_load_devices, mock_monitor, mock_telnet_constructor):
        telnet_mock = mock_telnet_constructor.return_value = mock.MagicMock()
        telnet_mock.read_until.side_effect = [
            b'login: ',
            b'connection established\r\n',
        ]
        QSE(HOSTNAME)
        assert telnet_mock.called_with(HOSTNAME, mock.ANY)

    @mock.patch.object(QSE, '_login')
    @mock.patch.object(QSE, '_monitor')
    def test_load_devices(self, mock_monitor, mock_login):
        telnet_mock = mock.MagicMock()
        mock_login.return_value = telnet_mock
        telnet_mock.read_until.side_effect = [
            DETAILS_RESPONSE,
            DEVICE_RESPONSE_1,
        ]
        telnet_mock.read_eager.side_effect = [
            b'',
            b'',
            b'',
            DEVICE_RESPONSE_2,
            b'',
        ]
        qse = QSE(HOSTNAME)
        assert telnet_mock.called_with(HOSTNAME, mock.ANY)
        assert len(qse.devices()) == 4
        rollers = qse.rollers()
        assert len(rollers) == 2
        roller_1 = [r for r in rollers if r.serial_number == '0x00294bad']
        roller_2 = [r for r in rollers if r.serial_number == '0x00294c2f']
        assert roller_1
        assert roller_2
        roller_1 = roller_1[0]
        roller_2 = roller_2[0]
        assert roller_1.integration_id is None
        assert roller_1.target_level == 100
        assert roller_2.integration_id == 'Master Bedroom'
        assert roller_2.target_level == 0


if __name__ == '__main__':
    unittest.main()
