"""
Tytan LendingOps & MemberAssist - Cloud Run API
Main application entry point
"""

import os
import logging
import json
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.cloud import bigquery, storage, pubsub_v1
from google.api_core import exceptions
import hashlib
import uuid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for web form

# Configuration
PROJECT_ID = os.getenv('PROJECT_ID', 'tytan-lending-dev')
REGION = os.getenv('REGION', 'us-central1')
DATASET_ID = os.getenv('DATASET_ID', 'tytan_lending_ops')
BUCKET_NAME = os.getenv('BUCKET_NAME', f'{PROJECT_ID}-lending-docs')
PUBSUB_TOPIC = os.getenv('PUBSUB_TOPIC', 'document-uploaded')
MOCK_MODE = os.getenv('MOCK_MODE', 'false').lower() == 'true'

# Initialize GCP clients
try:
    bq_client = bigquery.Client(project=PROJECT_ID)
    storage_client = storage.Client(project=PROJECT_ID)
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(PROJECT_ID, PUBSUB_TOPIC)

    logger.info(f"Initialized GCP clients for project: {PROJECT_ID}")
    logger.info(f"Mock mode: {MOCK_MODE}")
except Exception as e:
    logger.error(f"Failed to initialize GCP clients: {e}")
    # Continue anyway for local development
    bq_client = None
    storage_client = None
    publisher = None


def log_audit_event(case_id, event_type, actor, payload, req=None):
    """Log audit event to BigQuery"""
    try:
        event = {
            "event_id": str(uuid.uuid4()),
            "case_id": case_id,
            "event_type": event_type,
            "actor": actor or "system",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "payload": json.dumps(payload),
            "ip_address": req.headers.get("X-Forwarded-For", req.remote_addr) if req else None,
            "user_agent": req.headers.get("User-Agent") if req else None
        }

        if MOCK_MODE:
            logger.info(f"[MOCK] Audit event: {event}")
        else:
            table_id = f"{PROJECT_ID}.{DATASET_ID}.audit_log"
            errors = bq_client.insert_rows_json(table_id, [event])
            if errors:
                logger.error(f"Failed to insert audit log: {errors}")
    except Exception as e:
        logger.error(f"Failed to log audit event: {e}")


def generate_case_id():
    """Generate unique case ID in format: CU-YYYY-NNNNN"""
    year = datetime.utcnow().year
    # In production, query BigQuery for max sequence number
    # For now, use timestamp-based unique ID
    sequence = int(datetime.utcnow().timestamp() * 1000) % 100000
    return f"CU-{year}-{sequence:05d}"


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "mock_mode": MOCK_MODE
    }), 200


