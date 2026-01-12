# Tytan LendingOps & MemberAssist - Deployment Guide

## Overview

This guide walks you through deploying the Tytan LendingOps & MemberAssist solution to Google Cloud Platform in **under 60 minutes**.

**Target Audience**: Cloud engineers, DevOps teams, solution architects

**Prerequisites Time**: 10 minutes
**Terraform Deployment Time**: 15-20 minutes
**Testing & Validation**: 20-30 minutes

---

## Prerequisites

### 1. Google Cloud Account

- Active GCP account with billing enabled
- Organization or project creation permissions
- Estimated cost: $0.50-2.00 for initial testing

### 2. Local Development Environment

**Required Tools**:

| Tool | Version | Installation |
|------|---------|--------------|
| **gcloud CLI** | Latest | [Install Guide](https://cloud.google.com/sdk/docs/install) |
| **Terraform** | 1.6+ | [Install Guide](https://developer.hashicorp.com/terraform/downloads) |
| **Python** | 3.11+ | [Install Guide](https://www.python.org/downloads/) |
| **Git** | 2.0+ | [Install Guide](https://git-scm.com/downloads) |
| **Docker** (optional) | 20.0+ | [Install Guide](https://docs.docker.com/get-docker/) |

**Verify Installation**:
```bash
gcloud version
terraform version
python --version
git --version
docker --version  # optional
```

### 3. GCP Permissions

Your user account needs:
- `roles/owner` OR the following roles:
  - `roles/iam.serviceAccountAdmin`
  - `roles/iam.securityAdmin`
  - `roles/run.admin`
  - `roles/storage.admin`
  - `roles/bigquery.admin`
  - `roles/pubsub.admin`
  - `roles/cloudbuild.builds.editor`

**Check Permissions**:
```bash
gcloud projects get-iam-policy YOUR_PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:user:YOUR_EMAIL"
```

### 4. Enable Required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  bigquery.googleapis.com \
  pubsub.googleapis.com \
  documentai.googleapis.com \
  dialogflow.googleapis.com \
  secretmanager.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com
```

**Time**: ~3 minutes

---

## Deployment Steps

### Step 1: Clone Repository

```bash
git clone https://github.com/sabyasachi-basu-i/crispy-memory.git
cd crispy-memory
```

### Step 2: Configure Environment Variables

Create `infra/terraform.tfvars`:

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
# Required variables
project_id = "your-gcp-project-id"
region     = "us-central1"

# Optional (defaults provided)
environment         = "dev"
dataset_name       = "tytan_lending_ops"
bucket_name_prefix = "tytan-lending"

# Document AI processor IDs (create these first, see Step 3)
# Leave empty for mock mode during initial testing
docai_identity_processor_id = ""
docai_form_processor_id     = ""

# Notification emails
alert_email = "devops@yourcompany.com"
```

**Important**: Update `project_id` to your actual GCP project ID.

### Step 3: Create Document AI Processors (Optional)

For **production deployment**, create Document AI processors:

1. Navigate to [Document AI Console](https://console.cloud.google.com/ai/document-ai)
2. Click "Create Processor"
3. Create two processors:
   - **Identity Processor** (for driver's licenses, passports)
   - **Form Parser** (for paystubs, bank statements)
4. Note the processor IDs (format: `projects/{project}/locations/{location}/processors/{id}`)
5. Update `terraform.tfvars` with processor IDs

**For POC/Testing**: Skip this step. The solution will run in **mock mode** without real Document AI processors.

### Step 4: Initialize Terraform

```bash
cd infra
terraform init
```

**Expected Output**:
```
Initializing the backend...
Initializing provider plugins...
- Finding hashicorp/google versions matching "~> 5.0"...
Terraform has been successfully initialized!
```

### Step 5: Review Deployment Plan

```bash
terraform plan
```

**Review**:
- Resources to be created (~25-30 resources)
- Service accounts and IAM bindings
- Check for any errors

**Time**: 1-2 minutes

### Step 6: Deploy Infrastructure

```bash
terraform apply
```

Type `yes` when prompted.

**Resources Created**:
- 3 Cloud Run services (API, Worker, Webhook)
- 2 Pub/Sub topics + 2 subscriptions + 1 DLQ
- 1 Cloud Storage bucket
- 1 BigQuery dataset + 5 tables
- 5 Service accounts with IAM bindings
- Cloud Logging sinks
- Cloud Monitoring alert policies

**Time**: 15-20 minutes

**Expected Output**:
```
Apply complete! Resources: 28 added, 0 changed, 0 destroyed.

Outputs:

api_url = "https://tytan-api-xyz-uc.a.run.app"
webhook_url = "https://tytan-webhook-xyz-uc.a.run.app"
bucket_name = "tytan-lending-docs-dev"
dataset_id = "tytan_lending_ops"
```

**Save these outputs** — you'll need them for testing.

### Step 7: Verify Deployment

#### Check Cloud Run Services

```bash
gcloud run services list --region=us-central1
```

Expected services:
- `tytan-lending-api`
- `tytan-lending-docai-worker`
- `tytan-lending-webhook`

All should show status `Ready`.

#### Check BigQuery Dataset

```bash
bq ls tytan_lending_ops
```

Expected tables:
- `cases`
- `documents`
- `extracted_fields`
- `field_corrections`
- `audit_log`

#### Check Cloud Storage Bucket

```bash
gsutil ls gs://tytan-lending-docs-dev/
```

Expected folders (empty):
- `cases/`
- `incoming/`
- `rejected/`

#### Check Pub/Sub Topics

```bash
gcloud pubsub topics list
```

Expected topics:
- `document-uploaded`
- `extraction-completed`
- `document-uploaded-dlq`

---

## Testing the Deployment

### Test 1: Health Check

```bash
# Get API URL from Terraform output
API_URL=$(terraform output -raw api_url)

# Test health endpoint
curl $API_URL/health
```

**Expected Response**:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2024-01-15T10:00:00Z"
}
```

### Test 2: Create a Case

```bash
curl -X POST $API_URL/cases \
  -H "Content-Type: application/json" \
  -d '{
    "member_id": "M-TEST-001",
    "loan_type": "auto",
    "loan_amount": 25000,
    "member_contact": {
      "email": "test@example.com",
      "phone": "+15551234567"
    }
  }'
```

**Expected Response**:
```json
{
  "case_id": "CU-2024-00001",
  "status": "SUBMITTED",
  "created_at": "2024-01-15T10:05:00Z",
  "required_documents": [
    "drivers_license",
    "paystub_recent_2",
    "bank_statement_30days"
  ]
}
```

### Test 3: Upload a Document (Mock Mode)

```bash
# Use a sample document from the repo
CASE_ID="CU-2024-00001"  # from previous test

curl -X POST $API_URL/cases/$CASE_ID/documents \
  -F "file=@../sample_data/sample_drivers_license.pdf" \
  -F "document_type=drivers_license"
```

**Expected Response**:
```json
{
  "document_id": "doc-abc-123",
  "gcs_uri": "gs://tytan-lending-docs-dev/cases/CU-2024-00001/doc-abc-123.pdf",
  "upload_status": "success",
  "pubsub_message_id": "1234567890"
}
```

### Test 4: Check Case Status

```bash
curl $API_URL/cases/$CASE_ID
```

**Expected Response** (after Document AI Worker processes):
```json
{
  "case_id": "CU-2024-00001",
  "status": "READY_FOR_REVIEW",
  "documents": [
    {
      "document_id": "doc-abc-123",
      "document_type": "drivers_license",
      "status": "EXTRACTED",
      "extraction_summary": {
        "fields_extracted": 8,
        "avg_confidence": 0.96
      }
    }
  ],
  "extracted_applicant": {
    "full_name": "John Doe",
    "date_of_birth": "1985-03-15"
  }
}
```

**Note**: In mock mode, extraction happens immediately. With real Document AI, allow 10-30 seconds for processing.

### Test 5: Query BigQuery

```bash
bq query --use_legacy_sql=false \
  'SELECT case_id, status, created_at FROM `tytan_lending_ops.cases` LIMIT 5'
```

**Expected Output**:
```
+----------------+------------+---------------------+
|    case_id     |   status   |     created_at      |
+----------------+------------+---------------------+
| CU-2024-00001  | SUBMITTED  | 2024-01-15 10:05:00 |
+----------------+------------+---------------------+
```

---

## Local Development Setup

For developers who want to run services locally before deploying:

### 1. Set Up Python Virtual Environment

```bash
cd services/cloud-run-api
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Local Environment

Create `.env` file:

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"
export BUCKET_NAME="tytan-lending-docs-dev"
export DATASET_ID="tytan_lending_ops"
export PUBSUB_TOPIC="document-uploaded"
export MOCK_MODE="true"
export PORT="8080"
```

Load environment:
```bash
source .env
```

### 3. Authenticate with GCP

```bash
gcloud auth application-default login
```

### 4. Run API Locally

```bash
cd services/cloud-run-api
python main.py
```

API will be available at `http://localhost:8080`

### 5. Run Worker Locally

In a separate terminal:

```bash
cd pipelines/document_ai_worker
source venv/bin/activate
python worker.py
```

Worker will poll Pub/Sub and process messages.

### 6. Test Locally

```bash
curl http://localhost:8080/health
curl -X POST http://localhost:8080/cases -H "Content-Type: application/json" -d '{...}'
```

---

## Environment-Specific Deployments

### Development Environment

```bash
cd infra
terraform workspace new dev
terraform workspace select dev
terraform apply -var-file=dev.tfvars
```

**dev.tfvars**:
```hcl
project_id  = "tytan-lending-dev"
environment = "dev"
min_instances_api = 0      # cost savings
min_instances_worker = 0
mock_mode_enabled = true   # use mock Document AI
```

### Staging Environment

```bash
terraform workspace new staging
terraform workspace select staging
terraform apply -var-file=staging.tfvars
```

**staging.tfvars**:
```hcl
project_id  = "tytan-lending-staging"
environment = "staging"
min_instances_api = 1      # reduce cold starts
min_instances_worker = 0
mock_mode_enabled = false  # use real Document AI
docai_identity_processor_id = "projects/.../processors/abc123"
```

### Production Environment

```bash
terraform workspace new prod
terraform workspace select prod
terraform apply -var-file=prod.tfvars
```

**prod.tfvars**:
```hcl
project_id  = "tytan-lending-prod"
environment = "prod"
min_instances_api = 2      # high availability
min_instances_worker = 1
mock_mode_enabled = false
docai_identity_processor_id = "projects/.../processors/abc123"
enable_audit_logging = true
enable_backup_replication = true
log_retention_days = 2555  # 7 years
```

---

## Continuous Deployment (Cloud Build)

### 1. Create Cloud Build Trigger

```bash
gcloud builds triggers create github \
  --name="tytan-lending-deploy" \
  --repo-name="crispy-memory" \
  --repo-owner="sabyasachi-basu-i" \
  --branch-pattern="^main$" \
  --build-config="cloudbuild.yaml"
```

### 2. Cloud Build Configuration

See `cloudbuild.yaml` in repo root:

```yaml
steps:
  # Run tests
  - name: 'python:3.11'
    entrypoint: 'bash'
    args:
      - '-c'
      - |
        pip install -r services/cloud-run-api/requirements.txt
        pytest services/cloud-run-api/tests/

  # Build and push API image
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', 'gcr.io/$PROJECT_ID/tytan-api:$SHORT_SHA', 'services/cloud-run-api']
  - name: 'gcr.io/cloud-builders/docker'
    args: ['push', 'gcr.io/$PROJECT_ID/tytan-api:$SHORT_SHA']

  # Deploy to Cloud Run
  - name: 'gcr.io/cloud-builders/gcloud'
    args:
      - 'run'
      - 'deploy'
      - 'tytan-lending-api'
      - '--image=gcr.io/$PROJECT_ID/tytan-api:$SHORT_SHA'
      - '--region=us-central1'
      - '--platform=managed'
```

### 3. Manual Deployment Trigger

```bash
gcloud builds submit --config=cloudbuild.yaml
```

---

## Troubleshooting

### Issue: Terraform Apply Fails with "API Not Enabled"

**Error**:
```
Error creating service: googleapi: Error 403: Cloud Run API has not been used
```

**Solution**:
```bash
gcloud services enable run.googleapis.com
terraform apply
```

---

### Issue: Cloud Run Service Returns 403 Forbidden

**Cause**: IAM invoker permission missing

**Solution**:
```bash
# Allow unauthenticated access (for testing only)
gcloud run services add-iam-policy-binding tytan-lending-api \
  --region=us-central1 \
  --member="allUsers" \
  --role="roles/run.invoker"

# For production, use authenticated access
gcloud run services add-iam-policy-binding tytan-lending-api \
  --region=us-central1 \
  --member="serviceAccount:your-app@project.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

---

### Issue: Document AI Worker Not Processing Messages

**Check Pub/Sub Subscription**:
```bash
gcloud pubsub subscriptions describe document-ai-worker-sub
```

**Check Worker Logs**:
```bash
gcloud logs read "resource.type=cloud_run_revision AND resource.labels.service_name=tytan-lending-docai-worker" \
  --limit=50 \
  --format=json
```

**Common Causes**:
- Worker service account lacks `pubsub.subscriber` role
- Subscription has no messages (check topic)
- Worker crashed on startup (check logs for Python errors)

**Manual Message Pull** (for debugging):
```bash
gcloud pubsub subscriptions pull document-ai-worker-sub --limit=1
```

---

### Issue: BigQuery Permission Denied

**Error**:
```
403 Access Denied: BigQuery BigQuery: Permission denied while getting Drive credentials
```

**Solution**:
```bash
# Grant service account BigQuery permissions
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:tytan-api-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"
```

---

### Issue: Cloud Storage Upload Fails

**Check Bucket Permissions**:
```bash
gsutil iam get gs://tytan-lending-docs-dev
```

**Grant Upload Permission**:
```bash
gsutil iam ch \
  serviceAccount:tytan-api-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com:objectCreator \
  gs://tytan-lending-docs-dev
```

---

### Issue: High Cloud Costs

**Check Current Spend**:
```bash
gcloud billing accounts list
gcloud billing projects describe YOUR_PROJECT_ID
```

**Cost Optimization Checklist**:
- ✅ Set `min_instances = 0` for non-prod environments
- ✅ Enable BigQuery table partitioning (already in Terraform)
- ✅ Set GCS lifecycle policy to move old docs to Coldline (already in Terraform)
- ✅ Use mock mode for development (no Document AI charges)
- ✅ Set Pub/Sub message retention to 1 day (vs. default 7 days)

**Budget Alert**:
```bash
gcloud billing budgets create \
  --billing-account=BILLING_ACCOUNT_ID \
  --display-name="Tytan Lending Budget" \
  --budget-amount=100 \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90
```

---

## Monitoring & Observability

### View Logs

**API Logs**:
```bash
gcloud logs read "resource.type=cloud_run_revision AND resource.labels.service_name=tytan-lending-api" \
  --limit=20 \
  --format=json | jq -r '.[] | .textPayload'
```

**Worker Logs**:
```bash
gcloud logs read "resource.type=cloud_run_revision AND resource.labels.service_name=tytan-lending-docai-worker" \
  --limit=20
```

**Filter by Case ID** (correlation):
```bash
gcloud logs read "jsonPayload.case_id=CU-2024-00001" --limit=50
```

### Metrics Dashboard

1. Open [Cloud Console](https://console.cloud.google.com)
2. Navigate to **Monitoring > Dashboards**
3. Import pre-built dashboard: `monitoring/tytan_lending_dashboard.json`

**Key Metrics**:
- API request rate, latency, error rate
- Document processing throughput
- BigQuery bytes scanned
- Pub/Sub message backlog

### Set Up Alerts

```bash
# Alert on high error rate
gcloud alpha monitoring policies create \
  --notification-channels=YOUR_CHANNEL_ID \
  --display-name="High API Error Rate" \
  --condition-threshold-value=0.05 \
  --condition-threshold-filter='resource.type="cloud_run_revision" AND metric.type="run.googleapis.com/request_count" AND metric.labels.response_code_class="5xx"'
```

---

## Updating the Deployment

### Update Code

```bash
git pull origin main
cd infra
terraform apply
```

Terraform will detect changes and update Cloud Run services.

### Update Configuration

Edit `terraform.tfvars`, then:

```bash
terraform plan   # review changes
terraform apply  # apply changes
```

### Rollback to Previous Version

```bash
# List Cloud Run revisions
gcloud run revisions list --service=tytan-lending-api --region=us-central1

# Rollback to specific revision
gcloud run services update-traffic tytan-lending-api \
  --region=us-central1 \
  --to-revisions=tytan-lending-api-00005-abc=100
```

---

## Tear Down (Clean Up)

**Warning**: This will **permanently delete** all data.

```bash
cd infra
terraform destroy
```

Type `yes` to confirm.

**Manual Cleanup** (if Terraform fails):

```bash
# Delete Cloud Run services
gcloud run services delete tytan-lending-api --region=us-central1 --quiet
gcloud run services delete tytan-lending-docai-worker --region=us-central1 --quiet
gcloud run services delete tytan-lending-webhook --region=us-central1 --quiet

# Delete BigQuery dataset
bq rm -r -f -d tytan_lending_ops

# Delete Cloud Storage bucket
gsutil rm -r gs://tytan-lending-docs-dev

# Delete Pub/Sub topics
gcloud pubsub topics delete document-uploaded
gcloud pubsub topics delete extraction-completed
gcloud pubsub topics delete document-uploaded-dlq

# Delete service accounts
gcloud iam service-accounts delete tytan-api-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com --quiet
gcloud iam service-accounts delete tytan-worker-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com --quiet
gcloud iam service-accounts delete tytan-webhook-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com --quiet
```

---

## Next Steps

1. ✅ **Customize for Your Use Case**
   - Integrate with your LOS/core banking system
   - Add custom document types
   - Train custom Document AI processors

2. ✅ **Set Up Dialogflow CX Agent**
   - Import agent configuration from `dialogflow/agent.json`
   - Connect webhook to Cloud Run webhook URL
   - Test conversational flows

3. ✅ **Enable Production Features**
   - Turn off mock mode
   - Enable audit logging
   - Set up BigQuery scheduled queries for analytics
   - Configure backup and disaster recovery

4. ✅ **Security Hardening**
   - Review IAM policies (see `security_governance.md`)
   - Enable VPC Service Controls (optional)
   - Set up Cloud Armor for DDoS protection (if public-facing)

5. ✅ **Load Testing**
   - Use sample data to simulate 100+ concurrent uploads
   - Measure autoscaling behavior
   - Validate cost projections

6. ✅ **Train Your Team**
   - Review operations runbook
   - Run incident drills (e.g., Pub/Sub backlog, Document AI quota exceeded)
   - Document custom playbooks

---

## Support & Resources

- **Documentation**: See `/docs` folder
- **Sample Data**: See `/sample_data` folder
- **Terraform Modules**: See `/infra` folder
- **Architecture Diagrams**: See `/architecture` folder

**Questions?** Contact Tytan Support: support@tytan.tech

---

**Document Version**: 1.0
**Last Updated**: 2026-01-12
**Deployment Time**: 60 minutes (verified)
