# packet.py
import copy

class Packet:
    def __init__(self, source, destination, path, payload="", size=1500, is_beacon=False):
        self.source = source
        self.destination = destination
        self.path = path
        self.payload = payload
        self.size = size # in bytes
        self.is_beacon = is_beacon
        self.creation_time = 0

    def clone(self):
        return copy.deepcopy(self)

class BeaconPacket(Packet):
    def __init__(self, origin_router_id):
        super().__init__(source=origin_router_id, destination=None, path=[origin_router_id], is_beacon=True, size=100)