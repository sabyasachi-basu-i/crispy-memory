# Tytan LendingOps & MemberAssist - Security & Governance

## Overview

This document outlines the security controls, compliance measures, and governance policies for the Tytan LendingOps & MemberAssist solution.

**Security Principles**:
1. **Least Privilege Access**: Every service account has minimum permissions needed
2. **Defense in Depth**: Multiple layers of security controls
3. **Audit Everything**: Immutable logs for all data access and mutations
4. **Encrypt Everything**: At-rest and in-transit encryption by default
5. **Compliance-First**: Designed for GLBA, FCRA, ECOA, and BSA/AML requirements

---

## Identity & Access Management (IAM)

### Service Account Strategy

Each component has a dedicated service account with minimal permissions:

#### 1. Cloud Run API Service Account

**Name**: `tytan-api-sa@{project}.iam.gserviceaccount.com`

**Purpose**: Handle case and document management API requests

**Permissions**:

| Resource | Role | Justification |
|----------|------|---------------|
| Cloud Storage (bucket) | `roles/storage.objectCreator` | Upload documents to GCS |
| Cloud Storage (bucket) | `roles/storage.objectViewer` | Generate signed URLs for downloads |
| BigQuery (dataset) | `roles/bigquery.dataEditor` | Insert/update case and document records |
| BigQuery (dataset) | `roles/bigquery.jobUser` | Execute queries |
| Pub/Sub (topic: document.uploaded) | `roles/pubsub.publisher` | Publish document upload events |
| Cloud Logging | `roles/logging.logWriter` | Write structured logs |

**Does NOT have**:
- ❌ BigQuery admin (cannot delete tables)
- ❌ Storage admin (cannot delete bucket)
- ❌ IAM admin (cannot modify permissions)
- ❌ Pub/Sub subscriber (cannot pull messages)

**Terraform Configuration**:
```hcl
resource "google_service_account" "api_sa" {
  account_id   = "tytan-api-sa"
  display_name = "Tytan API Service Account"
}

resource "google_storage_bucket_iam_member" "api_storage" {
  bucket = google_storage_bucket.documents.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_bigquery_dataset_iam_member" "api_bigquery" {
  dataset_id = google_bigquery_dataset.lending_ops.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.api_sa.email}"
}
```

---

#### 2. Document AI Worker Service Account

**Name**: `tytan-worker-sa@{project}.iam.gserviceaccount.com`

**Purpose**: Process documents with Document AI, write extraction results

**Permissions**:

| Resource | Role | Justification |
|----------|------|---------------|
| Cloud Storage (bucket) | `roles/storage.objectViewer` | Read documents for processing |
| BigQuery (dataset) | `roles/bigquery.dataEditor` | Write extracted fields |
| BigQuery (dataset) | `roles/bigquery.jobUser` | Execute queries |
| Pub/Sub (subscription: document-ai-worker-sub) | `roles/pubsub.subscriber` | Pull messages from subscription |
| Pub/Sub (topic: extraction.completed) | `roles/pubsub.publisher` | Publish extraction results |
| Document AI (processors) | `roles/documentai.apiUser` | Call Document AI API |
| Cloud Logging | `roles/logging.logWriter` | Write structured logs |

**Does NOT have**:
- ❌ Storage write permissions (read-only for documents)
- ❌ Pub/Sub publisher on document.uploaded (cannot create new upload events)

---

#### 3. Dialogflow Webhook Service Account

**Name**: `tytan-webhook-sa@{project}.iam.gserviceaccount.com`

**Purpose**: Query case status for chatbot responses

**Permissions**:

| Resource | Role | Justification |
|----------|------|---------------|
| BigQuery (dataset) | `roles/bigquery.dataViewer` | Read case and document status (read-only) |
| BigQuery (dataset) | `roles/bigquery.jobUser` | Execute SELECT queries |
| Cloud Logging | `roles/logging.logWriter` | Write structured logs |

**Does NOT have**:
- ❌ BigQuery write permissions (read-only access)
- ❌ Storage access (no document download capability)
- ❌ Pub/Sub access (no event publishing)

---

### Human User Roles

#### Loan Officer Role

**Custom IAM Role**: `roles/tytan.loanOfficer`

**Permissions**:
- View cases and documents (BigQuery read)
- Approve/reject extraction reviews (API write via web UI)
- Download documents (signed URLs from API)
- Cannot delete cases or modify audit logs

