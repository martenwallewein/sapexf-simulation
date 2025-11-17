# path_selection.py
from abc import ABC, abstractmethod
import networkx as nx

class PathSelectionAlgorithm(ABC):
    def __init__(self, topology):
        self.topology = topology
        self.path_store = {} # (src_as, dst_as) -> [path1, path2, ...]

    @abstractmethod
    def select_path(self, source_as, destination_as):
        """Selects and returns the best path."""
        pass

    def discover_paths(self, use_graph_traversal=False):
        """
        Optional discovery process that finds all simple paths using graph traversal.
        This is a fallback mechanism - normally paths should be populated by beaconing.

        Args:
            use_graph_traversal: If True, use NetworkX to find all paths (bypasses beaconing)
        """
        if not use_graph_traversal:
            print("Path discovery via beaconing is enabled. Paths will be discovered through beacon propagation.")
            return

        # Fallback: graph-based path discovery (for testing/debugging)
        print("WARNING: Using graph traversal for path discovery. This bypasses SCION beaconing.")
        all_ases = {node.split('-')[0] for node in self.topology.graph.nodes() if '-' in node}
        for src_as in all_ases:
            for dst_as in all_ases:
                if src_as != dst_as:
                    # Find routers in source and destination ASes
                    src_routers = [r for r in self.topology.graph.nodes() if r.startswith(src_as + '-')]
                    dst_routers = [r for r in self.topology.graph.nodes() if r.startswith(dst_as + '-')]
                    if not src_routers or not dst_routers:
                        continue

                    # Find all paths between the first router of each AS
                    # In a real scenario, you'd do this for all border routers
                    paths = list(nx.all_simple_paths(self.topology.graph, source=src_routers[0], target=dst_routers[0]))
                    self.path_store[(src_as, dst_as)] = paths


# --- Example Implementation ---
class ShortestPathAlgorithm(PathSelectionAlgorithm):
    def __init__(self, topology, use_beaconing=True):
        super().__init__(topology)
        self.use_beaconing = use_beaconing
        # Only use graph traversal if beaconing is disabled
        self.discover_paths(use_graph_traversal=not use_beaconing)

    def select_path(self, source_as, destination_as):
        available_paths = self.path_store.get((source_as, destination_as), [])
        if not available_paths:
            return None
        
        # Select the path with the minimum number of hops
        return min(available_paths, key=len)