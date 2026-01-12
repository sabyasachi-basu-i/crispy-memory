# Tytan LendingOps & MemberAssist - Technical Architecture

## Architecture Overview

This document describes the technical architecture of the Tytan LendingOps & MemberAssist solution, built on Google Cloud Platform and Google Workspace.

### Design Principles

1. **Event-Driven**: Pub/Sub decouples services for scalability and resilience
2. **Serverless-First**: Cloud Run eliminates infrastructure management, autoscales automatically
3. **Idempotent Processing**: Safe to retry any operation without side effects
4. **Observability**: Structured logging with correlation IDs for end-to-end tracing
5. **Cost-Conscious**: Pay-per-use pricing, BigQuery partitioning, GCS lifecycle policies
6. **Security-First**: Least privilege IAM, service account per component, audit logging
7. **API-First**: All functionality exposed via REST APIs for integration

---

## High-Level Architecture

See `architecture/high_level_architecture.mermaid` for visual diagram.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         INTAKE CHANNELS                              │
│  Web Form  │  Gmail/Drive  │  Partner API  │  Mobile Upload          │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      CLOUD RUN API (REST)                            │
│  POST /cases  │  POST /cases/{id}/documents  │  GET /cases/{id}     │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
           ┌──────────────────┐     ┌──────────────────┐
           │  Cloud Storage   │     │    BigQuery      │
           │  (Documents)     │     │  (Case Records)  │
           └──────────────────┘     └──────────────────┘
                    │
                    │ (triggers)
                    ▼
           ┌──────────────────┐
           │  Pub/Sub Topic   │
           │ document.uploaded│
           └──────────────────┘
                    │
                    │ (pull subscription)
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│              DOCUMENT AI WORKER (Cloud Run)                          │
│  • Consume Pub/Sub events                                            │
│  • Call Document AI processors                                       │
│  • Write extracted fields to BigQuery                                │
│  • Update case status based on confidence                            │
└─────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
           ┌──────────────────┐
           │    BigQuery      │
           │ extracted_fields │
           └──────────────────┘
                    │
                    │ (queries)
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│            DIALOGFLOW CX + WEBHOOK (Cloud Run)                       │
│  • Member queries: "What's my status?"                               │
│  • Webhook fetches case details from BigQuery                        │
│  • Returns structured response with missing docs checklist           │
│  • Escalates to human agent if needed                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Cloud Run API (`services/cloud-run-api`)

**Purpose**: Central API for case and document management

**Technology**: Python 3.11, Flask, gunicorn

**Endpoints**:

#### `POST /cases`
Create a new loan application case.

**Request**:
```json
{
  "member_id": "M-12345",
  "loan_type": "auto",
  "loan_amount": 25000,
  "member_contact": {
    "email": "sarah@example.com",
    "phone": "+15551234567"
  },
  "metadata": {
    "source": "web_form",
    "user_agent": "Mozilla/5.0..."
  }
}
```

**Response**:
```json
{
  "case_id": "CU-2024-00123",
  "status": "SUBMITTED",
  "created_at": "2024-01-15T10:30:00Z",
  "required_documents": [
    "drivers_license",
    "paystub_recent_2",
    "bank_statement_30days"
  ]
}
```

**Actions**:
1. Generate unique case ID (format: `{prefix}-{YYYY}-{sequence}`)
2. Insert row into BigQuery `cases` table
3. Log event to Cloud Logging with correlation ID
4. Return case metadata

---

#### `POST /cases/{case_id}/documents`
Upload document or link to existing GCS object.

**Request** (multipart/form-data):
```
Content-Type: multipart/form-data
file: <binary>
document_type: "drivers_license"
```

OR (JSON, referencing existing GCS object):
```json
{
  "gcs_uri": "gs://tytan-lending-docs/incoming/doc-xyz.pdf",
  "document_type": "paystub"
}
```

**Response**:
```json
{
  "document_id": "doc-abc-123",
  "gcs_uri": "gs://tytan-lending-docs/cases/CU-2024-00123/doc-abc-123.pdf",
  "upload_status": "success",
  "pubsub_message_id": "1234567890"
}
```

