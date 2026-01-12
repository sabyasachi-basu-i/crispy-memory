# Tytan LendingOps & MemberAssist - Operations Runbook

## Overview

This runbook provides operational procedures for monitoring, troubleshooting, and maintaining the Tytan LendingOps & MemberAssist solution.

**Audience**: DevOps engineers, SREs, on-call support

---

## System Health Monitoring

### Key Metrics Dashboard

**Access**: [Cloud Console Monitoring Dashboard](https://console.cloud.google.com/monitoring/dashboards)

**Critical Metrics**:

| Metric | Threshold | Alert Level |
|--------|-----------|-------------|
| **API Error Rate** | > 5% | Critical |
| **API P95 Latency** | > 2 seconds | Warning |
| **Worker Processing Time (P95)** | > 60 seconds | Warning |
| **Pub/Sub Message Backlog** | > 100 messages | Warning |
| **Pub/Sub DLQ Message Count** | > 5 messages | Critical |
| **Document AI Quota Usage** | > 80% | Warning |
| **BigQuery Slot Utilization** | > 90% | Warning |
| **Cloud Run CPU Utilization** | > 80% | Warning |
| **Daily GCP Cost** | > $50 (dev), > $500 (prod) | Warning |

---

### Monitoring Commands

#### Check Cloud Run Service Health

```bash
# List all services and their status
gcloud run services list --region=us-central1

# Get detailed service info
gcloud run services describe tytan-lending-api --region=us-central1

# Check recent revisions
gcloud run revisions list --service=tytan-lending-api --region=us-central1

# View request metrics (last 1 hour)
gcloud monitoring time-series list \
  --filter='metric.type="run.googleapis.com/request_count" AND resource.labels.service_name="tytan-lending-api"' \
  --interval-end-time=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --interval-start-time=$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)
```

#### Check Pub/Sub Health

```bash
# View topic details
gcloud pubsub topics describe document-uploaded

# Check subscription backlog
gcloud pubsub subscriptions describe document-ai-worker-sub

# View DLQ messages
gcloud pubsub subscriptions pull document-uploaded-dlq --limit=10

# Get message count metrics
gcloud monitoring time-series list \
  --filter='metric.type="pubsub.googleapis.com/subscription/num_undelivered_messages" AND resource.labels.subscription_id="document-ai-worker-sub"'
```

#### Check BigQuery Health

```bash
# List recent queries
bq ls -j --max_results=10

# Check dataset size
bq show --format=prettyjson tytan_lending_ops

# View table row counts
bq query --use_legacy_sql=false \
  'SELECT table_name, row_count
   FROM `tytan_lending_ops.__TABLES__`'

# Check query performance (slow queries)
bq query --use_legacy_sql=false \
  'SELECT
     user_email,
     query,
     total_bytes_processed / 1e9 as gb_processed,
     TIMESTAMP_DIFF(end_time, start_time, SECOND) as duration_sec
   FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
   WHERE creation_time > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
     AND total_bytes_processed > 1e9  -- More than 1 GB
   ORDER BY duration_sec DESC
   LIMIT 10'
```

---

## Logging & Tracing

### View Logs

#### API Logs

```bash
# Recent errors (last 10 minutes)
gcloud logging read \
  'resource.type="cloud_run_revision"
   resource.labels.service_name="tytan-lending-api"
   severity>=ERROR' \
  --limit=50 \
  --format=json \
  --freshness=10m

# Trace specific case
gcloud logging read \
  'jsonPayload.case_id="CU-2024-00123"' \
  --limit=100 \
  --format=json

# Filter by HTTP status code
gcloud logging read \
  'resource.type="cloud_run_revision"
   httpRequest.status>=500' \
  --limit=20
```

#### Worker Logs

```bash
# View Document AI Worker logs
gcloud logging read \
  'resource.type="cloud_run_revision"
   resource.labels.service_name="tytan-lending-docai-worker"' \
  --limit=50 \
  --format=json

# Filter by document ID
gcloud logging read \
  'jsonPayload.document_id="doc-abc-123"' \
  --limit=50
```

#### Structured Logging Best Practices

All services log in JSON format with correlation IDs:

```python
import logging
import json

logger = logging.getLogger(__name__)

def log_event(level, message, case_id=None, document_id=None, **kwargs):
    log_entry = {
        "message": message,
        "case_id": case_id,
        "document_id": document_id,
        "service": "tytan-api",
        **kwargs
    }
    logger.log(level, json.dumps(log_entry))

# Usage
log_event(logging.INFO, "Document uploaded", case_id="CU-2024-00123", document_id="doc-abc-123", file_size=1024000)
```

**Query by correlation ID**:
```bash
gcloud logging read 'jsonPayload.case_id="CU-2024-00123"'
```

---

## Common Failure Scenarios

### Scenario 1: API Returns 500 Internal Server Error

**Symptoms**:
- Users report "Something went wrong" errors
- API error rate > 5%
- Cloud Run logs show Python exceptions

**Diagnosis**:

```bash
# Check recent errors
gcloud logging read \
  'resource.type="cloud_run_revision"
   resource.labels.service_name="tytan-lending-api"
   severity=ERROR' \
  --limit=20 \
  --format=json | jq -r '.[].jsonPayload.message'

# Check for common issues
# - Database connection errors
# - Missing environment variables
# - Permission denied errors
```

**Common Causes**:

1. **BigQuery Permission Denied**
   - Service account lacks `bigquery.dataEditor` role
   - **Fix**: Grant permissions (see `security_governance.md`)

2. **Cloud Storage Upload Fails**
   - Bucket doesn't exist or service account lacks `storage.objectCreator`
   - **Fix**: Verify bucket exists, grant permissions

3. **Out of Memory**
   - Cloud Run container OOM (Out of Memory)
   - **Fix**: Increase memory limit in Terraform

**Resolution**:

```bash
# Grant missing permissions
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:tytan-api-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

# Increase memory (if OOM)
gcloud run services update tytan-lending-api \
  --region=us-central1 \
  --memory=2Gi

# Rollback to previous revision (if recent deployment caused issue)
gcloud run services update-traffic tytan-lending-api \
  --region=us-central1 \
  --to-revisions=tytan-lending-api-00042=100
```

---

### Scenario 2: Document Processing Stuck (Pub/Sub Backlog)

**Symptoms**:
- Documents uploaded but not extracted
- Pub/Sub subscription backlog > 100 messages
- Worker logs show no recent activity

**Diagnosis**:

```bash
# Check subscription backlog
gcloud pubsub subscriptions describe document-ai-worker-sub

# Check worker logs for errors
gcloud logging read \
  'resource.type="cloud_run_revision"
   resource.labels.service_name="tytan-lending-docai-worker"
   severity=ERROR' \
  --limit=20

# Check if worker is running
gcloud run services describe tytan-lending-docai-worker --region=us-central1
```

**Common Causes**:

1. **Worker Service Crashed**
   - Python exception in startup code
   - **Fix**: Check logs, fix code, redeploy

2. **Document AI Quota Exceeded**
   - Hit API rate limit (60 pages/minute on Standard tier)
   - **Fix**: Request quota increase OR wait for quota to reset

3. **Worker Autoscaling Disabled**
   - Only 1 instance running, can't keep up with load
   - **Fix**: Increase max instances

**Resolution**:

```bash
# Increase max instances
gcloud run services update tytan-lending-docai-worker \
  --region=us-central1 \
  --max-instances=50

# Request Document AI quota increase
# Go to: https://console.cloud.google.com/apis/api/documentai.googleapis.com/quotas

# Manually trigger worker (for testing)
# Pull one message and process locally
gcloud pubsub subscriptions pull document-ai-worker-sub --limit=1 --auto-ack
```

---

### Scenario 3: High Cloud Costs

**Symptoms**:
- Daily GCP bill > expected
- Budget alert triggered

**Diagnosis**:

```bash
# View cost breakdown
gcloud billing accounts list
gcloud billing projects describe PROJECT_ID

# Check BigQuery bytes scanned (biggest cost driver)
bq query --use_legacy_sql=false \
  'SELECT
     user_email,
     SUM(total_bytes_processed) / 1e12 as TB_scanned,
     SUM(total_bytes_processed) / 1e12 * 5 as estimated_cost_usd
   FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
   WHERE creation_time > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
   GROUP BY user_email
   ORDER BY TB_scanned DESC'

# Check Document AI usage
gcloud logging read \
  'resource.type="documentai.googleapis.com/Processor"' \
  --limit=100 \
  --format=json | jq -r '.[] | .jsonPayload.pageCount' | awk '{sum+=$1} END {print "Pages processed:", sum}'

# Check Cloud Run billable time
gcloud monitoring time-series list \
  --filter='metric.type="run.googleapis.com/container/billable_instance_time"'
```

**Common Causes**:

1. **Unoptimized BigQuery Queries**
   - Scanning entire tables instead of using partition filters
   - **Fix**: Add `WHERE DATE(created_at) = '2024-01-15'` to queries

2. **Cloud Run Min Instances Too High**
   - Paying for idle instances
   - **Fix**: Set `min_instances = 0` for non-prod environments

3. **GCS Lifecycle Policy Not Applied**
   - All documents in Standard storage
   - **Fix**: Verify lifecycle policy moves old docs to Coldline

**Resolution**:

```bash
# Optimize BigQuery query
# BAD:
bq query 'SELECT * FROM tytan_lending_ops.cases'

# GOOD:
bq query 'SELECT * FROM tytan_lending_ops.cases WHERE DATE(created_at) = "2024-01-15"'

# Reduce Cloud Run min instances
gcloud run services update tytan-lending-api \
  --region=us-central1 \
  --min-instances=0

# Verify GCS lifecycle policy
gsutil lifecycle get gs://tytan-lending-docs-dev
```

---

### Scenario 4: Dialogflow Webhook Timeout

**Symptoms**:
- Chatbot returns "I'm having trouble connecting. Please try again."
- Webhook logs show timeouts or slow queries

**Diagnosis**:

```bash
# Check webhook logs
gcloud logging read \
  'resource.type="cloud_run_revision"
   resource.labels.service_name="tytan-lending-webhook"' \
  --limit=20

# Check BigQuery query performance
bq query --use_legacy_sql=false \
  'SELECT query, total_slot_ms, total_bytes_processed
   FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
   WHERE user_email = "tytan-webhook-sa@PROJECT_ID.iam.gserviceaccount.com"
     AND creation_time > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
   ORDER BY total_slot_ms DESC
   LIMIT 10'
```

**Common Causes**:

1. **Slow BigQuery Query**
   - Query scans too much data
   - **Fix**: Add indexes (clustering) or use materialized view

2. **Webhook Timeout Too Short**
   - Dialogflow times out after 5 seconds
   - **Fix**: Optimize query OR return "Still processing..." response quickly

**Resolution**:

```python
# In webhook code, set timeout for BigQuery
from google.cloud import bigquery

client = bigquery.Client()

query_job = client.query(
    query_string,
    job_config=bigquery.QueryJobConfig(
        use_query_cache=True,
        use_legacy_sql=False
    ),
    timeout=3.0  # Fail fast if query takes > 3 seconds
)

try:
    results = list(query_job.result(timeout=3.0))
except TimeoutError:
    return {
        "fulfillmentResponse": {
            "messages": [{
                "text": {"text": ["Your case is still being processed. Please check back in a few minutes."]}
            }]
        }
    }
```

---

## Retry & Replay Strategies

### Pub/Sub Message Retry

**Configuration**:
- Max retries: 5
- Exponential backoff: 10s, 20s, 40s, 80s, 160s
- After 5 failures → send to DLQ

**Manual Replay from DLQ**:

```bash
# Pull failed messages
gcloud pubsub subscriptions pull document-uploaded-dlq --limit=10 --format=json > failed_messages.json

# Inspect message
cat failed_messages.json | jq '.[0].message.data' | base64 -d

# Fix underlying issue (e.g., grant permissions, deploy fix)

# Re-publish message to original topic
gcloud pubsub topics publish document-uploaded \
  --message='{"case_id":"CU-2024-00123","document_id":"doc-abc-123",...}'

# Acknowledge DLQ message (to remove it)
gcloud pubsub subscriptions ack document-uploaded-dlq \
  --ack-ids=$(cat failed_messages.json | jq -r '.[0].ackId')
```

**Automated Replay Script** (`scripts/replay_dlq.py`):

```python
#!/usr/bin/env python3
from google.cloud import pubsub_v1

project_id = "your-project-id"
dlq_subscription = "document-uploaded-dlq"
target_topic = "document-uploaded"

subscriber = pubsub_v1.SubscriberClient()
publisher = pubsub_v1.PublisherClient()

dlq_path = subscriber.subscription_path(project_id, dlq_subscription)
topic_path = publisher.topic_path(project_id, target_topic)

response = subscriber.pull(request={"subscription": dlq_path, "max_messages": 100})

for msg in response.received_messages:
    # Re-publish to original topic
    publisher.publish(topic_path, msg.message.data)
    # Ack DLQ message
    subscriber.acknowledge(request={"subscription": dlq_path, "ack_ids": [msg.ack_id]})
    print(f"Replayed message: {msg.message.message_id}")

print(f"Replayed {len(response.received_messages)} messages")
```

---

### Idempotency Checks

All operations are idempotent (safe to retry):

**Document Upload**:
- Deduplication key: SHA-256 hash of file content
- If same file uploaded twice → returns existing `document_id`

**Extraction**:
- Check if `extracted_fields` already exists for `(case_id, document_id)`
- If exists → skip re-extraction, ACK message

**Case Creation**:
- Use client-provided `idempotency_key` (optional)
- If key already exists → return existing `case_id`

---

## Disaster Recovery Procedures

### Scenario: BigQuery Dataset Accidentally Deleted

**Recovery Steps**:

1. **Restore from Time Travel** (if < 7 days ago):
   ```sql
   -- Restore table from 2 days ago
   CREATE TABLE `tytan_lending_ops.cases_restored`
   AS SELECT * FROM `tytan_lending_ops.cases`
   FOR SYSTEM_TIME AS OF TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY);

   -- Rename back
   ALTER TABLE `tytan_lending_ops.cases_restored` RENAME TO cases;
   ```

2. **Restore from Snapshot** (if > 7 days ago):
   ```bash
   # Restore from daily snapshot dataset
   bq cp tytan_lending_ops_backup_20240115.cases tytan_lending_ops.cases
   ```

3. **Verify Data Integrity**:
   ```sql
   SELECT COUNT(*) FROM `tytan_lending_ops.cases`;
   SELECT MAX(created_at) FROM `tytan_lending_ops.cases`;  -- Should be recent
   ```

---

### Scenario: Cloud Storage Bucket Deleted

**Prevention**:
- Retention policy on bucket (cannot delete until retention period expires)
- Object versioning enabled (restore previous versions)

**Recovery**:

```bash
# List deleted objects (if versioning enabled)
gsutil ls -a gs://tytan-lending-docs-dev/cases/

# Restore specific file version
gsutil cp gs://tytan-lending-docs-dev/cases/CU-2024-00123/doc-abc-123.pdf#1234567890 \
  gs://tytan-lending-docs-dev/cases/CU-2024-00123/doc-abc-123.pdf

# Restore all files (if bucket deleted)
# Must restore from cross-region replica or backup
gsutil -m cp -r gs://tytan-lending-docs-backup/* gs://tytan-lending-docs-dev/
```

---

### Scenario: Region Outage (us-central1)

**Mitigation**:
- Deploy multi-region for production (us-central1 + us-east1)
- Use global load balancer to route traffic

**Failover Steps**:

1. **Deploy to Secondary Region**:
   ```bash
   cd infra
   terraform apply -var="region=us-east1" -var="failover_mode=true"
   ```

2. **Update DNS** (if using custom domain):
   ```bash
   # Point to us-east1 Cloud Run service
   gcloud run services describe tytan-lending-api --region=us-east1 --format='value(status.url)'
   ```

3. **Verify Functionality**:
   ```bash
   curl https://tytan-api-xyz-ue.a.run.app/health
   ```

4. **Switch Back** (when primary region recovers):
   ```bash
   # Gradually shift traffic back to us-central1
   gcloud run services update-traffic tytan-lending-api \
     --region=us-central1 \
     --to-revisions=LATEST=100
   ```

---

## Performance Tuning

### BigQuery Optimization

**Partition and Cluster Tables**:
```sql
-- Add partitioning to existing table
ALTER TABLE `tytan_lending_ops.cases`
SET OPTIONS (
  partition_expiration_days=2555,  -- 7 years
  require_partition_filter=true    -- Force queries to specify date
);

-- Add clustering
ALTER TABLE `tytan_lending_ops.cases`
CLUSTER BY status, loan_type;
```

**Use Materialized Views for Dashboard Queries**:
```sql
-- Pre-aggregate case summaries
CREATE MATERIALIZED VIEW `tytan_lending_ops.case_summary_mv`
AS
SELECT
  DATE(created_at) as date,
  status,
  loan_type,
  COUNT(*) as case_count,
  AVG(loan_amount) as avg_loan_amount
FROM `tytan_lending_ops.cases`
GROUP BY date, status, loan_type;

-- Refresh every 15 minutes
ALTER MATERIALIZED VIEW `tytan_lending_ops.case_summary_mv`
SET OPTIONS (enable_refresh=true, refresh_interval_minutes=15);
```

---

### Cloud Run Autoscaling

**Tune Concurrency**:
```bash
# Allow up to 100 concurrent requests per instance
gcloud run services update tytan-lending-api \
  --region=us-central1 \
  --concurrency=100

# For CPU-intensive workloads (Document AI worker), use lower concurrency
gcloud run services update tytan-lending-docai-worker \
  --region=us-central1 \
  --concurrency=1
```

**Tune Min/Max Instances**:
```bash
# Production: always keep 2 instances warm
gcloud run services update tytan-lending-api \
  --region=us-central1 \
  --min-instances=2 \
  --max-instances=100

# Dev: scale to zero
gcloud run services update tytan-lending-api \
  --region=us-central1 \
  --min-instances=0 \
  --max-instances=10
```

---

## Scheduled Maintenance

### Weekly Tasks

**1. Review DLQ Messages** (every Monday):
```bash
gcloud pubsub subscriptions pull document-uploaded-dlq --limit=100
# Investigate root cause, replay messages
```

**2. Check for Slow Queries**:
```sql
-- Find queries scanning > 1 GB
SELECT
  user_email,
  query,
  total_bytes_processed / 1e9 as gb_scanned,
  TIMESTAMP_DIFF(end_time, start_time, SECOND) as duration_sec
FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
WHERE creation_time > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
  AND total_bytes_processed > 1e9
ORDER BY gb_scanned DESC
LIMIT 20;
```

**3. Update Dependencies**:
```bash
cd services/cloud-run-api
pip list --outdated
# Review for security patches, update requirements.txt
pip install -r requirements.txt
pytest  # Run tests
# Deploy
```

---

### Monthly Tasks

**1. Rotate Service Account Keys** (if using keys instead of Workload Identity):
```bash
# Create new key
gcloud iam service-accounts keys create new-key.json \
  --iam-account=tytan-api-sa@PROJECT_ID.iam.gserviceaccount.com

# Update Secret Manager
gcloud secrets versions add api-service-account-key --data-file=new-key.json

# Delete old key (after verifying new key works)
gcloud iam service-accounts keys delete OLD_KEY_ID \
  --iam-account=tytan-api-sa@PROJECT_ID.iam.gserviceaccount.com
```

**2. Review IAM Permissions**:
```bash
# List all service account permissions
gcloud projects get-iam-policy PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount" \
  --format="table(bindings.role, bindings.members)"

# Audit for overly permissive roles (e.g., Owner, Editor)
```

**3. Cost Optimization Review**:
- Review cost breakdown
- Archive old documents to Coldline storage
- Delete old BigQuery partitions (if retention expired)

---

### Quarterly Tasks

**1. Security Audit**:
- Review audit logs for anomalies
- Run vulnerability scan on container images
- Update security documentation

**2. Disaster Recovery Drill**:
- Simulate BigQuery dataset deletion → restore from snapshot
- Simulate region outage → failover to secondary region
- Document lessons learned

**3. Performance Benchmarking**:
- Load test API with 1000 concurrent requests
- Measure Document AI processing throughput
- Optimize bottlenecks

---

## On-Call Rotation

### Escalation Path

**Level 1 (DevOps Engineer)**:
- Monitor alerts, acknowledge incidents
- Handle common issues (e.g., permission errors, DLQ replay)
- Escalate if unresolved after 30 minutes

**Level 2 (Senior SRE)**:
- Complex troubleshooting (e.g., performance degradation, quota issues)
- Coordinate with GCP support
- Escalate if unresolved after 1 hour

**Level 3 (Engineering Lead)**:
- Critical production outage
- Data loss or corruption
- Security incident

### On-Call Checklist

**When Alert Fires**:
1. ✅ Acknowledge alert in PagerDuty/Slack
2. ✅ Check monitoring dashboard for context
3. ✅ Review recent deployments (rollback if needed)
4. ✅ Check logs for error messages
5. ✅ Document troubleshooting steps in incident ticket
6. ✅ Resolve issue OR escalate
7. ✅ Post-mortem (if critical incident)

---

## Useful Commands Reference

### Quick Diagnostics

```bash
# API health
curl https://tytan-api-xyz-uc.a.run.app/health

# Recent errors (last 10 min)
gcloud logging read 'severity>=ERROR' --limit=20 --freshness=10m

# Pub/Sub backlog
gcloud pubsub subscriptions describe document-ai-worker-sub | grep numUndeliveredMessages

# BigQuery row counts
bq query 'SELECT table_name, row_count FROM `tytan_lending_ops.__TABLES__`'

# Cloud Run CPU/Memory
gcloud run services describe tytan-lending-api --region=us-central1 | grep -A5 resources

# Quota usage
gcloud compute project-info describe --project=PROJECT_ID
```

---

## Contact

**On-Call**: oncall@tytan.tech
**Slack**: #tytan-lending-ops
**PagerDuty**: https://tytan.pagerduty.com

---

**Document Version**: 1.0
**Last Updated**: 2026-01-12
**Next Review**: 2026-02-12 (Monthly)
