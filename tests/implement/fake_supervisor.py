"""RecordingSupervisor: test double that records journal events in memory."""


class RecordingSupervisor:
    """Records every event passed to record() as a dict with an 'event' key."""

    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append({"event": event, **fields})

    def names(self):
        return [e["event"] for e in self.events]

    def first(self, name):
        return next(e for e in self.events if e["event"] == name)
