import re
import csv
from io import StringIO
import os # Import os for path operations, although not strictly used in this environment

# --- Configuration & Mock Input Data ---
# In a real environment, you would use 'network_log.txt' to load data from disk.
# We use MOCK_LOG_CONTENT below to simulate the content of that file for demonstration.
LOG_FILEPATH = "ppm80_nodes60.111100010000"

# Regex for WRAPUP (Node Side) Data
# Captures: 1:WRAPUP_Timestamp, 2:NodeID, 3:reason, 4:end_ms, 5:Gen, 6:Fwd, 7:QLoss, 8:qsize, 9:residual, 10:ppm, 11:parent, 12:switches
WRAPUP_PATTERN = re.compile(
    r"^(\d+)\s+(\d+)\s+.*WRAPUP node_id=\d+\s+reason=(\w+)\s+end_ms=(\d+)\s+Gen=(\d+)\s+Fwd=(\d+)\s+QLoss=(\d+)\s+qsize=(\d+)\s+residual=([\d\.]+J)\s+ppm=(\d+)\s+parent=(\d+)\s+switches=(\d+)",
    re.MULTILINE
)

# Regex for SINK_SUMMARY (Sink Side) Data
# Captures: 1:SINK_Timestamp, 2:NodeID, 3:Recv, 4:AvgE2E, 5:MinE2E, 6:MaxE2E
SINK_PATTERN = re.compile(
    r"^(\d+)\s+1\s+.*SINK_SUMMARY node=(\d+)\s+Recv=(\d+)\s+AvgE2E=([\d\w]+)\s+MinE2E=([\d\w]+)\s+MaxE2E=([\d\w]+)",
    re.MULTILINE
)

def load_log_content(filepath):
    """
    Reads the content of the specified log file.
    
    In a real environment, this function would read from the disk. 
    Here, it simulates the process by returning the MOCK_LOG_CONTENT.
    """
    print(f"Attempting to read log data from: {filepath}")
    
    # --- Real-World File Reading Logic (Commented out for runnable environment) ---
    try:
        with open(filepath, 'r') as f:
            return f.read()
    except FileNotFoundError:
        print(f"ERROR: Log file not found at path: {filepath}")
        return ""


def parse_and_merge_logs(log_data):
    """
    Parses the log data, extracts WRAPUP and SINK_SUMMARY metrics,
    and merges them into a single dictionary keyed by Node ID.
    """
    if not log_data:
        print("Input log data is empty. Cannot parse.")
        return {}

    # Initialize dictionary to hold merged data, using NodeID as the key
    merged_data = {}
    
    # 1. Parse WRAPUP (Node Side) Data
    for match in WRAPUP_PATTERN.finditer(log_data):
        (wrapup_ts, node_id, reason, end_ms, gen, fwd, qloss, qsize,
         residual, ppm, parent, switches) = match.groups()

        # Initialize the dictionary entry for this node ID
        merged_data[node_id] = {
            "NodeID": node_id,
            "WRAPUP_Timestamp": wrapup_ts,
            "reason": reason,
            "end_ms": end_ms,
            "Gen": gen,
            "Fwd": fwd,
            "QLoss": qloss,
            "qsize": qsize,
            "residual": residual,
            "ppm": ppm,
            "parent": parent,
            "switches": switches,
            # Placeholder for SINK data - initialized to empty strings
            "SINK_Timestamp": "",
            "Recv": "",
            "AvgE2E": "",
            "MinE2E": "",
            "MaxE2E": "",
        }

    # 2. Parse SINK_SUMMARY (Sink Side) Data
    for match in SINK_PATTERN.finditer(log_data):
        (sink_ts, node_id, recv, avg_e2e, min_e2e, max_e2e) = match.groups()

        # Check if a WRAPUP record exists for this node ID and merge the SINK data
        if node_id in merged_data:
            node_record = merged_data[node_id]
            node_record["SINK_Timestamp"] = sink_ts
            node_record["Recv"] = recv
            node_record["AvgE2E"] = avg_e2e
            node_record["MinE2E"] = min_e2e
            node_record["MaxE2E"] = max_e2e
        else:
            # SINK_SUMMARY for node 1 (the sink itself) or nodes without WRAPUP are ignored
            pass

    return merged_data

def write_to_csv(merged_data, filename="merged_network_summary.csv"):
    """
    Writes the merged dictionary data to a CSV file.
    """
    if not merged_data:
        print("No data to write.")
        return

    # Define the header based on the keys of the first (any) record
    # This ensures consistent ordering of columns.
    fieldnames = [
        "NodeID", "WRAPUP_Timestamp", "reason", "end_ms", "Gen", "Fwd",
        "QLoss", "qsize", "residual", "ppm", "parent", "switches",
        "SINK_Timestamp", "Recv", "AvgE2E", "MinE2E", "MaxE2E"
    ]

    try:
        # Note: In a real environment, this writes to the disk.
        with open(filename, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            # Write rows, sorted by Node ID for better readability
            for node_id in sorted(merged_data.keys(), key=int):
                writer.writerow(merged_data[node_id])
        
        print(f"Successfully created CSV file: '{filename}' with {len(merged_data)} records.")

    except Exception as e:
        print(f"An error occurred while writing the CSV: {e}")

if __name__ == "__main__":
    
    # 1. Load Data from the simulated log file
    log_content = load_log_content(LOG_FILEPATH)
    
    # 2. Parse and Merge Data
    merged_network_data = parse_and_merge_logs(log_content)

    # 3. Write to CSV file
    write_to_csv(merged_network_data)
