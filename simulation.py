# simulation.py
import simpy
import json
from topology import Topology
from application import Application
from path_selection import ShortestPathAlgorithm

class Simulation:
    def __init__(self, topology_file, traffic_file, algorithm_class=ShortestPathAlgorithm):
        self.env = simpy.Environment()
        self.topology = Topology(self.env, topology_file)
        self.traffic_scenario = self.load_traffic_scenario(traffic_file)
        self.path_selection_algorithm = algorithm_class(self.topology)
        self.results = {"packet_loss": 0, "latencies": []}

    def load_traffic_scenario(self, filename):
        with open(filename, 'r') as f:
            return json.load(f)

    def run(self):
        print("Starting beaconing process...")
        self.topology.initiate_beaconing(self.path_selection_algorithm)
        # Give beaconing some time to propagate
        yield self.env.timeout(2000) 
        
        print("\nAll available paths discovered:")
        for (src, dst), paths in self.path_selection_algorithm.path_store.items():
            print(f"  Paths from {src} to {dst}:")
            for i, path in enumerate(paths):
                 print(f"    {i+1}: {' -> '.join([str(hop) for hop in path])}")


        print("\nStarting applications based on traffic scenario...")
        for flow in self.traffic_scenario['flows']:
            source_host = self.topology.get_host(flow['source'])
            destination_host = self.topology.get_host(flow['destination'])
            
            if source_host and destination_host:
                app = Application(
                    self.env,
                    f"App-{flow['name']}",
                    source_host,
                    destination_host,
                    self.path_selection_algorithm,
                    flow,
                    self.results
                )
                self.env.process(app.run())
            else:
                print(f"Warning: Could not find source or destination host for flow {flow['name']}")

        # Run the simulation for a specified duration
        simulation_duration = self.traffic_scenario.get("duration_ms", 1000)
        print(f"\nRunning simulation for {simulation_duration}ms...")
        self.env.run(until=simulation_duration)
        print("\nSimulation finished.")
        self.print_results()

    def print_results(self):
        print("\n--- Simulation Results ---")
        total_lost = self.results['packet_loss']
        total_received = len(self.results['latencies'])
        total_sent = total_lost + total_received
        
        loss_rate = (total_lost / total_sent * 100) if total_sent > 0 else 0
        avg_latency = sum(self.results['latencies']) / total_received if total_received > 0 else 0

        print(f"Total Packets Sent: {total_sent}")
        print(f"Total Packets Received: {total_received}")
        print(f"Total Packets Lost: {total_lost}")
        print(f"Packet Loss Rate: {loss_rate:.2f}%")
        print(f"Average Packet Latency: {avg_latency:.2f}ms")