@app.route('/cases', methods=['POST'])
def create_case():
    """
    Create a new loan application case

    Request body:
    {
      "member_id": "M-12345",
      "loan_type": "auto",
      "loan_amount": 25000,
      "member_contact": {
        "email": "sarah@example.com",
        "phone": "+15551234567"
      },
      "metadata": {...}
    }
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['member_id', 'loan_type', 'loan_amount']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        # Generate case ID
        case_id = generate_case_id()

        # Create case record
        case_record = {
            "case_id": case_id,
            "member_id": data['member_id'],
            "loan_type": data['loan_type'],
            "loan_amount": float(data['loan_amount']),
            "status": "SUBMITTED",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "member_contact_email": data.get('member_contact', {}).get('email'),
            "member_contact_phone": data.get('member_contact', {}).get('phone'),
            "source_channel": data.get('metadata', {}).get('source', 'api'),
            "metadata": json.dumps(data.get('metadata', {}))
        }

        # Insert into BigQuery
        if MOCK_MODE:
            logger.info(f"[MOCK] Created case: {case_record}")
        else:
            table_id = f"{PROJECT_ID}.{DATASET_ID}.cases"
            errors = bq_client.insert_rows_json(table_id, [case_record])
            if errors:
                logger.error(f"Failed to insert case: {errors}")
                return jsonify({"error": "Failed to create case"}), 500

        # Log audit event
        log_audit_event(case_id, "CASE_CREATED", data.get('member_id'), case_record, request)

        # Define required documents based on loan type
        required_documents = get_required_documents(data['loan_type'])

        # Response
        response = {
            "case_id": case_id,
            "status": "SUBMITTED",
            "created_at": case_record['created_at'],
            "required_documents": required_documents
        }

        logger.info(f"Created case: {case_id}")
        return jsonify(response), 201

    except Exception as e:
        logger.error(f"Error creating case: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


def get_required_documents(loan_type):
    """Return list of required documents based on loan type"""
    base_docs = ["drivers_license"]

    if loan_type == "auto":
        return base_docs + ["paystub_recent_2", "bank_statement_30days", "proof_of_insurance"]
    elif loan_type == "personal":
        return base_docs + ["paystub_recent_2", "bank_statement_60days"]
    elif loan_type == "mortgage":
        return base_docs + ["paystub_recent_2", "w2_2years", "bank_statement_60days", "tax_returns_2years"]
    else:
        return base_docs + ["paystub_recent_2", "bank_statement_30days"]


@app.route('/cases/<case_id>/documents', methods=['POST'])
def upload_document(case_id):
    """
    Upload document for a case

    Supports multipart/form-data (file upload) or JSON (GCS URI reference)
    """
    try:
        # Check if case exists
        if not MOCK_MODE:
            query = f"""
                SELECT case_id FROM `{PROJECT_ID}.{DATASET_ID}.cases`
                WHERE case_id = @case_id
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("case_id", "STRING", case_id)
                ]
            )
            result = bq_client.query(query, job_config=job_config).result()
            if result.total_rows == 0:
                return jsonify({"error": f"Case not found: {case_id}"}), 404

        # Handle file upload
        if 'file' in request.files:
            file = request.files['file']
            document_type = request.form.get('document_type', 'unknown')

            # Generate document ID
            document_id = f"doc-{uuid.uuid4().hex[:12]}"

            # Read file content
            file_content = file.read()
            file_size = len(file_content)
            file_hash = hashlib.sha256(file_content).hexdigest()

            # Check for duplicate (same hash)
            if not MOCK_MODE:
                dup_query = f"""
                    SELECT document_id FROM `{PROJECT_ID}.{DATASET_ID}.documents`
                    WHERE case_id = @case_id AND file_hash_sha256 = @file_hash
                """
                dup_job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("case_id", "STRING", case_id),
                        bigquery.ScalarQueryParameter("file_hash", "STRING", file_hash)
                    ]
                )
                dup_result = bq_client.query(dup_query, dup_job_config=dup_job_config).result()
                if dup_result.total_rows > 0:
                    existing_doc = list(dup_result)[0]
                    logger.info(f"Duplicate document detected: {existing_doc['document_id']}")
                    return jsonify({
                        "document_id": existing_doc['document_id'],
                        "upload_status": "duplicate",
                        "message": "This document has already been uploaded"
                    }), 200

            # Upload to Cloud Storage
            file_ext = file.filename.split('.')[-1] if '.' in file.filename else 'pdf'
            gcs_path = f"cases/{case_id}/{document_id}.{file_ext}"
            gcs_uri = f"gs://{BUCKET_NAME}/{gcs_path}"

            if MOCK_MODE:
                logger.info(f"[MOCK] Upload to GCS: {gcs_uri}")
            else:
                bucket = storage_client.bucket(BUCKET_NAME)
                blob = bucket.blob(gcs_path)
                blob.upload_from_string(file_content, content_type=file.content_type)

            # Create document record
            document_record = {
                "document_id": document_id,
                "case_id": case_id,
                "document_type": document_type,
                "gcs_uri": gcs_uri,
                "file_size_bytes": file_size,
                "mime_type": file.content_type or "application/pdf",
                "uploaded_at": datetime.utcnow().isoformat() + "Z",
                "status": "UPLOADED",
                "file_hash_sha256": file_hash
            }

            # Insert into BigQuery
            if MOCK_MODE:
                logger.info(f"[MOCK] Created document record: {document_record}")
            else:
                table_id = f"{PROJECT_ID}.{DATASET_ID}.documents"
                errors = bq_client.insert_rows_json(table_id, [document_record])
                if errors:
                    logger.error(f"Failed to insert document: {errors}")
                    return jsonify({"error": "Failed to create document record"}), 500

            # Publish to Pub/Sub
            message_data = {
                "case_id": case_id,
                "document_id": document_id,
                "gcs_uri": gcs_uri,
                "document_type": document_type,
                "timestamp": document_record['uploaded_at'],
                "correlation_id": f"req-{uuid.uuid4().hex[:8]}"
            }

            if MOCK_MODE:
                logger.info(f"[MOCK] Published to Pub/Sub: {message_data}")
                pubsub_message_id = "mock-message-id-12345"
            else:
                future = publisher.publish(
                    topic_path,
                    json.dumps(message_data).encode('utf-8')
                )
                pubsub_message_id = future.result()

            # Log audit event
            log_audit_event(case_id, "DOCUMENT_UPLOADED", None, document_record, request)

            # Response
            response = {
                "document_id": document_id,
                "gcs_uri": gcs_uri,
                "upload_status": "success",
                "pubsub_message_id": pubsub_message_id
            }

            logger.info(f"Uploaded document {document_id} for case {case_id}")
            return jsonify(response), 201

        else:
            return jsonify({"error": "No file provided"}), 400

    except Exception as e:
        logger.error(f"Error uploading document: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/cases/<case_id>', methods=['GET'])
def get_case(case_id):
    """Get case details including documents and extracted fields"""
    try:
        if MOCK_MODE:
            # Return mock data
            return jsonify({
                "case_id": case_id,
                "status": "SUBMITTED",
                "created_at": datetime.utcnow().isoformat() + "Z",
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "loan_type": "auto",
                "loan_amount": 25000,
                "documents": [],
                "missing_documents": ["drivers_license", "paystub_recent_2", "bank_statement_30days"],
                "extracted_applicant": {}
            }), 200

        # Query case details
        query = f"""
            SELECT * FROM `{PROJECT_ID}.{DATASET_ID}.cases`
            WHERE case_id = @case_id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("case_id", "STRING", case_id)
            ]
        )
        case_result = bq_client.query(query, job_config=job_config).result()

        if case_result.total_rows == 0:
            return jsonify({"error": f"Case not found: {case_id}"}), 404

        case_row = list(case_result)[0]

        # Query documents
        doc_query = f"""
            SELECT
                d.document_id,
                d.document_type,
                d.status,
                d.uploaded_at,
                COUNT(e.extraction_id) as fields_extracted,
                AVG(e.confidence) as avg_confidence
            FROM `{PROJECT_ID}.{DATASET_ID}.documents` d
            LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.extracted_fields` e
                ON d.document_id = e.document_id
            WHERE d.case_id = @case_id
            GROUP BY d.document_id, d.document_type, d.status, d.uploaded_at
        """
        doc_result = bq_client.query(doc_query, job_config=job_config).result()

        documents = []
        for row in doc_result:
            doc_summary = {
                "document_id": row.document_id,
                "document_type": row.document_type,
                "status": row.status,
                "uploaded_at": row.uploaded_at.isoformat() + "Z" if row.uploaded_at else None,
                "extraction_summary": {
                    "fields_extracted": row.fields_extracted or 0,
                    "avg_confidence": float(row.avg_confidence) if row.avg_confidence else 0.0,
                    "needs_review": row.avg_confidence < 0.85 if row.avg_confidence else False
                }
            }
            documents.append(doc_summary)

        # Determine missing documents
        required_docs = get_required_documents(case_row.loan_type)
        uploaded_types = [d['document_type'] for d in documents]
        missing_docs = [doc for doc in required_docs if doc not in uploaded_types]

        # Build response
        response = {
            "case_id": case_id,
            "status": case_row.status,
            "created_at": case_row.created_at.isoformat() + "Z" if case_row.created_at else None,
            "updated_at": case_row.updated_at.isoformat() + "Z" if case_row.updated_at else None,
            "loan_type": case_row.loan_type,
            "loan_amount": float(case_row.loan_amount),
            "documents": documents,
            "missing_documents": missing_docs,
            "extracted_applicant": {}  # TODO: aggregate extracted fields
        }

        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Error getting case: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/cases/<case_id>/review', methods=['POST'])
def review_case(case_id):
    """Human review and correction of extracted fields"""
    try:
        data = request.get_json()
        reviewer_id = data.get('reviewer_id', 'anonymous')
        document_id = data.get('document_id')
        field_corrections = data.get('field_corrections', [])
        approval_status = data.get('approval_status', 'APPROVED')

        review_id = f"rev-{uuid.uuid4().hex[:8]}"

        # Apply corrections
        for correction in field_corrections:
            correction_record = {
                "correction_id": f"corr-{uuid.uuid4().hex[:8]}",
                "extraction_id": correction.get('extraction_id', 'unknown'),
                "case_id": case_id,
                "document_id": document_id,
                "field_name": correction['field_name'],
                "original_value": correction.get('extracted_value'),
                "corrected_value": correction['corrected_value'],
                "reviewer_id": reviewer_id,
                "review_timestamp": datetime.utcnow().isoformat() + "Z",
                "correction_reason": correction.get('reason', 'Human review')
            }

            if MOCK_MODE:
                logger.info(f"[MOCK] Correction: {correction_record}")
            else:
                table_id = f"{PROJECT_ID}.{DATASET_ID}.field_corrections"
                errors = bq_client.insert_rows_json(table_id, [correction_record])
                if errors:
                    logger.error(f"Failed to insert correction: {errors}")

        # Update case status
        if not MOCK_MODE:
            update_query = f"""
                UPDATE `{PROJECT_ID}.{DATASET_ID}.cases`
                SET status = @status, updated_at = CURRENT_TIMESTAMP()
                WHERE case_id = @case_id
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("status", "STRING", "READY_FOR_DECISION"),
                    bigquery.ScalarQueryParameter("case_id", "STRING", case_id)
                ]
            )
            bq_client.query(update_query, job_config=job_config).result()

        # Log audit event
        log_audit_event(case_id, "REVIEW_COMPLETED", reviewer_id, data, request)

        response = {
            "review_id": review_id,
            "case_id": case_id,
            "status": "READY_FOR_DECISION",
            "corrections_applied": len(field_corrections)
        }

        logger.info(f"Review completed for case {case_id} by {reviewer_id}")
        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Error reviewing case: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
