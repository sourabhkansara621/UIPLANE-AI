"""
scripts/test_namespaces.py
---------------------------
Simple test to get namespaces from Rancher cluster
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from kubernetes import client, config as k8s_config
    from config.settings import get_settings
    
    settings = get_settings()
    
    if not settings.k8s_kubeconfig_paths:
        print("❌ K8S_KUBECONFIG_PATHS not set in .env")
        sys.exit(1)
    
    kubeconfig_path = settings.k8s_kubeconfig_paths.split(",")[0].strip()
    print(f"Loading kubeconfig: {kubeconfig_path}")
    
    # Load kubeconfig and get contexts
    contexts, active = k8s_config.list_kube_config_contexts(config_file=kubeconfig_path)
    
    if not contexts:
        print("❌ No contexts found in kubeconfig")
        sys.exit(1)
    
    print(f"\n✓ Found {len(contexts)} context(s):\n")
    
    for ctx in contexts:
        cluster_name = ctx["name"]
        print(f"  Testing context: {cluster_name}")
        
        try:
            # Load this specific context
            k8s_config.load_kube_config(config_file=kubeconfig_path, context=cluster_name)
            api = client.CoreV1Api()
            
            # Try to list namespaces
            namespaces = api.list_namespace()
            ns_names = [ns.metadata.name for ns in namespaces.items]
            
            print(f"  ✓ Connected! Found {len(ns_names)} namespace(s):")
            for ns in sorted(ns_names):
                print(f"      - {ns}")
            print()
            
        except Exception as e:
            print(f"  ✗ Failed to connect: {e}\n")
            
except ImportError as e:
    print(f"❌ Missing module: {e}")
    print("\nInstall with: pip install -r requirements.txt")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
