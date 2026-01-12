# Sample Data & Test Scenarios

This directory contains sample data payloads and test scenarios for the Tytan LendingOps & MemberAssist solution.

## Sample Payloads

### 1. Create Case (sample_case_create.json)

Used to create a new loan application case via API:

```bash
curl -X POST https://YOUR-API-URL/cases \
  -H "Content-Type: application/json" \
  -d @sample_case_create.json
```

**Expected Response**:
```json
{
  "case_id": "CU-2024-00123",
  "status": "SUBMITTED",
  "created_at": "2024-01-15T10:30:00Z",
  "required_documents": [
    "drivers_license",
    "paystub_recent_2",
    "bank_statement_30days",
    "proof_of_insurance"
  ]
}
```

### 2. Extracted Output (sample_extracted_output.json)

Example of Document AI extraction results for a driver's license.

## Test Scenarios

### Scenario 1: Happy Path (Auto Loan)

**Objective**: Member applies for auto loan, uploads all documents, gets approved

**Steps**:

1. **Create Case**
   ```bash
   curl -X POST $API_URL/cases \
     -H "Content-Type: application/json" \
     -d @sample_case_create.json
   ```
   Expected: HTTP 201, `case_id` returned

2. **Upload Driver's License**
   ```bash
   curl -X POST $API_URL/cases/CU-2024-00123/documents \
     -F "file=@sample_drivers_license.pdf" \
     -F "document_type=drivers_license"
   ```
   Expected: HTTP 201, document uploaded to GCS, Pub/Sub event published

3. **Wait for Extraction** (automatic, ~10-30 seconds in real mode, instant in mock mode)
   - Document AI Worker processes Pub/Sub message
   - Extracts fields, writes to BigQuery
   - Updates case status

4. **Check Case Status**
   ```bash
   curl $API_URL/cases/CU-2024-00123
   ```
   Expected: `status: "NEEDS_REVIEW"` or `"READY_FOR_REVIEW"` depending on confidence

5. **Upload Remaining Documents**
   - Repeat step 2 for paystub, bank statement, proof of insurance

6. **Human Review (if needed)**
   ```bash
   curl -X POST $API_URL/cases/CU-2024-00123/review \
     -H "Content-Type: application/json" \
     -d '{
       "reviewer_id": "loan.officer@creditunion.com",
       "document_id": "doc-abc-123",
       "field_corrections": [],
       "approval_status": "APPROVED"
     }'
   ```
   Expected: Case status updated to `READY_FOR_DECISION`

7. **Chatbot Status Inquiry**
   ```bash
   curl -X POST $WEBHOOK_URL/dialogflow-webhook \
     -H "Content-Type: application/json" \
     -d '{
       "sessionInfo": {
         "parameters": {"case_id": "CU-2024-00123"}
       },
       "fulfillmentInfo": {
         "tag": "get_case_status"
       }
     }'
   ```
   Expected: Formatted status message with document checklist

**Success Criteria**:
- All documents extracted with avg confidence > 85%
- Case progresses through states: SUBMITTED → EXTRACTING → READY_FOR_REVIEW → READY_FOR_DECISION
- Member can query status via chatbot
- All events logged in BigQuery audit_log

---

### Scenario 2: Low Confidence Extraction (Human Review Required)

**Objective**: Document has low-quality scan, requires human correction

**Steps**:

1. Create case (same as Scenario 1)

2. Upload poor-quality document (blurry scan)
   - In mock mode, modify mock data to return low confidence fields:
     ```python
     {"field_name": "employer_ein", "value": "12-345678", "confidence": 0.65}
     ```

3. Check case status
   Expected: `status: "NEEDS_REVIEW"`, `avg_confidence < 0.85`

4. Loan officer reviews and corrects
   ```bash
   curl -X POST $API_URL/cases/CU-2024-00123/review \
     -H "Content-Type: application/json" \
     -d '{
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
     }'
   ```

5. Verify correction in BigQuery
   ```sql
   SELECT * FROM field_corrections WHERE case_id = 'CU-2024-00123';
   ```

**Success Criteria**:
- Low confidence fields flagged automatically
- Corrections logged with reviewer ID and timestamp
- Original extracted value preserved for audit

---

### Scenario 3: Missing Documents

**Objective**: Member submits incomplete application

**Steps**:

1. Create case for mortgage (requires many documents)

2. Upload only driver's license

3. Check status
   ```bash
   curl $API_URL/cases/CU-2024-00123
   ```
   Expected: `missing_documents` includes w2, tax returns, bank statement

4. Chatbot inquiry
   Expected: "Still waiting for: W-2 Forms (last 2 years), Tax Returns..."

5. Upload missing documents
   - Case status updates automatically as documents arrive

**Success Criteria**:
- API returns clear list of missing documents
- Chatbot provides user-friendly document names
- Status updates in real-time as documents uploaded

