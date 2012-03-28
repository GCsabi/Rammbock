from __future__ import with_statement
from contextlib import contextmanager
from Networking import TCPServer, TCPClient, UDPServer, UDPClient, _NamedCache
from templates import Protocol, UInt, PDU, MessageTemplate, Char, \
    StructTemplate, ListTemplate, UnionTemplate, BinaryContainerTemplate
from binary_tools import to_0xhex, to_bin


class Rammbock(object):

    ROBOT_LIBRARY_SCOPE = 'GLOBAL'

    def __init__(self):
        self._init_caches()

    def _init_caches(self):
        self._protocol_in_progress = None
        self._protocols = {}
        self._servers = _NamedCache('server')
        self._clients = _NamedCache('client')
        self._message_stack = []
        self._field_values = None

    @property
    def _current_container(self):
        return self._message_stack[-1]

    def reset_rammbock(self):
        """Closes all connections, deletes all servers, clients, and protocols.

        You should call this method before exiting your test run. This will close all the connections and the ports
        will therefore be available for reuse faster.
        """
        for server in self._servers:
            server.close()
        for client in self._clients:
            client.close()
        self._init_caches()

    def reset_message_streams(self):
        """ Resets streams of incoming messages.
        
        You can use this method to reuse the same connections for several consecutive test cases.
        """
        for client in self._clients:
            client.empty()
        for server in self._servers:
            server.empty()

    def start_protocol_description(self, protocol_name):
        """Start defining a new protocol template.

        All messages sent and received from a connection that uses a protocol have to conform to this protocol template.
        Protocol template fields can be used to search messages from buffer.
        """
        if self._protocol_in_progress:
            raise Exception('Can not start a new protocol definition in middle of old.')
        if protocol_name in self._protocols:
            raise Exception('Protocol %s already defined' % protocol_name)
        self._protocol_in_progress = Protocol(protocol_name)

    def end_protocol_description(self):
        """End protocol definition."""
        self._protocols[self._protocol_in_progress.name] = self._protocol_in_progress
        self._protocol_in_progress = None

    def start_udp_server(self, ip, port, name=None, timeout=None, protocol=None):
        self._start_server(UDPServer, ip, port, name, timeout, protocol)

    def start_tcp_server(self, ip, port, name=None, timeout=None, protocol=None):
        self._start_server(TCPServer, ip, port, name, timeout, protocol)

    def _start_server(self, server_class, ip, port, name=None, timeout=None, protocol=None):
        protocol = self._get_protocol(protocol)
        server = server_class(ip=ip, port=port, timeout=timeout, protocol=protocol)
        return self._servers.add(server, name)

    def start_udp_client(self, ip=None, port=None, name=None, timeout=None, protocol=None):
        self._start_client(UDPClient, ip, port, name, timeout, protocol)

    def start_tcp_client(self, ip=None, port=None, name=None, timeout=None, protocol=None):
        self._start_client(TCPClient, ip, port, name, timeout, protocol)

    def _start_client(self, client_class, ip=None, port=None, name=None, timeout=None, protocol=None):
        protocol = self._get_protocol(protocol)
        client = client_class(timeout=timeout, protocol=protocol)
        if ip or port:
            client.set_own_ip_and_port(ip=ip, port=port)
        return self._clients.add(client, name)

    def _get_protocol(self, protocol):
        protocol = self._protocols[protocol] if protocol else None
        return protocol

    def get_client_protocol(self, name):
        return self._clients.get(name).protocol

    def accept_connection(self, name=None, alias=None):
        server = self._servers.get(name)
        server.accept_connection(alias)

    def connect(self, host, port, name=None):
        """Connect a client to certain host and port."""
        client = self._clients.get(name)
        client.connect_to(host, port)

    def client_sends_binary(self, message, name=None):
        """Send raw binary data."""
        client = self._clients.get(name)
        client.send(message)

    # FIXME: support "send to" somehow. A new keyword?
    def server_sends_binary(self, message, name=None, connection=None):
        """Send raw binary data."""
        server = self._servers.get(name)
        server.send(message, alias=connection)

    def client_receives_binary(self, name=None, timeout=None):
        """Receive raw binary data."""
        client = self._clients.get(name)
        return client.receive(timeout=timeout)

    def server_receives_binary(self, name=None, timeout=None, connection=None):
        """Receive raw binary data."""
        return self.server_receives_binary_from(name, timeout, connection)[0]

    def server_receives_binary_from(self, name=None, timeout=None, connection=None):
        """Receive raw binary data. Returns message, ip, port"""
        server = self._servers.get(name)
        return server.receive_from(timeout=timeout, alias=connection)

    def new_message(self, message_name, protocol=None, *parameters):
        """Define a new message template.

        Parameters have to be header fields."""
        if self._protocol_in_progress:
            raise Exception("Protocol definition in progress. Please finish it before starting to define a message.")
        proto = self._get_protocol(protocol)
        _, header_fields, _ = self._parse_parameters(parameters)
        self._message_stack = [MessageTemplate(message_name, proto, header_fields)]
        self._field_values = {}

    def get_message(self, *parameters):
        """Get encoded message.

        * Send Message -keywords are convenience methods, that will call this to get the message object and then send it.
        Parameters have to be pdu fields."""
        _, message_fields, header_fields = self._get_parameters_with_defaults(parameters)
        return self._encode_message(message_fields, header_fields)

    def _encode_message(self, message_fields, header_fields):
        msg = self._get_message_template().encode(message_fields, header_fields)
        print '*DEBUG* %s' % repr(msg)
        return msg

    def _get_message_template(self):
        if len(self._message_stack) != 1:
            raise Exception('Message definition not complete. %s not completed.' % self._current_container.name)
        return self._message_stack[0]

    def client_sends_message(self, *parameters):
        """Send a message.

        Parameters have to be message fields."""
        self._send_message(self.client_sends_binary, parameters)

    # FIXME: support "send to" somehow. A new keyword?
    def server_sends_message(self, *parameters):
        """Send a message.

        Parameters have to be message fields."""
        self._send_message(self.server_sends_binary, parameters)

    def _send_message(self, callback, parameters):
        configs, message_fields, header_fields = self._get_parameters_with_defaults(parameters)
        msg = self._encode_message(message_fields, header_fields)
        callback(msg._raw, **configs)

    def client_receives_message(self, *parameters):
        """Receive a message object.

        Parameters that have been given are validated against message fields."""
        with self._receive(self._clients, *parameters) as (msg, message_fields):
            self._validate_message(msg, message_fields)
            return msg

    def client_receives_without_validation(self, *parameters):
        with self._receive(self._clients, *parameters) as (msg, _):
            return msg

    def server_receives_message(self, *parameters):
        """Receive a message object.

        Parameters that have been given are validated against message fields."""
        with self._receive(self._servers, *parameters) as (msg, message_fields):
            self._validate_message(msg, message_fields)
            return msg

    def server_receives_without_validation(self, *parameters):
        with self._receive(self._servers, *parameters) as (msg, _):
            return msg

    def validate_message(self, msg, *parameters):
        _, message_fields, _ = self._get_parameters_with_defaults(parameters)
        self._validate_message(msg, message_fields)

    def _validate_message(self, msg, message_fields):
        errors = self._get_message_template().validate(msg, message_fields)
        if errors:
            print "Validation failed for %s" % repr(msg)
            print '\n'.join(errors)
            raise AssertionError(errors[0])

    @contextmanager
    def _receive(self, nodes, *parameters):
        configs, message_fields, _ = self._get_parameters_with_defaults(parameters)
        node = nodes.get(configs.pop('name', None))
        msg = node.get_message(self._get_message_template(), **configs)
        yield msg, message_fields
        print "*DEBUG* Received %s" % repr(msg)

    def uint(self, length, name, value=None, align=None):
        self._add_field(UInt(length, name, value, align=align))

    def chars(self, length, name, value=None):
        self._add_field(Char(length, name, value))

    def _add_field(self, field):
        if self._protocol_in_progress:
            self._protocol_in_progress.add(field)
        else:
            self._current_container.add(field)

    def struct(self, type, name, *parameters):
        configs, parameters, _ = self._get_parameters_with_defaults(parameters)
        self._message_stack.append(StructTemplate(type, name, self._current_container, parameters, length=configs.get('length')))

    def end_struct(self):
        struct = self._message_stack.pop()
        self._add_field(struct)

    def new_list(self, size, name):
        self._message_stack.append(ListTemplate(size, name, self._current_container))

    def end_list(self):
        list = self._message_stack.pop()
        self._add_field(list)

    def new_binary_container(self, name):
        self._message_stack.append(BinaryContainerTemplate(name, self._current_container))

    def end_binary_container(self):
        binary_container = self._message_stack.pop()
        binary_container.verify()
        self._add_field(binary_container)

    def union(self, type, name):
        self._message_stack.append(UnionTemplate(type, name, self._current_container))

    def end_union(self):
        union = self._message_stack.pop()
        self._add_field(union)

    def pdu(self, length):
        """Defines the message in protocol template.

        Length must be the name of a previous field in template definition."""
        self._add_field(PDU(length))

    def hex_to_bin(self, hex_value):
        return to_bin(hex_value)

    def bin_to_hex(self, bin_value):
        return to_0xhex(bin_value)

    def _get_parameters_with_defaults(self, parameters):
        config, fields, headers = self._parse_parameters(parameters)
        fields = self._populate_defaults(fields)
        return config, fields, headers

    def _populate_defaults(self, fields):
        ret_val = self._field_values
        ret_val.update(fields)
        self._field_values = {}
        return ret_val 

    def value(self, name, value):
        self._field_values[name] = value

    def _parse_parameters(self, parameters):
        configs, fields = [], []
        for parameter in parameters:
            self._parse_entry(parameter, configs, fields)
        headers, fields = self._get_headers(fields)
        return self._to_dict(configs, fields, headers)

    def _get_headers(self, fields):
        headers = []
        header_indexes = []
        for index, (name, value) in enumerate(fields):
            if name == 'header' and ':' in value:
                headers.append(value.split(':', 1))
                header_indexes.append(index)
        fields = [field for index, field in enumerate(fields) 
                  if index not in header_indexes]
        return headers, fields
    
    def _to_dict(self, *lists):
        return (dict(list) for list in lists)

    def _parse_entry(self, param, configs, fields):
        colon_index = param.find(':')
        equals_index = param.find('=')
        # TODO: Cleanup. There must be a cleaner way.
        # Luckily test_rammbock.py has unit tests covering all paths.
        if colon_index == equals_index == -1:
            raise Exception('Illegal parameter %s' % param)
        elif equals_index == -1:
            fields.append(self._name_and_value(':', param))
        elif colon_index == -1 or colon_index > equals_index:
            configs.append(self._name_and_value('=', param))
        else:
            fields.append(self._name_and_value(':', param))

    def _name_and_value(self, separator, parameter):
        index = parameter.find(separator)
        try:
            key = str(parameter[:index].strip())
        except UnicodeError:
            raise Exception("Only ascii characters are supported in parameters.")
        return (key, parameter[index + 1:].strip())

    def _log_msg(self, loglevel, log_msg):
        print '*%s* %s' % (loglevel, log_msg)