**Actions**:
1. Validate case exists
2. Upload file to GCS: `{bucket}/cases/{case_id}/{document_id}.{ext}`
3. Write document metadata to BigQuery `documents` table
4. Publish message to Pub/Sub `document.uploaded` topic:
   ```json
   {
     "case_id": "CU-2024-00123",
     "document_id": "doc-abc-123",
     "gcs_uri": "gs://.../doc-abc-123.pdf",
     "document_type": "drivers_license",
     "timestamp": "2024-01-15T10:35:00Z"
   }
   ```
5. Return document metadata

**Idempotency**: Uses document hash as deduplication key. If same file uploaded twice, returns existing document_id.

---

#### `GET /cases/{case_id}`
Retrieve case status, document checklist, extracted fields summary.

**Response**:
```json
{
  "case_id": "CU-2024-00123",
  "status": "NEEDS_REVIEW",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:45:00Z",
  "loan_type": "auto",
  "loan_amount": 25000,
  "documents": [
    {
      "document_id": "doc-abc-123",
      "document_type": "drivers_license",
      "status": "EXTRACTED",
      "uploaded_at": "2024-01-15T10:35:00Z",
      "extraction_summary": {
        "fields_extracted": 8,
        "avg_confidence": 0.96,
        "needs_review": false
      }
    },
    {
      "document_id": "doc-def-456",
      "document_type": "paystub",
      "status": "NEEDS_REVIEW",
      "uploaded_at": "2024-01-15T10:40:00Z",
      "extraction_summary": {
        "fields_extracted": 12,
        "avg_confidence": 0.78,
        "needs_review": true,
        "low_confidence_fields": ["employer_ein", "net_pay"]
      }
    }
  ],
  "missing_documents": ["bank_statement_30days"],
  "extracted_applicant": {
    "full_name": "Sarah Johnson",
    "date_of_birth": "1989-05-12",
    "annual_income": 65000
  }
}
```

**Actions**:
1. Query BigQuery `cases` table
2. Join with `documents` and `extracted_fields` tables
3. Aggregate extraction summaries
4. Return enriched case view

---

#### `POST /cases/{case_id}/review`
Human approval or correction of extracted fields.

**Request**:
```json
{
  "reviewer_id": "marcus@creditunion.com",
  "document_id": "doc-def-456",
  "field_corrections": [
    {
      "field_name": "employer_ein",
      "extracted_value": "12-345678",
      "corrected_value": "12-3456789",
      "reason": "OCR missed last digit"
    }
  ],
  "approval_status": "APPROVED"
}
```

**Response**:
```json
{
  "review_id": "rev-789",
  "case_id": "CU-2024-00123",
  "status": "READY_FOR_DECISION",
  "corrections_applied": 1
}
```

**Actions**:
1. Update `extracted_fields` with corrected values
2. Insert audit record into `field_corrections` table
3. Update case status to `READY_FOR_DECISION`
4. (Optional) Trigger downstream LOS integration event

---

### 2. Document AI Worker (`pipelines/document_ai_worker`)

**Purpose**: Consume document upload events, extract structured data using Document AI

**Technology**: Python 3.11, google-cloud-documentai, google-cloud-pubsub

**Trigger**: Pub/Sub message on `document.uploaded` topic

**Processing Flow**:

1. **Receive Message**
   - Pull message from Pub/Sub subscription
   - Parse JSON payload
   - Extract `case_id`, `document_id`, `gcs_uri`, `document_type`

2. **Route to Processor**
   - Map `document_type` to Document AI processor:
     - `drivers_license` → Identity Processor (`projects/{project}/locations/{location}/processors/{id}`)
     - `paystub` → Form Parser (custom trained)
     - `bank_statement` → Document OCR + custom parser
     - `w2` → W2 Processor
     - Unknown → Generic Form Parser

3. **Call Document AI**
   ```python
   from google.cloud import documentai_v1 as documentai

   # Real mode
   client = documentai.DocumentProcessorServiceClient()
   request = documentai.ProcessRequest(
       name=processor_name,
       raw_document=documentai.RawDocument(
           content=gcs_file_bytes,
           mime_type="application/pdf"
       )
   )
   result = client.process_document(request=request)

   # Mock mode (for local dev)
   if MOCK_MODE:
       result = load_mock_response(document_type)
   ```

