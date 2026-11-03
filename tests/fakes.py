"""A scriptable in-memory Backend for state-machine and app tests."""


class FakeBackend:
    def __init__(self, host_up=False, sunshine_up=False):
        self._host_up = host_up
        self._sunshine_up = sunshine_up
        self.calls = []
        self.stream = ("none", None)
        self.pair = ("waiting", "")
        self.stopped = 0
        self.restarted = 0

    def wake(self):
        self.calls.append("wake")

    def host_up(self):
        return self._host_up

    def sunshine_up(self):
        return self._sunshine_up

    def start_stream(self, app):
        self.calls.append(("stream", app))
        self.stream = ("running", None)

    def stream_status(self):
        return self.stream

    def stop_stream(self):
        self.stopped += 1
        self.stream = ("none", None)
        return True

    def pair_start(self):
        self.calls.append("pair")
        return "1234"

    def pair_status(self):
        return self.pair

    def pair_cancel(self):
        self.calls.append("pair_cancel")

    def restart_box(self):
        self.restarted += 1
