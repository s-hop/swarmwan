import asyncio

class SimpleQueue:
    def __init__(self):
        self.queue = []
        self.event = asyncio.Event()
        
    async def put(self, item):
        self.queue.append(item)
        print(f'command added to q: {item}')
        self.event.set()  # Notify that an item is available

    async def get(self):
        while not self.queue:
            await self.event.wait()  # Wait for an item
            self.event.clear()  # Reset event after waking up
        return self.queue.pop(0)  # Return oldest item (FIFO)