4. **Parse Extraction Results**
   - For each entity/field in `result.document.entities`:
     ```python
     {
       "field_name": entity.type_,
       "value": entity.mention_text,
       "confidence": entity.confidence,
       "page_anchor": entity.page_anchor.page_refs[0].page
     }
     ```

5. **Write to BigQuery**
   - Insert into `extracted_fields` table:
     ```sql
     INSERT INTO extracted_fields (
       case_id, document_id, field_name, value,
       confidence, extracted_at, processor_id
     ) VALUES (...)
     ```

6. **Evaluate Confidence Threshold**
   - Calculate average confidence across all fields
   - If `avg_confidence < 0.85` OR any critical field < 0.80:
     - Update case status → `NEEDS_REVIEW`
     - Flag document for human review
   - Else:
     - Update case status → `READY_FOR_DECISION`

7. **Acknowledge Pub/Sub Message**
   - Only ACK after successful BigQuery write
   - If processing fails, NACK message → retry (max 5 attempts)
   - If max retries exceeded, move to dead-letter topic

**Error Handling**:
- **Transient Errors** (API timeout, rate limit): NACK message, exponential backoff
- **Permanent Errors** (invalid PDF, unsupported format): ACK message, log error, mark document status as `FAILED`
- **Document AI Quota Exceeded**: Pause processing, send alert, resume when quota refreshes

**Idempotency**:
- Check if `extracted_fields` already exists for `(case_id, document_id)`
- If exists: skip extraction, ACK message
- Deduplication prevents double-processing on retries

---

### 3. Dialogflow Webhook (`services/dialogflow-webhook`)

**Purpose**: Backend for Dialogflow CX agent to answer member queries

**Technology**: Python 3.11, Flask, google-cloud-bigquery

**Dialogflow CX Agent Structure**:

**Intents**:
- `check_application_status`
- `missing_documents`
- `timeline_inquiry`
- `escalate_to_human`

**Entities**:
- `@case_id`: Regex pattern `CU-\d{4}-\d{5}`
- `@member_last_name`: System entity

**Webhook Endpoint**: `POST /dialogflow-webhook`

**Request** (from Dialogflow CX):
```json
{
  "detectIntentResponseId": "abc-123",
  "intentInfo": {
    "displayName": "check_application_status"
  },
  "sessionInfo": {
    "session": "projects/.../sessions/xyz",
    "parameters": {
      "case_id": "CU-2024-00123"
    }
  },
  "fulfillmentInfo": {
    "tag": "get_case_status"
  }
}
```

**Webhook Processing**:

1. **Extract Parameters**
   ```python
   case_id = request["sessionInfo"]["parameters"].get("case_id")
   tag = request["fulfillmentInfo"]["tag"]
   ```

2. **Query BigQuery**
   ```sql
   SELECT
     c.case_id,
     c.status,
     c.created_at,
     ARRAY_AGG(
       STRUCT(d.document_type, d.status)
     ) as documents,
     (
       SELECT ARRAY_AGG(rd.document_type)
       FROM required_documents rd
       WHERE rd.case_id = c.case_id
         AND rd.document_type NOT IN (
           SELECT document_type FROM documents WHERE case_id = c.case_id
         )
     ) as missing_documents
   FROM cases c
   LEFT JOIN documents d ON c.case_id = d.case_id
   WHERE c.case_id = @case_id
   GROUP BY c.case_id, c.status, c.created_at
   ```

3. **Build Response**
   ```python
   if tag == "get_case_status":
       status_text = format_status_message(case_data)
       return {
           "fulfillmentResponse": {
               "messages": [
                   {
                       "text": {
                           "text": [status_text]
                       }
                   }
               ]
           },
           "sessionInfo": {
               "parameters": {
                   "case_status": case_data["status"],
                   "has_missing_docs": len(case_data["missing_documents"]) > 0
               }
           }
       }
   ```

**Response** (to Dialogflow CX):
```json
{
  "fulfillmentResponse": {
    "messages": [
      {
        "text": {
          "text": ["Hi Sarah! Your application CU-2024-00123 is currently under review. We've received your driver's license and paystubs. We're still waiting for your bank statement. Estimated completion: tomorrow by 3 PM."]
        }
      }
    ]
  },
  "sessionInfo": {
    "parameters": {
      "case_status": "NEEDS_REVIEW",
      "has_missing_docs": true,
      "missing_doc_list": ["bank_statement_30days"]
    }
  }
}
```

