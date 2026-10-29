import heapq

class EventLoop:
    def __init__(self):
        self.now = 0
        self.sequence = 0
        self.queue = []

    def schedule(self, time, callback, *args):
        if time < self.now:
            raise ValueError("Cannot schedule into the past")
        heapq.heappush(self.queue, (time, self.sequence, callback, args))
        self.sequence += 1

    def run(self, until=None):
        while self.queue and (until is None or self.queue[0][0] <= until):
            self.now, _, callback, args = heapq.heappop(self.queue)
            callback(*args)
