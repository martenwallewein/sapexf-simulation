# components.py
import simpy
from collections import deque

class Link:
    def __init__(self, env, latency, bandwidth_mbps, destination_node, loss_notifier):
        self.env = env
        self.latency = latency
        # Bandwidth in bytes per millisecond
        self.bandwidth = (bandwidth_mbps * 1_000_000) / 8 / 1000
        self.queue = simpy.Store(env)
        self.destination = destination_node
        self.loss_notifier = loss_notifier
        self.env.process(self.run())

    def run(self):
        while True:
            packet = yield self.queue.get()
            # Propagation delay
            yield self.env.timeout(self.latency)
            # Transmission delay
            transmission_delay = (packet.size * 8) / (self.bandwidth * 1000)
            yield self.env.timeout(transmission_delay)
            self.destination.receive_packet(packet)
    
    def enqueue(self, packet):
        self.queue.put(packet)


class Node:
    def __init__(self, env, node_id):
        self.env = env
        self.node_id = node_id
        self.ports = {}

    def add_link(self, to_node_id, latency, bandwidth, destination_node):
        link = Link(self.env, latency, bandwidth, destination_node, self.notify_loss)
        self.ports[to_node_id] = link
        return link

    def notify_loss(self, packet):
        # This can be extended to notify the sending application
        pass

    def receive_packet(self, packet):
        raise NotImplementedError

class Router(Node):
    def __init__(self, env, node_id):
        super().__init__(env, node_id)
        self.forwarding_table = {} # simple next-hop forwarding

    def receive_packet(self, packet):
        # Beacon packets are handled by the beaconing process logic
        if packet.is_beacon:
            # Propagate beacon
            packet.path.append(self.node_id)
            # Simple flooding for beaconing
            for neighbor_id, link in self.ports.items():
                # Avoid loops
                if neighbor_id not in packet.path:
                    link.enqueue(packet.clone())
        else: # Data packet
            if packet.destination == self.node_id:
                # Packet for a host in this AS, find the host link
                # This part needs a more complex routing logic in a full implementation
                pass
            else:
                # Forward along the path
                try:
                    current_hop_index = packet.path.index(self.node_id)
                    next_hop = packet.path[current_hop_index + 1]
                    if next_hop in self.ports:
                        self.ports[next_hop].enqueue(packet)
                    else:
                        print(f"[{self.env.now:.2f}] Router {self.node_id}: Dead end for packet to {packet.destination}. Dropping.")
                except (ValueError, IndexError):
                    print(f"[{self.env.now:.2f}] Router {self.node_id}: Invalid path on packet. Dropping.")

class Host(Node):
    def __init__(self, env, node_id, topology):
        super().__init__(env, node_id)
        self.isd_as = node_id.split(',')[0]
        self.topology = topology
        self.in_queue = simpy.Store(env)
        self.application = None # To be set by the application instance

    def send_packet(self, packet):
        # Send to the connected router
        router_id = list(self.topology.graph.neighbors(self.node_id))[0]
        if router_id in self.ports:
            self.ports[router_id].enqueue(packet)
        else:
            print(f"Error: Host {self.node_id} has no link to router {router_id}")

    def receive_packet(self, packet):
        self.in_queue.put(packet)