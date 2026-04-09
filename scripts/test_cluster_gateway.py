"""
scripts/test_cluster_gateway.py
-------------------------------
Test Kubernetes cluster connectivity via kubeconfig.
Run after updating K8S_KUBECONFIG_PATHS in .env
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from gateway.cluster_gateway import ClusterGateway
from config.settings import get_settings

settings = get_settings()

print(f"Kubeconfig paths: {settings.k8s_kubeconfig_paths}")
print(f"Use in-cluster: {settings.k8s_use_in_cluster}\n")

if not settings.k8s_kubeconfig_paths and not settings.k8s_use_in_cluster:
    print("❌ No kubeconfig paths configured and not in-cluster mode.")
    print("   Update K8S_KUBECONFIG_PATHS in .env with your rancher kubeconfig path.\n")
    sys.exit(1)

# Initialize gateway
gateway = ClusterGateway()
gateway.load_clusters()

# List loaded clusters
clusters = gateway.list_clusters()
print(f"✓ Loaded {len(clusters)} cluster(s):\n")

if not clusters:
    print("❌ No clusters loaded. Check your kubeconfig paths.\n")
    sys.exit(1)

for cluster_name in clusters:
    is_alive = gateway.test_connection(cluster_name)
    status = "✓ Connected" if is_alive else "✗ No connection"
    print(f"  [{status}] {cluster_name}")

print("\n✓ Gateway ready! Your app can now fetch data from Rancher.\n")
