# topology.py
import json
import networkx as nx
from components import Host, Router
from beaconing import BeaconingProcess

class Topology:
    def __init__(self, env, filename):
        self.env = env
        self.graph = nx.Graph()
        self.nodes = {}
        self.hosts = {}
        self.core_ases = set()
        self._load_from_json(filename)

    def _load_from_json(self, filename):
        with open(filename, 'r') as f:
            topo_data = json.load(f)

        for isd_as, as_details in topo_data.items():
            if as_details.get("core", False):
                self.core_ases.add(isd_as)

            # Add routers (border and internal)
            for router_id, router_details in as_details.get("border_routers", {}).items():
                router = Router(self.env, f"{isd_as}-{router_id}")
                self.nodes[router.node_id] = router
                self.graph.add_node(router.node_id, instance=router)

            # Add hosts
            for host_id, host_details in as_details.get("hosts", {}).items():
                full_host_id = f"{isd_as},{host_details['addr']}"
                host = Host(self.env, full_host_id, self)
                self.hosts[full_host_id] = host
                self.nodes[full_host_id] = host
                self.graph.add_node(full_host_id, instance=host)
                # Connect host to a router in the AS (simplified: connect to first border router)
                if as_details.get("border_routers"):
                    first_router_id = list(as_details["border_routers"].keys())[0]
                    self._add_link(host.node_id, f"{isd_as}-{first_router_id}", 1, 1000) # 1ms latency, 1Gbps


        # Add links between routers
        for isd_as, as_details in topo_data.items():
            for router_id, router_details in as_details.get("border_routers", {}).items():
                local_router_id = f"{isd_as}-{router_id}"
                for link in router_details.get("interfaces", []):
                    remote_isd_as = link["isd_as"]
                    remote_router_id_full = f"{remote_isd_as}-{link['neighbor_router']}"
                    
                    # Avoid duplicate link creation
                    if not self.graph.has_edge(local_router_id, remote_router_id_full):
                        self._add_link(local_router_id, remote_router_id_full, link['latency_ms'], link['bandwidth_mbps'])

    def _add_link(self, from_node, to_node, latency, bandwidth):
        self.graph.add_edge(from_node, to_node, latency=latency, bandwidth=bandwidth)
        # Create the physical link component
        link_component = self.nodes[from_node].add_link(to_node, latency, bandwidth, self.nodes[to_node])
        self.nodes[to_node].ports[from_node] = link_component


    def get_host(self, host_id):
        return self.hosts.get(host_id)
        
    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def initiate_beaconing(self, path_selection_algorithm):
        for isd_as in self.core_ases:
            # Start beaconing from the first border router of the core AS
            router_id = list(self.graph.neighbors(next(h for h in self.hosts if h.startswith(isd_as))))[0]
            start_router = self.get_node(router_id)
            beaconing_process = BeaconingProcess(self.env, start_router, path_selection_algorithm)
            self.env.process(beaconing_process.start())