**Webhook Tags**:
- `get_case_status`: Return current status + document checklist
- `get_timeline`: Estimate completion based on SLA + current queue depth
- `escalate`: Create callback ticket, return confirmation
- `retry_extraction`: Trigger manual re-extraction (admin only)

---

### 4. BigQuery Data Model

**Dataset**: `tytan_lending_ops`

**Tables**:

#### `cases`
Primary case record.

| Column | Type | Description |
|--------|------|-------------|
| case_id | STRING | Primary key (format: CU-YYYY-NNNNN) |
| member_id | STRING | Reference to member/customer ID |
| loan_type | STRING | auto, personal, mortgage, etc. |
| loan_amount | NUMERIC | Requested loan amount |
| status | STRING | SUBMITTED, EXTRACTING, NEEDS_REVIEW, READY_FOR_DECISION, APPROVED, REJECTED |
| created_at | TIMESTAMP | Case creation time |
| updated_at | TIMESTAMP | Last status change |
| member_contact_email | STRING | For notifications |
| member_contact_phone | STRING | For escalations |
| source_channel | STRING | web_form, gmail, api, mobile |
| metadata | JSON | Flexible field for custom attributes |

**Partition**: `DATE(created_at)` (for cost-efficient querying)
**Clustering**: `status`, `loan_type`

---

#### `documents`
Document upload tracking.

| Column | Type | Description |
|--------|------|-------------|
| document_id | STRING | Primary key |
| case_id | STRING | Foreign key to cases |
| document_type | STRING | drivers_license, paystub, bank_statement, w2, etc. |
| gcs_uri | STRING | gs://bucket/path/to/doc.pdf |
| file_size_bytes | INT64 | For cost tracking |
| mime_type | STRING | application/pdf, image/jpeg, etc. |
| uploaded_at | TIMESTAMP | Upload time |
| status | STRING | UPLOADED, EXTRACTING, EXTRACTED, FAILED, NEEDS_REVIEW |
| file_hash_sha256 | STRING | For deduplication |

**Partition**: `DATE(uploaded_at)`
**Clustering**: `case_id`, `document_type`

---

#### `extracted_fields`
Individual field extractions from Document AI.

| Column | Type | Description |
|--------|------|-------------|
| extraction_id | STRING | Primary key |
| case_id | STRING | Foreign key |
| document_id | STRING | Foreign key |
| field_name | STRING | full_name, date_of_birth, employer_ein, etc. |
| value | STRING | Extracted value (stored as string, cast as needed) |
| confidence | FLOAT64 | 0.0 - 1.0 |
| page_number | INT64 | Page where field was found |
| bounding_box | JSON | Coordinates (for UI highlighting) |
| extracted_at | TIMESTAMP | Extraction time |
| processor_id | STRING | Document AI processor version |
| is_corrected | BOOLEAN | Whether human corrected this field |

**Partition**: `DATE(extracted_at)`
**Clustering**: `case_id`, `field_name`

---

#### `field_corrections`
Audit log of human corrections.

| Column | Type | Description |
|--------|------|-------------|
| correction_id | STRING | Primary key |
| extraction_id | STRING | Foreign key to extracted_fields |
| case_id | STRING | Foreign key |
| document_id | STRING | Foreign key |
| field_name | STRING | Which field was corrected |
| original_value | STRING | Value from Document AI |
| corrected_value | STRING | Human-provided value |
| reviewer_id | STRING | email of reviewer |
| review_timestamp | TIMESTAMP | When correction was made |
| correction_reason | STRING | Free-text explanation |

**Partition**: `DATE(review_timestamp)`

---

#### `audit_log`
Immutable event log for compliance.

| Column | Type | Description |
|--------|------|-------------|
| event_id | STRING | Primary key (UUID) |
| case_id | STRING | Foreign key |
| event_type | STRING | CASE_CREATED, DOCUMENT_UPLOADED, EXTRACTION_COMPLETED, STATUS_CHANGED, etc. |
| actor | STRING | Service account or user email |
| timestamp | TIMESTAMP | Event time |
| payload | JSON | Full event details |
| ip_address | STRING | For security audits |
| user_agent | STRING | For security audits |

