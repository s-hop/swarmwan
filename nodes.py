from micropython import const

_JOIN = const('<< Node Joined: ')
_REJOIN = const('<< Node Rejoined: ')
_TIMEOUT = const('<< Node Timed Out: ')
_TAG = const('MESH')

class Nodes:
    def __init__(self, logger):
        self.logger = logger
        self.count = 0
        self.all = {}
        self.active = {}
        
    def add(self, nick, ticks_ms, rssi):
        new = {
            'last_seen_ms':ticks_ms,
            'last_rssi':rssi,
            'timedout':False
            }
        self.all[nick] = new
        self.active[nick] = new
        self.count += 1
        self.logger.log_sys(tag=_TAG, msg=f'{_JOIN}{nick} n:{self.count}')
        
    def update(self, nick, ticks_ms, rssi):
        n = self.all[nick]
        if n['timedout']:
            self.count += 1
            n['timedout'] = False
            self.active[nick] = n
            self.logger.log_sys(tag=_TAG, msg=f'{_REJOIN}{nick} n:{self.count}')
        n['last_seen_ms'] = ticks_ms
        n['last_rssi'] = rssi
        
    def timeout(self, nick):
        node = self.active[nick]
        self.all[nick]['timedout'] = True
        self.active.pop(nick)
        self.count -= 1
        self.logger.log_sys(tag=_TAG, msg=f'{_TIMEOUT}{nick} n:{self.count}')
    
    def seen(self, nick):
        return True if nick in self.all else False
