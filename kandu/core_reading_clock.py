"""A local monotonic clock. Navigation samples never credit skipped prose."""
import time
import uuid


class ReadingClock:
    def __init__(self, now=time.monotonic):
        self.now = now
        self.last = now()
        self.elapsed = 0
        self.sequence = 0
        self.session = uuid.uuid4().hex
        self.running = False

    def tick(self, active):
        current = self.now()
        delta = max(0, current - self.last)
        # A stalled UI or waking from sleep is not reading time.
        if active and self.running and delta <= 15:
            self.elapsed += round(delta * 1000)
        self.last = current
        self.running = active

    def sample(self, position, navigation=False):
        self.sequence += 1
        return dict(sessionId=self.session, sequence=self.sequence,
                    elapsedMs=self.elapsed, navigation=navigation,
                    paragraphIndex=position['paragraphIndex'],
                    paragraphOffset=position['paragraphOffset'])