**Partition**: `DATE(timestamp)`
**Clustering**: `case_id`, `event_type`

**Retention**: 7 years (regulatory requirement for consumer loans)

---

### 5. Cloud Storage Buckets

#### `{project}-lending-documents`

**Lifecycle Policies**:
- Documents older than 90 days → move to Nearline storage
- Documents older than 1 year → move to Coldline storage
- Documents older than 7 years → delete (after audit log retention verified)

**Structure**:
```
gs://{project}-lending-documents/
  cases/
    CU-2024-00123/
      doc-abc-123.pdf         (drivers license)
      doc-def-456.pdf         (paystub)
  incoming/                   (temp staging for bulk imports)
  rejected/                   (failed documents for manual review)
```

**Access Control**:
- Cloud Run API service account: `objectCreator`, `objectViewer`
- Document AI Worker service account: `objectViewer`
- Human reviewers: `objectViewer` (via signed URLs, not direct access)

---

### 6. Pub/Sub Topics & Subscriptions

#### Topic: `document.uploaded`

**Publisher**: Cloud Run API

**Message Schema**:
```json
{
  "case_id": "CU-2024-00123",
  "document_id": "doc-abc-123",
  "gcs_uri": "gs://bucket/cases/CU-2024-00123/doc-abc-123.pdf",
  "document_type": "drivers_license",
  "timestamp": "2024-01-15T10:35:00Z",
  "correlation_id": "req-xyz-789"
}
```

**Subscription**: `document-ai-worker-sub`
- Pull subscription
- Ack deadline: 600 seconds (10 minutes, to allow for Document AI processing)
- Max retry: 5 attempts
- Dead-letter topic: `document.uploaded.dlq`

---

#### Topic: `extraction.completed`

**Publisher**: Document AI Worker

**Message Schema**:
```json
{
  "case_id": "CU-2024-00123",
  "document_id": "doc-abc-123",
  "extraction_status": "success",
  "fields_extracted": 8,
  "avg_confidence": 0.96,
  "needs_review": false,
  "timestamp": "2024-01-15T10:36:30Z"
}
```

**Subscription**: `case-status-updater-sub` (future: triggers LOS integration)

---

#### Topic: `document.uploaded.dlq`

**Purpose**: Dead-letter queue for failed processing

**Monitoring**: Alert if > 5 messages in DLQ

**Replay Process**:
1. Human investigates failure reason
2. Fixes issue (e.g., re-uploads clearer scan)
3. Re-publishes message to `document.uploaded` (with deduplication check)

---

## Event Flow Diagrams

See `architecture/event_flow_sequence.mermaid` for detailed sequence diagram.

**End-to-End Flow**:

```
Member         Web UI         API          GCS      Pub/Sub      Worker      BigQuery     Dialogflow
  │              │             │            │          │           │            │             │
  │─Submit Form─>│             │            │          │           │            │             │
  │              │─POST /cases─>│           │          │           │            │             │
  │              │             │──INSERT──────────────────────────────────────>│             │
  │              │<─Case ID────│            │          │           │            │             │
  │<─Confirmation│             │            │          │           │            │             │
  │              │             │            │          │           │            │             │
  │─Upload Doc──>│             │            │          │           │            │             │
  │              │─POST .../documents──────>│          │           │            │             │
  │              │             │──PUBLISH────────────>│           │            │             │
  │              │             │            │          │           │            │             │
  │              │             │            │          │─pull msg─>│            │             │
  │              │             │            │          │           │─Call DocAI │             │
  │              │             │            │          │           │<─Results───│             │
  │              │             │            │          │           │──INSERT─────────────────>│
  │              │             │            │          │<─ACK msg──│            │             │
  │              │             │            │          │           │            │             │
  │─"Status?"──────────────────────────────────────────────────────────────────────────────>│
  │              │             │            │          │           │            │             │
  │              │             │            │          │           │            │<─Query──────│
  │<─"Under review, waiting for bank statement"────────────────────────────────────────────│
```

---

## Integration Points

### 1. Google Workspace Integration (Optional)

**Gmail Intake**:
- Use Gmail API to monitor specific mailbox (e.g., `loans@creditunion.com`)
- Filter emails with subject matching pattern (e.g., "Loan Application")
- Extract attachments, create case via API
- Implementation: Separate Cloud Run service triggered by Cloud Scheduler (every 5 minutes)

