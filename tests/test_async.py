from __future__ import absolute_import
import os
import sys

from mock import patch, create_autospec
from tornado.iostream import IOStream

# shunt '..' into sys.path since we are in a 'tests' subdirectory
base_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from nsq._compat import struct_l
from nsq.conn import AsyncConn
from nsq import protocol


# The functions below are meant to be used as specs for mocks of callbacks. Yay dynamic typing
def f(*args, **kwargs):
    pass


def _get_test_conn():
    conn = AsyncConn('test', 4150)
    # now set the stream attribute, which is ordinarily set in conn.connect()
    conn.stream = create_autospec(IOStream)
    return conn


# We'd like to autospec the socket mock here, but due to a bug in mock on
# Python 2.x, we can't currently do that. See:
#
#   https://github.com/testing-cabal/mock/issues/323
#
@patch('nsq.conn.socket')
@patch('nsq.conn.tornado.iostream.IOStream', autospec=True)
def test_connect(mock_iostream, mock_socket):
    conn = _get_test_conn()
    conn.connect()
    conn.stream.set_close_callback.assert_called_once_with(conn._socket_close)
    conn.stream.connect.assert_called_once_with((conn.host, conn.port), conn._connect_callback)
    assert conn.state == 'CONNECTING'

    # now ensure that we will bail if we were already in a connecting or connected state
    conn = _get_test_conn()
    conn.state = 'CONNECTING'
    conn.connect()
    assert not conn.stream.set_close_callback.called
    assert not conn.stream.connect.called

    conn = _get_test_conn()
    conn.state = 'CONNECTED'
    conn.connect()
    assert not conn.stream.set_close_callback.called
    assert not conn.stream.connect.called


def test_connect_callback():
    conn = _get_test_conn()
    on_connect = create_autospec(f)
    conn.on('connect', on_connect)
    # simulate having called `conn.connect` by setting state to `connecting`
    conn.state = 'CONNECTING'
    with patch.object(conn, '_start_read', autospec=True) as mock_start_read:
        conn._connect_callback()
        assert conn.state == 'CONNECTED'
        conn.stream.write.assert_called_once_with(protocol.MAGIC_V2)
        mock_start_read.assert_called_once_with()
        on_connect.assert_called_once_with(conn=conn)


def test_start_read():
    conn = _get_test_conn()
    conn._start_read()
    conn.stream.read_bytes.assert_called_once_with(4, conn._read_size)


def test_read_size():
    conn = _get_test_conn()
    body_size = 6
    body_size_packed = struct_l.pack(body_size)
    conn._read_size(body_size_packed)
    conn.stream.read_bytes.assert_called_once_with(body_size, conn._read_body)

    # now test that we get the right behavior when we get malformed data
    # for this, we'll want to stick on mock on conn.close
    conn.stream.read_bytes.reset_mock()
    with patch.object(conn, 'close', autospec=True) as mock_close:
        conn._read_size('asdfasdf')
        mock_close.assert_called_once_with()
        assert not conn.stream.read_bytes.called


def test_read_body():
    conn = _get_test_conn()
    on_data = create_autospec(f)
    conn.on('data', on_data)

    data = 'NSQ'
    conn._read_body(data)
    on_data.assert_called_once_with(conn=conn, data=data)
    conn.stream.read_bytes.assert_called_once_with(4, conn._read_size)

    # now test functionality when the data_callback fails
    on_data.reset_mock()
    conn.stream.read_bytes.reset_mock()
    on_data.return_value = Exception("Boom.")
    conn._read_body(data)
    # verify that the next _start_read was called
    conn.stream.read_bytes.assert_called_once_with(4, conn._read_size)
