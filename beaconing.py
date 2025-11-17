# beaconing.py
from packet import BeaconPacket

class BeaconingProcess:
    def __init__(self, env, start_router, path_selection_algorithm, interval=1000):
        self.env = env
        self.start_router = start_router
        self.path_selection_algorithm = path_selection_algorithm
        self.interval = interval

    def start(self):
        while True:
            beacon = BeaconPacket(self.start_router.node_id)
            print(f"[{self.env.now:.2f}] Core router {self.start_router.node_id} sending beacon.")
            
            # The router's receive_packet will handle forwarding
            self.start_router.receive_packet(beacon)
            
            # The path store is populated implicitly when beacons reach their destination AS
            # In a real SCION implementation, beacons are processed at each hop.
            # Here we simplify: when a beacon arrives at any router, we check if its AS
            # is a destination and register the path.
            self.env.process(self.path_discovery_listener())
            
            yield self.env.timeout(self.interval)

    def path_discovery_listener(self):
        # A simplified listener to register paths
        # This would be more integrated in a real implementation
        yield self.env.timeout(100) # wait a bit for propagation
        # This is a conceptual simplification. In reality, path registration is more complex.
        # Here, we'll let the path selection algorithm scan the network state.
        # The algorithm itself will discover paths by traversing the graph,
        # guided by the conceptual beaconing process.