**Drive Upload**:
- Shared Drive folder per case: `Loan Applications/{case_id}/`
- Drive API watches folder for new files
- On file upload, trigger Cloud Function → POST to `/cases/{id}/documents`

---

### 2. LOS (Loan Origination System) Integration

**Export Extracted Data**:
- Endpoint: `GET /cases/{case_id}/export/los`
- Returns structured XML or JSON compatible with LOS import
- Alternative: Direct database write or API call (custom per LOS vendor)

**Status Callback**:
- LOS sends webhook to API when decision made
- Update case status to `APPROVED` or `REJECTED`
- Trigger email notification to member

---

### 3. Core Banking System Integration

**Member Lookup**:
- API calls core banking API to validate `member_id`
- Retrieve member profile (name, address, existing accounts)
- Pre-fill application data

---

## Deployment Architecture

**Environments**:

| Environment | Purpose | GCP Project |
|-------------|---------|-------------|
| **dev** | Developer testing, mock mode enabled | `tytan-lending-dev` |
| **staging** | Pre-production, real Document AI, limited quota | `tytan-lending-staging` |
| **prod** | Production, full monitoring, backup policies | `tytan-lending-prod` |

**CI/CD Pipeline** (Cloud Build):
1. Developer pushes code to GitHub
2. Cloud Build triggers:
   - Run unit tests
   - Build Docker images
   - Push to Artifact Registry
3. Deploy to `dev` automatically
4. Manual approval gate for `staging` and `prod`

---

## Scalability & Performance

### Cloud Run Autoscaling

**API Service**:
- Min instances: 1 (to reduce cold starts)
- Max instances: 100
- Concurrency: 80 requests per instance
- Expected load: 10-50 RPS (requests per second) during business hours
- Cold start latency: < 2 seconds

**Document AI Worker**:
- Min instances: 0 (cost savings, OK with some latency)
- Max instances: 50
- Concurrency: 1 (process one document at a time per instance)
- Expected load: 100-500 documents/hour

---

### BigQuery Performance

**Partitioning**:
- All tables partitioned by date field (created_at, uploaded_at, etc.)
- Queries filter on partition column → only scan relevant partitions
- Cost savings: 95%+ for date-range queries

**Clustering**:
- Cluster on high-cardinality columns (case_id, status)
- Queries filter on clustered columns → skip irrelevant data blocks
- Cost savings: 30-50% on top of partitioning

**Materialized Views** (optional):
- Pre-aggregate case summaries for dashboard queries
- Refresh every 15 minutes
- Reduces query cost and latency for reporting

---

### Document AI Quotas

**Limits**:
- Standard tier: 60 pages/minute, 15 requests/minute
- Advanced tier: 600 pages/minute, 600 requests/minute

**Strategy**:
- Start with Standard tier (sufficient for < 1,000 cases/month)
- Monitor quota usage in Cloud Monitoring
- Request quota increase or upgrade tier as volume grows
- Implement queuing: if quota exceeded, delay processing until next minute

---

## Disaster Recovery

### Backup Strategy

**BigQuery**:
- Automatic 7-day time-travel (restore to any point in last 7 days)
- Daily snapshots to separate dataset (retained 90 days)
- Cross-region replication for `prod` environment

**Cloud Storage**:
- Versioning enabled (retain 30 previous versions)
- Cross-region replication for compliance-critical documents
- Lifecycle policy: move to Archive storage after 7 years, delete after 10

**Pub/Sub**:
- Messages retained 7 days (configurable)
- Dead-letter topic for failed processing
- Manual replay capability via Python script

---

### RTO/RPO Targets

| Component | RTO (Recovery Time) | RPO (Recovery Point) |
|-----------|---------------------|----------------------|
| Cloud Run API | 15 minutes | 0 (stateless) |
| BigQuery | 1 hour | 1 hour (snapshot restore) |
| Cloud Storage | 30 minutes | 0 (replicated) |
| Pub/Sub | 5 minutes | 7 days (message retention) |

---

## Security Architecture

See `security_governance.md` for detailed security controls.