**Implementation**:
```hcl
resource "google_project_iam_custom_role" "loan_officer" {
  role_id     = "tytanLoanOfficer"
  title       = "Tytan Loan Officer"
  permissions = [
    "bigquery.tables.get",
    "bigquery.tables.getData",
    "run.routes.invoke"  # Call API
  ]
}

resource "google_project_iam_member" "loan_officers" {
  project = var.project_id
  role    = google_project_iam_custom_role.loan_officer.name
  member  = "group:loan-officers@creditunion.com"
}
```

---

#### Compliance Auditor Role

**Custom IAM Role**: `roles/tytan.auditor`

**Permissions**:
- Read-only access to all BigQuery tables (including audit_log)
- Read-only access to Cloud Logging
- Export audit reports
- Cannot modify any data

**Implementation**:
```hcl
resource "google_project_iam_custom_role" "auditor" {
  role_id     = "tytanAuditor"
  title       = "Tytan Compliance Auditor"
  permissions = [
    "bigquery.datasets.get",
    "bigquery.tables.get",
    "bigquery.tables.getData",
    "bigquery.tables.export",
    "logging.logs.list",
    "logging.logEntries.list"
  ]
}
```

---

#### DevOps Engineer Role

**Standard IAM Roles**:
- `roles/run.admin` (deploy Cloud Run services)
- `roles/bigquery.admin` (manage dataset schema)
- `roles/storage.admin` (manage bucket lifecycle)
- `roles/iam.serviceAccountUser` (impersonate service accounts for deployment)

**Does NOT have** (production):
- ❌ BigQuery data viewer (cannot see PII)
- ❌ Storage object viewer (cannot download documents)

---

### Workload Identity (GKE Integration)

If running on GKE instead of Cloud Run:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: tytan-api
  annotations:
    iam.gke.io/gcp-service-account: tytan-api-sa@PROJECT_ID.iam.gserviceaccount.com
```

Bind Kubernetes SA to GCP SA:
```bash
gcloud iam service-accounts add-iam-policy-binding \
  tytan-api-sa@PROJECT_ID.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:PROJECT_ID.svc.id.goog[default/tytan-api]"
```

---

## Data Protection

### Encryption at Rest

**Cloud Storage**:
- Default: Google-managed encryption keys (AES-256)
- Optional: Customer-managed encryption keys (CMEK) via Cloud KMS

**Enable CMEK**:
```hcl
resource "google_kms_key_ring" "lending_ops" {
  name     = "tytan-lending-keyring"
  location = var.region
}

resource "google_kms_crypto_key" "document_key" {
  name     = "document-encryption-key"
  key_ring = google_kms_key_ring.lending_ops.id
}

resource "google_storage_bucket" "documents" {
  name     = "${var.project_id}-lending-docs"
  location = var.region

  encryption {
    default_kms_key_name = google_kms_crypto_key.document_key.id
  }
}
```

**BigQuery**:
- Default: Google-managed encryption
- Optional: CMEK (configure per-dataset or per-table)

---

### Encryption in Transit

**All communications use TLS 1.3**:
- Client → Cloud Run API: HTTPS (TLS 1.3)
- API → BigQuery: Encrypted API calls
- API → Cloud Storage: Encrypted API calls
- Worker → Document AI: Encrypted API calls
- Pub/Sub: Encrypted message transport

**Enforce HTTPS-only**:
```hcl
resource "google_cloud_run_service" "api" {
  name     = "tytan-lending-api"
  location = var.region

  template {
    spec {
      containers {
        image = "gcr.io/${var.project_id}/tytan-api:latest"
      }
    }
  }

  traffic {
    percent         = 100
    latest_revision = true
  }

  # HTTPS is enforced by default on Cloud Run
}
```

---

### Data Classification & Handling

| Data Type | Classification | Encryption | Retention | Access Control |
|-----------|---------------|------------|-----------|----------------|
| **PII (SSN, DOB)** | Highly Sensitive | CMEK + TLS | 7 years | Loan officers + auditors only |
| **Financial Data** | Sensitive | CMEK + TLS | 7 years | Loan officers + auditors |
| **Documents (PDFs)** | Sensitive | CMEK + TLS | 7 years | Signed URLs (time-limited) |
| **Audit Logs** | Confidential | Default + TLS | 10 years | Auditors only |
| **Extracted Fields** | Sensitive | Default + TLS | 7 years | Loan officers + auditors |
| **Case Metadata** | Internal | Default + TLS | 7 years | Loan officers + chatbot |

---

### Data Masking (BigQuery)

For non-production environments, mask PII:

```sql
-- Create view with masked SSN
CREATE VIEW `tytan_lending_ops.cases_masked` AS
SELECT
  case_id,
  member_id,
  loan_type,
  loan_amount,
  status,
  created_at,
  CONCAT('XXX-XX-', SUBSTR(ssn, -4, 4)) AS ssn_masked,  -- Mask SSN
  member_contact_email,  -- Keep email for testing
  member_contact_phone
