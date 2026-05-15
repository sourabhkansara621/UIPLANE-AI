#!/usr/bin/env python3
"""Test script to verify namespace selection now auto-fetches resources."""

import requests
import json
import time

base_url = 'http://127.0.0.1:8091'

# Login
print("Logging in...")
auth_resp = requests.post(
    f'{base_url}/api/auth/login',
    json={'username': 'priya', 'password': 'demo1234'}
)
token = auth_resp.json()['access_token']
headers = {'Authorization': f'Bearer {token}'}

# Test 1: List namespaces
print("\n=== TEST 1: List namespaces ===")
resp = requests.post(
    f'{base_url}/api/chat/query',
    headers=headers,
    json={'query': 'list namespaces for EKS RMZ NonProd', 'chat_mode': 'k8-info'}
)
data = resp.json()
session_id = data['session_id']
print(f'✓ Session ID: {session_id}')

# Test 2: Select namespace with explicit "show pods in" query (simulating auto-fetch)
print("\n=== TEST 2: Simulate auto-fetch after namespace selection ===")
print("Step 1: Select namespace")
resp = requests.post(
    f'{base_url}/api/chat/query',
    headers=headers,
    json={'query': 'namespace default for EKS RMZ NonProd', 'session_id': session_id, 'chat_mode': 'k8-info'}
)
data = resp.json()
print(f'  Answer: {data["answer"].split(chr(10))[0]}')

print("Step 2: Auto-fetch pods")
resp = requests.post(
    f'{base_url}/api/chat/query',
    headers=headers,
    json={'query': 'show pods in default', 'session_id': session_id, 'chat_mode': 'k8-info'}
)
data = resp.json()

# Check if pods were returned
if 'rmz-wklds-nonprod-use2' in data.get('data', {}):
    cluster_data = data['data']['rmz-wklds-nonprod-use2']
    if 'pods' in cluster_data:
        print(f'✓ SUCCESS: Found {len(cluster_data["pods"])} pods')
        pod_names = [pod.get("name") for pod in cluster_data["pods"][:5]]
        for pod in pod_names:
            print(f'    - {pod}')
        print(f'\n✓ WORKING: Resources now auto-fetch after namespace selection!')
    elif 'error' in cluster_data:
        print(f'✗ ERROR: {cluster_data["error"]}')
    else:
        print(f'✗ No pods in cluster_data. Keys: {list(cluster_data.keys())}')
else:
    print(f'✗ No cluster data in response')

print("\n=== TEST 3: Verify deployments also work ===")
resp = requests.post(
    f'{base_url}/api/chat/query',
    headers=headers,
    json={'query': 'show deployments in default', 'session_id': session_id, 'chat_mode': 'k8-info'}
)
data = resp.json()

if 'rmz-wklds-nonprod-use2' in data.get('data', {}):
    cluster_data = data['data']['rmz-wklds-nonprod-use2']
    if 'deployments' in cluster_data:
        print(f'✓ SUCCESS: Found {len(cluster_data["deployments"])} deployments')
        dep_names = [dep.get("name") for dep in cluster_data["deployments"][:5]]
        for dep in dep_names:
            print(f'    - {dep}')
    elif 'error' in cluster_data:
        print(f'✗ ERROR: {cluster_data["error"]}')
    else:
        print(f'✗ No deployments in response')