**Key Principles**:
1. **Service Account per Component**: API, Worker, Webhook each have dedicated service accounts
2. **Least Privilege IAM**: Only grant minimum permissions needed
3. **No User Credentials**: All authentication via Workload Identity
4. **Audit Logging**: All API calls, BigQuery queries, GCS access logged
5. **Data Encryption**: At-rest (default GCP KMS), in-transit (TLS 1.3)

---

## Cost Estimation

**Assumptions**:
- 1,000 cases/month
- 3 documents per case (average)
- 10 pages per document (average)

| Service | Usage | Monthly Cost |
|---------|-------|--------------|
| Cloud Run (API) | 100K requests, 1GB RAM | $8 |
| Cloud Run (Worker) | 3K requests, 2GB RAM | $12 |
| Cloud Run (Webhook) | 5K requests, 512MB RAM | $3 |
| Document AI | 30K pages | $450 ($0.015/page) |
| BigQuery (storage) | 50GB | $10 |
| BigQuery (queries) | 500GB scanned | $2.50 |
| Cloud Storage | 100GB, 10K ops | $5 |
| Pub/Sub | 10M messages | $2 |
| **TOTAL** | | **$492.50** |

**Cost per Case**: $0.49

**Cost Optimization Tips**:
- Use BigQuery partitioning to reduce scanned data
- Enable GCS lifecycle policies to move old docs to Coldline
- Batch Document AI requests (process 5 pages at once vs. 5 separate calls)
- Use Cloud Run min instances = 0 for worker (accept cold start latency)

---

## Technology Stack Summary

| Layer | Technology | Version |
|-------|------------|---------|
| **Compute** | Cloud Run | Latest (managed) |
| **Language** | Python | 3.11 |
| **Web Framework** | Flask | 3.0 |
| **Message Queue** | Pub/Sub | Latest (managed) |
| **Storage** | Cloud Storage | Latest (managed) |
| **Database** | BigQuery | Latest (managed) |
| **Document Processing** | Document AI | v1 |
| **Conversational AI** | Dialogflow CX | Latest (managed) |
| **Infrastructure** | Terraform | 1.6+ |
| **Logging** | Cloud Logging | Latest (managed) |
| **Monitoring** | Cloud Monitoring | Latest (managed) |
| **IAM** | Workload Identity | Latest (managed) |
| **Secrets** | Secret Manager | Latest (managed) |

---

## Compliance & Regulatory Considerations

**Applicable Regulations**:
- **GLBA (Gramm-Leach-Bliley Act)**: Financial data privacy
- **FCRA (Fair Credit Reporting Act)**: Consumer report handling
- **ECOA (Equal Credit Opportunity Act)**: Non-discrimination, record retention
- **TILA (Truth in Lending Act)**: Disclosure requirements
- **BSA/AML (Bank Secrecy Act / Anti-Money Laundering)**: Identity verification

**Solution Compliance Features**:
- **Audit Trail**: Every action logged with timestamp, actor, IP address
- **Data Retention**: 7-year retention for audit_log and documents
- **Access Control**: Role-based access, MFA required for human reviewers
- **Encryption**: FIPS 140-2 compliant (GCP default)
- **Monitoring**: Real-time alerts for suspicious activity (e.g., mass data export)

---

## Next Steps

1. Review this architecture with your team
2. Customize for specific LOS/core system integrations
3. Deploy POC using Terraform (see `deployment_guide.md`)
4. Load test with sample data (see `sample_data/`)
5. Security review with CISO (see `security_governance.md`)
6. Pilot with 50-100 real applications
7. Measure KPIs and iterate

---

## Appendix: Glossary

| Term | Definition |
|------|------------|
| **Case** | Single loan application instance |
| **Document** | Individual file (PDF, image) uploaded for a case |
| **Extraction** | Process of converting document to structured fields |
| **Confidence** | Document AI's certainty score (0.0 - 1.0) for extracted field |
| **LOS** | Loan Origination System (vendor software for loan processing) |
| **Core** | Core Banking System (account, member, transaction management) |
| **DLQ** | Dead-Letter Queue (failed messages for manual review) |
| **SLA** | Service Level Agreement (e.g., "respond within 24 hours") |

---

**Document Version**: 1.0
**Last Updated**: 2026-01-12
**Authors**: Tytan Architecture Team