FROM `tytan_lending_ops.cases`;

-- Grant access to masked view only
GRANT `roles/bigquery.dataViewer`
ON TABLE `tytan_lending_ops.cases_masked`
TO "group:developers@creditunion.com";
```

---

## Audit Logging

### Cloud Audit Logs

**Enabled by Default**:
- Admin Activity Logs (always on, no cost)
- Data Access Logs (configurable)

**Configuration**:
```hcl
resource "google_project_iam_audit_config" "audit_config" {
  project = var.project_id
  service = "allServices"

  audit_log_config {
    log_type = "ADMIN_READ"  # Who viewed IAM policies, configurations
  }

  audit_log_config {
    log_type = "DATA_READ"   # Who read BigQuery tables, GCS objects
  }

  audit_log_config {
    log_type = "DATA_WRITE"  # Who inserted/updated BigQuery rows, uploaded to GCS
  }
}
```

**Log Retention**:
- Default: 400 days in Cloud Logging
- Long-term: Export to GCS bucket with 10-year retention

**Export to GCS**:
```hcl
resource "google_logging_project_sink" "audit_export" {
  name        = "audit-log-export"
  destination = "storage.googleapis.com/${google_storage_bucket.audit_logs.name}"

  filter = <<-EOT
    logName:"cloudaudit.googleapis.com" OR
    protoPayload.serviceName="bigquery.googleapis.com" OR
    protoPayload.serviceName="storage.googleapis.com"
  EOT

  unique_writer_identity = true
}

resource "google_storage_bucket" "audit_logs" {
  name          = "${var.project_id}-audit-logs"
  location      = var.region
  storage_class = "ARCHIVE"

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 3650  # 10 years
    }
  }
}
```

---

### Application Audit Log (BigQuery)

Custom audit table for business events:

**Schema**:
```sql
CREATE TABLE `tytan_lending_ops.audit_log` (
  event_id STRING NOT NULL,
  case_id STRING,
  event_type STRING NOT NULL,  -- CASE_CREATED, DOCUMENT_UPLOADED, etc.
  actor STRING NOT NULL,        -- Service account or user email
  timestamp TIMESTAMP NOT NULL,
  payload JSON,                 -- Full event details
  ip_address STRING,
  user_agent STRING
)
PARTITION BY DATE(timestamp)
CLUSTER BY case_id, event_type;
```

**Example Insert** (from API code):
```python
def log_audit_event(case_id, event_type, actor, payload, request):
    event = {
        "event_id": str(uuid.uuid4()),
        "case_id": case_id,
        "event_type": event_type,
        "actor": actor,
        "timestamp": datetime.utcnow().isoformat(),
        "payload": payload,
        "ip_address": request.headers.get("X-Forwarded-For", request.remote_addr),
        "user_agent": request.headers.get("User-Agent")
    }

    bq_client.insert_rows_json("tytan_lending_ops.audit_log", [event])
```

**Audit Query Examples**:

```sql
-- Who accessed case CU-2024-00123?
SELECT timestamp, actor, event_type, ip_address
FROM `tytan_lending_ops.audit_log`
WHERE case_id = 'CU-2024-00123'
ORDER BY timestamp DESC;

-- How many cases did each loan officer review today?
SELECT actor, COUNT(*) as reviews
FROM `tytan_lending_ops.audit_log`
WHERE event_type = 'REVIEW_COMPLETED'
  AND DATE(timestamp) = CURRENT_DATE()
GROUP BY actor
ORDER BY reviews DESC;

