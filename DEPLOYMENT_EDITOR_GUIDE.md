# Deployment Editor Modal - Implementation Guide

## Overview
Implemented a visual YAML editor modal for editing Kubernetes deployments with save confirmation workflow.

## What Was Added

### Backend Endpoint
- **POST `/api/chat/save-deployment`** - Accepts edited deployment YAML and applies to cluster
  - Validates YAML format
  - Checks RBAC permissions before mutation
  - Creates or patches deployment in Kubernetes
  - Returns success/error response

### Frontend UI Components
- **Deployment Editor Modal** - Full-screen modal with YAML textarea
- **Confirmation Popup** - Validates save intent before applying
- **JavaScript Handlers** - Manage modal state and API communication

### Schema
- **SaveDeploymentRequest** in `models/schemas.py` - Request model with:
  - `session_id`: Chat session ID
  - `deployment_name`: Target deployment name
  - `yaml_content`: Edited YAML manifest
  - `app_name`: Application name
  - `namespace`: Kubernetes namespace

## Usage Workflow

```
1. User selects "k8-agent" chat mode
2. User runs: "edit deployment <name>"
3. Backend returns deployment YAML
4. UI opens modal editor with YAML content
5. User edits YAML
6. User clicks "Save" button
7. Confirmation popup appears asking: "Save changes to deployment X?"
8. User clicks "Confirm Save"
9. API POST to /api/chat/save-deployment with edited YAML
10. Backend validates, checks permissions, applies to cluster
11. Success: "Deployment saved successfully!"
    OR
    Error: "403: You do not have write access to 'sandbox'"
```

## Testing Instructions

### 1. Check Current Permissions
First, verify if user 'priya' has mutation permission on 'sandbox' app:

```python
# In Python REPL or script:
from models.database import get_db, AppOwnership, User
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Connect to database
db = Session()
ownership = db.query(AppOwnership).filter(
    AppOwnership.app_name == 'sandbox',
    User.username == 'priya'
).first()

if ownership:
    print(f"Can read: {ownership.can_read}")
    print(f"Can mutate: {ownership.can_mutate}")
else:
    print("No AppOwnership record found")
```

### 2. Grant Mutation Permission
If user doesn't have `can_mutate=True`, update the database:

```bash
# Using SQLite CLI
sqlite3 database.db

UPDATE AppOwnership 
SET can_mutate = 1 
WHERE app_name = 'sandbox' 
AND user_id = (SELECT id FROM User WHERE username = 'priya');
```

Or via Python:

```python
from models.database import get_db, AppOwnership, User
db = Session()
ownership = db.query(AppOwnership).filter(
    AppOwnership.app_name == 'sandbox'
).update({'can_mutate': True})
db.commit()
```

### 3. Test the Editor

1. **Start the application**:
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8080
   ```

2. **Login** as user 'priya'

3. **Select chat mode**: Click "k8-agent" button

4. **List deployments**:
   - Type: `show all deployments for sandbox`
   - Or: `list deployments in namespace default for sandbox`

5. **Click "edit" button** on a deployment from the list
   - Modal should open with deployment YAML

6. **Edit YAML**:
   - Change `replicas: 2` to `replicas: 3`
   - Or update image tag

7. **Click "Save"**:
   - Confirmation modal should appear
   - Click "Confirm Save"

8. **Verify Result**:
   - Success message with updated deployment name
   - Check cluster: `kubectl get deployment <name> -n <namespace>`

## Key Files Modified

1. **models/schemas.py**
   - Added `SaveDeploymentRequest` schema

2. **api/chat_router.py**
   - Added `POST /api/chat/save-deployment` endpoint
   - Imports: yaml, SaveDeploymentRequest, RBAC functions

3. **ui/templates/index.html**
   - Modal HTML for editor and confirmation
   - JavaScript functions for modal management
   - State tracking in `deploymentEditorState` object

4. **ui/templates/index2.html**
   - Same changes as index.html

## Error Handling

### 403 Forbidden
```
"403: Mutation denied. You do not have write access to 'sandbox'."
```
**Solution**: Grant `can_mutate=True` in AppOwnership table

### Invalid YAML
```
"Invalid YAML: <error details>"
```
**Solution**: Ensure YAML syntax is correct before saving

### Missing Context
```
"Missing deployment context (name, namespace, or app)"
```
**Solution**: Ensure session_id and deployment context are properly set

### Kubernetes API Error
```
"Kubernetes API error: <error reason>"
```
**Solution**: Check cluster connectivity and permissions

## Architecture Decisions

1. **Modal-based editing** - Provides focused editing experience with clear save flow
2. **Confirmation popup** - Prevents accidental deployments
3. **YAML validation** - Catches invalid manifests before cluster apply
4. **RBAC enforcement** - Consistent with platform security model
5. **Session context** - Maintains app/namespace across operations

## Future Enhancements

1. **Diff preview** - Show changes before saving
2. **YAML syntax highlighting** - Color-coded YAML in editor
3. **Field validation** - Warn about invalid values before save
4. **Rollback option** - Add "Revert" button to restore previous version
5. **Batch editing** - Edit multiple resources at once
6. **History** - Track deployment edits with timestamps
