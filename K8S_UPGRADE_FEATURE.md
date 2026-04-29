# Kubernetes Cluster Upgrade Feature

## Overview
Added a new **"Upgrade K8s"** button in the top-right header that allows users to upgrade their selected Kubernetes cluster to the latest available version.

## Features Implemented

### 1. **UI Components**

#### Header Button
- **Location**: Top-right corner, next to "General Questions" and "logout" buttons
- **Style**: Orange/warning color (using `--warn` color variable)
- **Behavior**: 
  - Disabled until a cluster is selected
  - Clicking opens the upgrade modal dialog
  - Shows tooltip: "Upgrade K8s version for selected cluster"

#### Upgrade Modal Dialog
- **Title**: "Upgrade Kubernetes Version"
- **Displays**:
  - **Current Version**: Shows the currently running K8s version on the selected cluster
  - **Selected Cluster**: Shows which cluster is targeted
  - **Available Versions**: List of upgrade-compatible K8s versions
  - **Target Version**: Selected upgrade version
  - **Warning Box**: Important notice about potential downtime

- **Actions**:
  - **Cancel**: Close modal without action
  - **Upgrade to vX.Y**: Trigger upgrade immediately after confirmation

### 2. **Frontend JavaScript Functions**

#### Core Functions:
- **`openUpgradeModal()`**
  - Validates cluster selection
  - Loads current and available versions
  - Displays upgrade modal

- **`closeUpgradeModal()`**
  - Closes the modal
  - Resets selection state

- **`loadUpgradeVersions()`**
  - Fetches current K8s version from backend
  - Retrieves list of available upgrade versions
  - Displays versions as selectable buttons

- **`selectUpgradeVersion(version, element)`**
  - Marks selected version as highlighted
  - Enables the upgrade button
  - Updates target version display

- **`confirmUpgrade()`**
  - Confirms upgrade via browser dialog
  - Sends upgrade request to backend API
  - Logs audit trail
  - Displays success/error messages

### 3. **Backend API Endpoints**

#### GET `/api/k8s/upgrade/{cluster_name}/versions`
- **Purpose**: Get current and available K8s versions for a cluster
- **Authentication**: Required (Bearer token)
- **Authorization**: User must have access to at least one app deployed on the cluster
- **Returns**:
  ```json
  {
    "cluster_name": "string",
    "current_version": "1.28.5",
    "available_versions": ["1.29", "1.30"]
  }
  ```
- **Error Handling**:
  - 404: Cluster not found in registry
  - 403: User lacks access to cluster
  - 500: Failed to fetch version

#### POST `/api/k8s/upgrade/{cluster_name}`
- **Purpose**: Initiate cluster upgrade to target version
- **Authentication**: Required (Bearer token)
- **Authorization**: User must have access to at least one app on the cluster
- **Request Body**:
  ```json
  {
    "target_version": "1.30"
  }
  ```
- **Returns**:
  ```json
  {
    "status": "upgrade_initiated",
    "cluster_name": "cluster-name",
    "target_version": "1.30",
    "message": "Cluster upgrade to v1.30 has been initiated...",
    "cloud_provider": "eks"
  }
  ```
- **Side Effects**:
  - Creates AuditLog entry with upgrade details
  - Logs action with user, cluster, and version info
- **Error Handling**:
  - 400: Missing or invalid target_version
  - 403: User lacks access to cluster
  - 404: Cluster not found in registry
  - 500: Upgrade initiation failed

### 4. **Styling (CSS)**

Added classes for upgrade button and version selection:
- **`.upgrade-btn`**: Header button styling (warning color)
- **`.upgrade-btn:hover`**: Hover state
- **`.upgrade-btn:disabled`**: Disabled state
- **`.version-btn`**: Individual version selector button
- **`.version-btn:hover`**: Hover feedback
- **`.version-btn.selected`**: Highlighted selected version

### 5. **Security & Validation**

- **RBAC Check**: Verifies user has access to at least one app on the cluster
- **Input Validation**: Verifies target version format is valid (x.y or x.y.z)
- **Audit Logging**: All upgrade requests logged with user, cluster, version
- **User Confirmation**: Browser confirmation dialog before proceeding
- **Error Messages**: Clear error feedback for all failure cases

## Available K8s Versions

Currently configured with popular LTS versions:
- 1.30
- 1.29
- 1.28
- 1.27
- 1.26

**Note**: In production, these would be fetched from cloud provider APIs (EKS, GKE, AKS) dynamically.

## Usage Flow

1. **Select Cluster**: Choose a cluster from the sidebar
2. **Click "Upgrade K8s"**: Button appears in top-right header
3. **View Current Version**: Modal shows current K8s version
4. **Select Target Version**: Click desired upgrade version
5. **Confirm Upgrade**: Click "Upgrade to vX.Y" button
6. **Confirm Again**: Confirm in browser dialog
7. **Status**: Success message shows upgrade has been initiated

## Important Notes

⚠️ **Downtime Warning**: The upgrade modal displays a warning that cluster upgrades may cause brief downtime and users should ensure workloads are resilient.

✅ **Audit Trail**: All upgrade attempts (successful and failed) are logged in the AuditLog table with:
   - User ID
   - Cluster name
   - Target version
   - Timestamp
   - Success/failure status

✅ **Future Enhancement**: Currently returns simulated response. Production implementation would:
   - Call AWS EKS API for EKS clusters
   - Call Google GKE API for GKE clusters
   - Call Azure AKS API for AKS clusters
   - Monitor upgrade progress
   - Return real-time status updates

## Files Modified

1. **ui/templates/index.html**
   - Added "Upgrade K8s" button to header
   - Added upgrade modal HTML structure

2. **ui/static/css/index.css**
   - Added `.upgrade-btn` styling (warning color)
   - Added `.version-btn` and `.version-btn.selected` styling

3. **ui/static/js/index.js**
   - Added `openUpgradeModal()` function
   - Added `closeUpgradeModal()` function
   - Added `loadUpgradeVersions()` function
   - Added `selectUpgradeVersion()` function
   - Added `confirmUpgrade()` function
   - Added state variable `selectedUpgradeVersion`

4. **api/k8s_router.py**
   - Added `GET /api/k8s/upgrade/{cluster_name}/versions` endpoint
   - Added `POST /api/k8s/upgrade/{cluster_name}` endpoint
   - Both endpoints include full RBAC validation and audit logging

## Testing the Feature

1. **Restart the application**:
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8080
   ```

2. **Open the UI** in browser (usually http://localhost:8080)

3. **Login** with your credentials

4. **Select a cluster** from the sidebar (button won't work without selection)

5. **Click "Upgrade K8s"** button in top-right header

6. **View available versions** and select one

7. **Confirm upgrade** twice (once in modal, once in browser dialog)

8. **Check Success Message** confirming upgrade initiation

## Database Audit

Upgrade actions are automatically logged. Query AuditLog:
```sql
SELECT * FROM audit_log 
WHERE action = 'CLUSTER_UPGRADE' 
ORDER BY timestamp DESC;
```