-- Detect suspicious activity: mass data export
SELECT actor, COUNT(*) as export_count
FROM `tytan_lending_ops.audit_log`
WHERE event_type = 'CASE_EXPORTED'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
GROUP BY actor
HAVING export_count > 100;
```

---

## Network Security

### VPC Service Controls (Optional - High Security Environments)

Create security perimeter around GCP resources:

```hcl
resource "google_access_context_manager_service_perimeter" "lending_perimeter" {
  parent = "accessPolicies/${var.access_policy_id}"
  name   = "accessPolicies/${var.access_policy_id}/servicePerimeters/lendingPerimeter"
  title  = "Tytan Lending Ops Perimeter"

  status {
    restricted_services = [
      "bigquery.googleapis.com",
      "storage.googleapis.com",
      "documentai.googleapis.com"
    ]

    resources = [
      "projects/${var.project_id}"
    ]

    # Allow access only from corporate network
    ingress_policies {
      ingress_from {
        sources {
          access_level = "accessPolicies/${var.access_policy_id}/accessLevels/corpNetwork"
        }
      }
    }
  }
}
```

**Effect**: Prevents data exfiltration even if attacker compromises credentials

---

### Cloud Armor (DDoS Protection)

If API is public-facing:

```hcl
resource "google_compute_security_policy" "api_policy" {
  name = "tytan-api-security-policy"

  # Block traffic from known bad IPs
  rule {
    action   = "deny(403)"
    priority = "1000"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["203.0.113.0/24"]  # Example bad IP range
      }
    }
  }

  # Rate limit: max 100 req/minute per IP
  rule {
    action   = "rate_based_ban"
    priority = "2000"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      rate_limit_threshold {
        count        = 100
        interval_sec = 60
      }
    }
  }

  # Default: allow all other traffic
  rule {
    action   = "allow"
    priority = "2147483647"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}
```

---

## Compliance Framework

### GLBA (Gramm-Leach-Bliley Act)

**Requirement**: Financial institutions must protect customer information

**How We Comply**:
- ✅ Encryption at rest (GCS, BigQuery)
- ✅ Encryption in transit (TLS 1.3)
- ✅ Access controls (IAM roles, least privilege)
- ✅ Audit logging (all data access logged)
- ✅ Incident response plan (see operations_runbook.md)

---

### FCRA (Fair Credit Reporting Act)

**Requirement**: Accurate reporting, consumer access, dispute resolution

**How We Comply**:
- ✅ Extraction confidence scoring (flag low-confidence fields for review)
- ✅ Human review workflow (loan officers verify extracted data)
- ✅ Audit trail (all corrections logged with reviewer ID and timestamp)
- ✅ Data accuracy metrics (track extraction error rate)

---

### ECOA (Equal Credit Opportunity Act)

**Requirement**: Non-discrimination, record retention

**How We Comply**:
- ✅ 7-year retention (BigQuery partition expiration + GCS lifecycle policy)
- ✅ Immutable audit log (cannot delete or modify audit_log rows)
- ✅ Reason for decision captured (review comments stored in field_corrections)

**Retention Configuration**:
```hcl
resource "google_bigquery_table" "cases" {
  dataset_id = google_bigquery_dataset.lending_ops.dataset_id
  table_id   = "cases"

  time_partitioning {
    type          = "DAY"
    expiration_ms = 220752000000  # 7 years in milliseconds
    field         = "created_at"
  }

  deletion_protection = true  # Prevent accidental deletion
}
```

---

### BSA/AML (Bank Secrecy Act / Anti-Money Laundering)

**Requirement**: Identity verification, suspicious activity reporting

**How We Comply**:
- ✅ Document AI extracts ID information (name, DOB, address, ID number)
- ✅ High confidence threshold for identity fields (must be > 95%)
- ✅ Audit log tracks all identity verifications
- ✅ Alert on anomalies (e.g., 10 applications from same IP in 1 hour)

**Anomaly Detection Query**:
```sql
-- Alert: Multiple applications from same IP
SELECT ip_address, COUNT(*) as case_count
FROM `tytan_lending_ops.audit_log`
WHERE event_type = 'CASE_CREATED'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
GROUP BY ip_address
HAVING case_count > 5
ORDER BY case_count DESC;
```

---

## Secrets Management

### Secret Manager Integration

Store sensitive configuration (API keys, database passwords, OAuth tokens):

**Create Secret**:
```bash
echo -n "my-docai-api-key" | gcloud secrets create docai-api-key --data-file=-
```

**Grant Access**:
```bash
gcloud secrets add-iam-policy-binding docai-api-key \
  --member="serviceAccount:tytan-worker-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

**Access in Code** (Python):
```python
from google.cloud import secretmanager

def get_secret(secret_id):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

# Usage
api_key = get_secret("docai-api-key")
```

