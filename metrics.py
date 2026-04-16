# metrics.py
"""

Tracks:
    - Path Switching Frequency (oscillation count)
    - Transfer Time (time to deliver all packets per flow)
    - Per-flow latency statistics (min, max, avg, median, p95, p99)
    - Per-flow packet loss rate
    - Per-flow throughput
    - Per-path utilization
"""

import time as wall_time
from collections import defaultdict
from collections.abc import Iterable


class MetricsCollector:
    """
    Collects and aggregates simulation metrics across all flows and paths.
    Attach to a Simulation instance to automatically gather data.
    """

    def __init__(self):
        # --- Per-flow tracking ---
        # flow_name -> list of latencies (ms)
        self.flow_latencies = defaultdict(list)
        # flow_name -> count of lost packets
        self.flow_packet_loss = defaultdict(int)
        # flow_name -> count of sent packets
        self.flow_packets_sent = defaultdict(int)
        # flow_name -> count of received packets
        self.flow_packets_received = defaultdict(int)
        # flow_name -> bytes sent
        self.flow_bytes_sent = defaultdict(int)
        # flow_name -> first packet send time (sim ms)
        self.flow_start_time = {}
        # flow_name -> last packet receive time (sim ms)
        self.flow_end_time = {}
        # flow_name -> list of (sim_time, path_tuple) to track switches
        self.flow_path_history = defaultdict(list)

        # --- Per-path tracking ---
        # path_tuple -> total packets forwarded
        self.path_packet_count = defaultdict(int)
        # path_tuple -> total bytes forwarded
        self.path_bytes = defaultdict(int)
        # (from_node, to_node) -> {flow_name -> bytes_sent}
        self.link_flow_bytes = defaultdict(lambda: defaultdict(int))

        # --- Global tracking ---
        self.all_latencies = []
        self.total_packets_sent = 0
        self.total_packets_received = 0
        self.total_packets_lost = 0

        # Wall-clock timing
        self._wall_start = None
        self._wall_end = None

    def start_collection(self):
        """Mark the start of metrics collection (wall-clock)."""
        self._wall_start = wall_time.time()

    def stop_collection(self):
        """Mark the end of metrics collection (wall-clock)."""
        self._wall_end = wall_time.time()

    # ---- Recording methods (called during simulation) ----

    def record_packet_sent(self, flow_name, sim_time, path, packet_size=1500):
        """Record a packet being sent."""
        if isinstance(sim_time, str) and not self._is_path_like(path):
            # Legacy call signature: (flow_name, app_name, sim_time)
            sim_time, path = path, []

        if not isinstance(sim_time, (int, float)):
            sim_time = 0

        if not self._is_path_like(path):
            path = []

        self.flow_packets_sent[flow_name] += 1
        self.flow_bytes_sent[flow_name] += packet_size
        self.total_packets_sent += 1

        path_tuple = tuple(path)
        self.path_packet_count[path_tuple] += 1
        self.path_bytes[path_tuple] += packet_size

        for from_node, to_node in zip(path_tuple[:-1], path_tuple[1:]):
            self.link_flow_bytes[(from_node, to_node)][flow_name] += packet_size

        if flow_name not in self.flow_start_time:
            self.flow_start_time[flow_name] = sim_time

    def record_packet_received(self, flow_name, sim_time, latency, packet_size=None):
        """Record a packet being received."""
        if isinstance(sim_time, str) and packet_size is not None:
            # Legacy call signature: (flow_name, app_name, sim_time, latency)
            sim_time, latency = latency, packet_size

        if not isinstance(sim_time, (int, float)):
            sim_time = 0
        if not isinstance(latency, (int, float)):
            latency = 0

        self.flow_latencies[flow_name].append(latency)
        self.flow_packets_received[flow_name] += 1
        self.flow_end_time[flow_name] = sim_time
        self.total_packets_received += 1
        self.all_latencies.append(latency)

    def record_packet_loss(self, flow_name):
        """Record a packet loss."""
        self.flow_packet_loss[flow_name] += 1
        self.total_packets_lost += 1

    def record_path_switch(self, flow_name, sim_time, new_path):
        """Record a path switch event."""
        path_tuple = tuple(new_path)
        history = self.flow_path_history[flow_name]

        # Only record if the path actually changed
        if not history or history[-1][1] != path_tuple:
            history.append((sim_time, path_tuple))

    # ---- Aggregation methods (called after simulation) ----

    def get_flow_stats(self, flow_name):
        """Get comprehensive statistics for a single flow."""
        latencies = self.flow_latencies.get(flow_name, [])
        sent = self.flow_packets_sent.get(flow_name, 0)
        received = self.flow_packets_received.get(flow_name, 0)
        lost = self.flow_packet_loss.get(flow_name, 0)
        bytes_sent = self.flow_bytes_sent.get(flow_name, 0)
        start = self.flow_start_time.get(flow_name, 0)
        end = self.flow_end_time.get(flow_name, 0)

        transfer_time = end - start if end > start else 0
        loss_rate = (lost / sent * 100) if sent > 0 else 0
        throughput_mbps = (bytes_sent * 8 / (transfer_time * 1000)) if transfer_time > 0 else 0
        unaccounted = max(0, sent - received - lost)

        stats = {
            "packets_sent": sent,
            "packets_received": received,
            "packets_lost": lost,
            "packets_unaccounted": unaccounted,
            "loss_rate_percent": round(loss_rate, 4),
            "bytes_sent": bytes_sent,
            "transfer_time_ms": round(transfer_time, 2),
            "throughput_mbps": round(throughput_mbps, 4),
            "path_switches": self.get_path_switch_count(flow_name),
        }

        if latencies:
            sorted_lat = sorted(latencies)
            n = len(sorted_lat)
            stats.update({
                "latency_min_ms": round(min(latencies), 4),
                "latency_max_ms": round(max(latencies), 4),
                "latency_avg_ms": round(sum(latencies) / n, 4),
                "latency_median_ms": round(sorted_lat[n // 2], 4),
                "latency_p95_ms": round(sorted_lat[int(n * 0.95)], 4),
                "latency_p99_ms": round(sorted_lat[int(n * 0.99)], 4),
                "latency_stddev_ms": round(self._stddev(latencies), 4),
            })
        else:
            stats.update({
                "latency_min_ms": 0,
                "latency_max_ms": 0,
                "latency_avg_ms": 0,
                "latency_median_ms": 0,
                "latency_p95_ms": 0,
                "latency_p99_ms": 0,
                "latency_stddev_ms": 0,
            })

        return stats

    def get_path_switch_count(self, flow_name):
        """Get the number of path switches for a flow (oscillation metric)."""
        history = self.flow_path_history.get(flow_name, [])
        # First entry is initial path selection, so switches = len - 1
        return max(0, len(history) - 1)

    def get_total_path_switches(self):
        """Get total path switches across all flows."""
        total = 0
        for flow_name in self.flow_path_history:
            total += self.get_path_switch_count(flow_name)
        return total

    def get_global_stats(self):
        """Get global simulation statistics."""
        total_sent = self.total_packets_sent
        total_received = self.total_packets_received
        total_lost = self.total_packets_lost
        total_unaccounted = max(0, total_sent - total_received - total_lost)
        loss_rate = (total_lost / total_sent * 100) if total_sent > 0 else 0

        stats = {
            "total_packets_sent": total_sent,
            "total_packets_received": total_received,
            "total_packets_lost": total_lost,
            "total_packets_unaccounted": total_unaccounted,
            "packet_loss_rate_percent": round(loss_rate, 4),
            "total_path_switches": self.get_total_path_switches(),
        }

        if self.all_latencies:
            sorted_lat = sorted(self.all_latencies)
            n = len(sorted_lat)
            stats.update({
                "latency_min_ms": round(min(self.all_latencies), 4),
                "latency_max_ms": round(max(self.all_latencies), 4),
                "latency_avg_ms": round(sum(self.all_latencies) / n, 4),
                "latency_median_ms": round(sorted_lat[n // 2], 4),
                "latency_p95_ms": round(sorted_lat[int(n * 0.95)], 4),
                "latency_p99_ms": round(sorted_lat[int(n * 0.99)], 4),
            })
        else:
            stats.update({
                "latency_min_ms": 0,
                "latency_max_ms": 0,
                "latency_avg_ms": 0,
                "latency_median_ms": 0,
                "latency_p95_ms": 0,
                "latency_p99_ms": 0,
            })

        if self._wall_start and self._wall_end:
            stats["wall_clock_seconds"] = round(self._wall_end - self._wall_start, 3)

        return stats

    def get_fairness_stats(self):
        """Compute Jain's Fairness Index globally and per link.

        Global JFI is computed on per-flow *effective throughput*
        (bytes_sent / transfer_time_ms) rather than raw bytes_sent.
        Using raw bytes_sent is not meaningful because the volume each flow
        sends is fixed by the scenario file and is identical across all
        algorithms, so JFI would always be ~1.0 regardless of how well the
        algorithm distributes load.  Throughput captures the actual service
        quality a flow received: flows assigned to congested or high-latency
        paths have longer transfer times and therefore lower throughput, which
        is correctly reflected as unfairness in the index.

        Per-link JFI is still byte-based (bytes each flow sends over a shared
        link), which measures how evenly flows are spread across a link.

        Only flows that both sent bytes and completed (have a recorded end
        time) are included in the global JFI.
        """
        throughput_values = []
        for flow_name, bytes_sent in self.flow_bytes_sent.items():
            if bytes_sent <= 0:
                continue
            start = self.flow_start_time.get(flow_name)
            end = self.flow_end_time.get(flow_name)
            if start is None or end is None:
                continue
            transfer_time = end - start
            if transfer_time > 0:
                throughput_values.append(bytes_sent / transfer_time)

        global_jfi = self._jains_fairness_index(throughput_values)

        per_link = {}
        for link_key, flow_dict in self.link_flow_bytes.items():
            active_flows = {fn: b for fn, b in flow_dict.items() if b > 0}
            if not active_flows:
                continue
            link_str = f"{link_key[0]} -> {link_key[1]}"
            per_link[link_str] = {
                "num_flows": len(active_flows),
                "jains_fairness_index": round(self._jains_fairness_index(list(active_flows.values())), 6),
                "flow_bytes": active_flows,
            }

        return {"global_jfi": round(global_jfi, 6), "per_link": per_link}
    
    def get_average_per_link_jfi(self):
        """
        Get the average Jain's Fairness Index across all links.
        Useful as a single metric for comparison in aggregated results.
        """
        per_link = self.get_fairness_stats().get("per_link", {})
        if not per_link:
            return 0.0
        
        jfi_values = [
            link_stats.get("jains_fairness_index")
            for link_stats in per_link.values()
            if isinstance(link_stats, dict) and link_stats.get("jains_fairness_index") is not None
        ]
        
        if not jfi_values:
            return 0.0
        
        return round(sum(jfi_values) / len(jfi_values), 6)
    
    def get_per_path_stats(self):
        """Get utilization stats per path."""
        path_stats = {}
        for path_tuple, count in self.path_packet_count.items():
            path_key = " -> ".join(path_tuple)
            path_stats[path_key] = {
                "packets_forwarded": count,
                "bytes_forwarded": self.path_bytes.get(path_tuple, 0),
            }
        return path_stats

    def get_full_report(self):
        """Get the complete metrics report as a dictionary."""
        report = {
            "global": self.get_global_stats(),
            "per_flow": {},
            "per_path": self.get_per_path_stats(),
            "fairness": self.get_fairness_stats(),
        }

        for flow_name in self.flow_packets_sent:
            report["per_flow"][flow_name] = self.get_flow_stats(flow_name)

        return report

    # ---- Helpers ----

    @staticmethod
    def _jains_fairness_index(values):
        """JFI = (sum(x_i))^2 / (n * sum(x_i^2)). Returns 0.0 for empty, 1.0 for single value."""
        n = len(values)
        if n == 0:
            return 0.0
        if n == 1:
            return 1.0
        sum_x = sum(values)
        sum_x2 = sum(x * x for x in values)
        if sum_x2 == 0:
            return 1.0
        return (sum_x ** 2) / (n * sum_x2)

    @staticmethod
    def _stddev(values):
        """Compute standard deviation."""
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return variance ** 0.5

    @staticmethod
    def _is_path_like(value):
        return isinstance(value, Iterable) and not isinstance(value, (str, bytes))
