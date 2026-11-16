import socket

from sqlalchemy.exc import InterfaceError, OperationalError

from app.utils.db_errors import is_transient_db_error


def test_name_resolution_is_transient() -> None:
    assert is_transient_db_error(socket.gaierror(-3, 'Temporary failure in name resolution'))


def test_connection_errors_are_transient() -> None:
    assert is_transient_db_error(ConnectionRefusedError())
    assert is_transient_db_error(TimeoutError())
    assert is_transient_db_error(InterfaceError('SELECT 1', {}, Exception('connection is closed')))
    assert is_transient_db_error(OperationalError('SELECT 1', {}, Exception('db unavailable')))


def test_business_errors_are_not_transient() -> None:
    assert not is_transient_db_error(ValueError('bad value'))
