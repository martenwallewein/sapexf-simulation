# application.py
from packet import Packet

class Application:
    def __init__(self, env, app_id, source_host, dest_host, path_selector, flow_config, results_dict):
        self.env = env
        self.app_id = app_id
        self.source = source_host
        self.destination = dest_host
        self.path_selector = path_selector
        self.flow_config = flow_config
        self.results = results_dict
        self.packets_sent = 0
        self.source.application = self # Link back for notifications
        
    def run(self):
        yield self.env.timeout(self.flow_config['start_time_ms'])
        print(f"[{self.env.now:.2f}] App {self.app_id}: Starting flow from {self.source.node_id} to {self.destination.node_id}")

        path = self.path_selector.select_path(self.source.isd_as, self.destination.isd_as)
        if not path:
            print(f"[{self.env.now:.2f}] App {self.app_id}: No path found. Stopping.")
            return

        print(f"[{self.env.now:.2f}] App {self.app_id}: Selected path: {' -> '.join(path)}")

        # Start a process to listen for incoming packets
        self.env.process(self.receive_handler())
        
        # Send data
        data_to_send_bytes = self.flow_config['data_size_kb'] * 1024
        packet_size = 1500 # bytes
        num_packets = data_to_send_bytes // packet_size

        for i in range(num_packets):
            # For now, we can stick to the initial path to keep it simple, 
            # but in a full version, we would call select_path() here inside the loop.
            packet = Packet(self.source.node_id, self.destination.node_id, path, size=packet_size)
            packet.creation_time = self.env.now
            self.source.send_packet(packet)
            self.packets_sent += 1
            yield self.env.timeout(1) # Send a packet every 1ms
    
    def receive_handler(self):
        while True:
            packet = yield self.source.in_queue.get()
            latency = self.env.now - packet.creation_time
            self.results["latencies"].append(latency)
            # --- FEEDBACK LOOP  ---
            # To close the control loop by feeding real-time Data Plane measurements
            # back into the Control Plane (Path Selection Algorithm).
            
            # We check if the current algorithm supports dynamic feedback, in order to make sure the simulation doesn't crash if we switch back to another 
            # algorithm (like ShortestPath) that doesn't implement 'update_path_feedback'.
            if hasattr(self.path_selector, 'update_path_feedback'):
        
                # We pass the physical path used as the key and the measured one-way latency as the value.
                # Setting 'is_loss=False' instructs the algorithm to append this latency 
                # to its sliding window history, which will be used in the path's composite score.
                self.path_selector.update_path_feedback(packet.path, latency, is_loss=False)
            print(f"[{self.env.now:.2f}] App {self.app_id}: Received packet/ACK after {latency:.2f}ms")

    def notify_loss(self, packet):
        self.results["packet_loss"] += 1
    # --- FEEDBACK LOOP (FAILURE SIGNAL) ---
        #To alert the Control Plane that a path has failed to deliver data.
        if hasattr(self.path_selector, 'update_path_feedback'):
            
            # We report the specific path that caused the packet drop.
            #Latency is passed as 0 (second argument)
            #Sets 'is_loss=True' to increment the loss counter of the algorithm
            self.path_selector.update_path_feedback(packet.path, 0, is_loss=True)

        print(f"[{self.env.now:.2f}] App {self.app_id}: Packet loss detected for flow to {packet.destination}")