**Terraform Configuration**:
```hcl
resource "google_secret_manager_secret" "docai_key" {
  secret_id = "docai-api-key"

  replication {
    automatic = true
  }
}

resource "google_secret_manager_secret_iam_member" "worker_access" {
  secret_id = google_secret_manager_secret.docai_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker_sa.email}"
}
```

---

## Vulnerability Management

### Dependency Scanning

**Scan Python dependencies**:
```bash
pip install safety
safety check --file requirements.txt
```

**Scan Docker images**:
```bash
gcloud artifacts docker images scan gcr.io/PROJECT_ID/tytan-api:latest
```

### Container Security

**Best Practices**:
- Use official base images (e.g., `python:3.11-slim`)
- Run as non-root user
- Multi-stage builds to minimize image size
- Scan for CVEs in CI/CD pipeline

**Dockerfile Example**:
```dockerfile
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user -r requirements.txt

FROM python:3.11-slim
WORKDIR /app

# Create non-root user
RUN useradd -m -u 1000 appuser

# Copy dependencies
COPY --from=builder /root/.local /home/appuser/.local
COPY . .

# Change ownership
RUN chown -R appuser:appuser /app

# Run as non-root
USER appuser

ENV PATH=/home/appuser/.local/bin:$PATH

CMD ["python", "main.py"]
```

---

## Incident Response

### Security Incident Playbook

**Phase 1: Detection**
- Alert triggered (e.g., "Unusual data access pattern detected")
- Security team notified via PagerDuty/email

**Phase 2: Containment**
- Revoke compromised service account keys
- Disable compromised user account
- Block malicious IPs via Cloud Armor

**Phase 3: Investigation**
- Query audit logs for all actions by compromised account
- Identify affected cases (PII exposure scope)
- Determine root cause (phishing, stolen credentials, etc.)

**Phase 4: Recovery**
- Rotate all secrets and API keys
- Patch vulnerability (if applicable)
- Re-deploy services with updated security controls

**Phase 5: Post-Mortem**
- Document timeline, impact, and remediation
- Update security controls to prevent recurrence
- Notify affected members (if PII breach)
- Regulatory reporting (if required)

**Example: Revoke Service Account Key**
```bash
# List keys
gcloud iam service-accounts keys list \
  --iam-account=tytan-api-sa@PROJECT_ID.iam.gserviceaccount.com

# Revoke key
gcloud iam service-accounts keys delete KEY_ID \
  --iam-account=tytan-api-sa@PROJECT_ID.iam.gserviceaccount.com
```

---

## Compliance Reporting

### Quarterly Audit Report

**SQL Query**:
```sql
-- Summary of all case activity last quarter
SELECT
  DATE_TRUNC(timestamp, MONTH) as month,
  event_type,
  COUNT(*) as event_count,
  COUNT(DISTINCT case_id) as unique_cases,
  COUNT(DISTINCT actor) as unique_actors
FROM `tytan_lending_ops.audit_log`
WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 3 MONTH)
GROUP BY month, event_type
ORDER BY month DESC, event_count DESC;
```

**Export to CSV**:
```bash
bq query --use_legacy_sql=false --format=csv \
  'SELECT * FROM `tytan_lending_ops.audit_log` WHERE timestamp >= "2024-01-01"' \
  > audit_report_q1_2024.csv
```

---

## Security Checklist

**Pre-Deployment**:
- [ ] All service accounts use least privilege IAM roles
- [ ] CMEK enabled for sensitive data (optional)
- [ ] Audit logging configured and exporting to GCS
- [ ] Secrets stored in Secret Manager (not hardcoded)
- [ ] Container images scanned for vulnerabilities
- [ ] Cloud Armor policies configured (if public-facing)
- [ ] Backup and disaster recovery tested

**Production Operation**:
- [ ] Monthly review of IAM permissions
- [ ] Quarterly security audit
- [ ] Annual penetration testing
- [ ] Incident response plan tested (tabletop exercise)
- [ ] Dependency updates applied within 30 days of release
- [ ] CVE alerts monitored and remediated

**Compliance**:
- [ ] 7-year retention verified (BigQuery + GCS)
- [ ] Audit logs immutable and tamper-proof
- [ ] Data access logs reviewed weekly
- [ ] Anomaly detection queries scheduled (daily)
- [ ] Regulatory exam preparation kit updated

---

## Contact

**Security Issues**: security@tytan.tech
**Compliance Questions**: compliance@tytan.tech

---

**Document Version**: 1.0
**Last Updated**: 2026-01-12
**Next Review**: 2026-04-12 (Quarterly)
