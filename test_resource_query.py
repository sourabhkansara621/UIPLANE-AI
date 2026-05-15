#!/usr/bin/env python3
"""Test script to diagnose resource query issues after namespace selection."""

import requests
import json

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
print(f'Session ID: {session_id}')
print(f'Answer: {data["answer"][:150]}...')

# Test 2: Select namespace
print("\n=== TEST 2: Select namespace 'default' ===")
resp = requests.post(
    f'{base_url}/api/chat/query',
    headers=headers,
    json={'query': 'namespace default for EKS RMZ NonProd', 'session_id': session_id, 'chat_mode': 'k8-info'}
)
data = resp.json()
print(f'Answer: {data["answer"][:150]}...')

# Test 3: Show pods
print("\n=== TEST 3: Show pods (should use 'default' from context) ===")
resp = requests.post(
    f'{base_url}/api/chat/query',
    headers=headers,
    json={'query': 'show pods', 'session_id': session_id, 'chat_mode': 'k8-info'}
)
data = resp.json()
print(f'Answer: {data["answer"][:200]}...')

# Check if pods were returned
if 'rmz-wklds-nonprod-use2' in data.get('data', {}):
    cluster_data = data['data']['rmz-wklds-nonprod-use2']
    if 'pods' in cluster_data:
        print(f'✓ SUCCESS: Found {len(cluster_data["pods"])} pods')
        for pod in cluster_data["pods"][:3]:
            print(f'  - {pod.get("name")}')
    elif 'error' in cluster_data:
        print(f'✗ ERROR: {cluster_data["error"]}')
    else:
        print(f'✗ No pods or error key in cluster_data')
        print(f'  Keys available: {list(cluster_data.keys())}')
else:
    print(f'✗ No cluster data for rmz-wklds-nonprod-use2')
    if data.get('data'):
        print(f'  Keys in data: {list(data["data"].keys())}')

print("\n=== TEST 4: Show deployments (should also use 'default') ===")
resp = requests.post(
    f'{base_url}/api/chat/query',
    headers=headers,
    json={'query': 'show deployments', 'session_id': session_id, 'chat_mode': 'k8-info'}
)
data = resp.json()
print(f'Answer: {data["answer"][:200]}...')

if 'rmz-wklds-nonprod-use2' in data.get('data', {}):
    cluster_data = data['data']['rmz-wklds-nonprod-use2']
    if 'deployments' in cluster_data:
        print(f'✓ SUCCESS: Found {len(cluster_data["deployments"])} deployments')
        for dep in cluster_data["deployments"][:3]:
            print(f'  - {dep.get("name")}')
    elif 'error' in cluster_data:
        print(f'✗ ERROR: {cluster_data["error"]}')
    else:
        print(f'✗ No deployments or error key')
        print(f'  Keys: {list(cluster_data.keys())}')
else:
    print(f'✗ No cluster data returned')