---

### Scenario 4: Duplicate Document Upload

**Objective**: Test idempotency - same file uploaded twice

**Steps**:

1. Create case

2. Upload document
   ```bash
   curl -X POST $API_URL/cases/CU-2024-00123/documents \
     -F "file=@drivers_license.pdf" \
     -F "document_type=drivers_license"
   ```
   Note `document_id` returned

3. Upload same file again (exact same bytes)
   Expected: HTTP 200, returns existing `document_id`, message "This document has already been uploaded"

4. Verify in BigQuery
   ```sql
   SELECT COUNT(*) FROM documents WHERE case_id = 'CU-2024-00123' AND file_hash_sha256 = '<hash>';
   ```
   Expected: Only 1 row

**Success Criteria**:
- No duplicate documents created
- Same document_id returned
- Idempotency key (file hash) works correctly

---

### Scenario 5: Pub/Sub Retry & Dead Letter Queue

**Objective**: Test failure handling and retry logic

**Steps**:

1. Disable Document AI worker (stop Cloud Run service)

2. Upload document
   Expected: Pub/Sub message published but not acknowledged

3. Check subscription metrics
   ```bash
   gcloud pubsub subscriptions describe document-ai-worker-sub
   ```
   Expected: `num_undelivered_messages > 0`

4. Re-enable worker
   Expected: Worker pulls message, processes, ACKs

5. Simulate permanent failure:
   - Modify worker to always throw exception
   - Upload document
   - Wait for max retries (5 attempts)
   - Check DLQ:
     ```bash
     gcloud pubsub subscriptions pull document-uploaded-dlq --limit=10
     ```
   Expected: Failed message in DLQ

6. Fix worker, replay from DLQ
   ```bash
   python scripts/replay_dlq.py
   ```

**Success Criteria**:
- Transient failures retry with exponential backoff
- After 5 failures, message moves to DLQ
- DLQ messages can be replayed manually

---

## Load Testing

### 100 Concurrent Case Creations

```bash
# Install Apache Bench
sudo apt-get install apache2-utils

# Create load test payload
cat > load_test_payload.json <<EOF
{"member_id":"M-LOAD-TEST","loan_type":"auto","loan_amount":25000,"member_contact":{"email":"test@example.com","phone":"+15551234567"}}
EOF

# Run 100 concurrent requests
ab -n 100 -c 10 -T 'application/json' -p load_test_payload.json $API_URL/cases
```

**Metrics to Monitor**:
- API latency (p50, p95, p99)
- Cloud Run autoscaling (instances created)
- BigQuery write throughput
- Pub/Sub message backlog

**Expected Results**:
- p95 latency < 2 seconds
- Zero errors
- Autoscaling responds within 30 seconds
- Pub/Sub processes all messages within 5 minutes

---

## BigQuery Sample Queries

### Query 1: Case Summary Report

```sql
SELECT
  DATE(created_at) as date,
  loan_type,
  status,
  COUNT(*) as case_count,
  AVG(loan_amount) as avg_loan_amount,
  MIN(loan_amount) as min_loan_amount,
  MAX(loan_amount) as max_loan_amount
FROM `tytan_lending_ops.cases`
WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY date, loan_type, status
ORDER BY date DESC, case_count DESC;
```

### Query 2: Extraction Accuracy Report

```sql
SELECT
  d.document_type,
  COUNT(DISTINCT d.document_id) as total_documents,
  AVG(e.confidence) as avg_confidence,
  SUM(CASE WHEN ef.is_corrected THEN 1 ELSE 0 END) as corrections_count,
  SAFE_DIVIDE(
    SUM(CASE WHEN ef.is_corrected THEN 1 ELSE 0 END),
    COUNT(DISTINCT e.extraction_id)
  ) * 100 as correction_rate_pct
FROM `tytan_lending_ops.documents` d
LEFT JOIN `tytan_lending_ops.extracted_fields` e ON d.document_id = e.document_id
LEFT JOIN `tytan_lending_ops.extracted_fields` ef ON e.extraction_id = ef.extraction_id
WHERE DATE(d.uploaded_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
GROUP BY d.document_type
ORDER BY total_documents DESC;
```

### Query 3: Audit Trail for Case

```sql
SELECT
  timestamp,
  event_type,
  actor,
  JSON_VALUE(payload, '$.status') as status_change,
  ip_address
FROM `tytan_lending_ops.audit_log`
WHERE case_id = 'CU-2024-00123'
ORDER BY timestamp ASC;
```

---

## Notes

- All sample payloads use fictional data
- In mock mode, Document AI extraction is simulated (no API calls)
- For production testing, replace mock mode with real Document AI processors
- Load testing should be done in a non-production environment

---

**Last Updated**: 2026-01-12
**Version**: 1.0
