"""Distinguish transient desktop/IPC delays from permanent client errors."""


class ClientError(Exception):
    pass


class ClientUnavailable(ClientError):
    pass


class ClientTimeout(ClientUnavailable):
    pass
