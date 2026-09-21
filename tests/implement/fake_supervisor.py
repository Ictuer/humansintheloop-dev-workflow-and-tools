"""RecordingSupervisor: test double that records journal events in memory."""


class RecordingSupervisor:
    """Records every event as a dict with an 'event' key.

    ``block()`` records a blocked event and answers with the next scripted response:
    a ResumeRequest is returned, an exception instance (e.g. RunStopped()) is raised.
    """

    def __init__(self, block_responses=()):
        self.events = []
        self._block_responses = list(block_responses)

    def record(self, event, **fields):
        self.events.append({"event": event, **fields})

    def block(self, kind, reason, **details):
        self.record("blocked", kind=kind, reason=reason, **details)
        response = self._block_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def names(self):
        return [e["event"] for e in self.events]

    def first(self, name):
        return next(e for e in self.events if e["event"] == name)

    def all(self, name):
        return [e for e in self.events if e["event"] == name]
