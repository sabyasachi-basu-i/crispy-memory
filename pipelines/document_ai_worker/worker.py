"""
Tytan LendingOps & MemberAssist - Document AI Worker
Consumes Pub/Sub events, extracts data with Document AI, writes to BigQuery
"""

import os
import logging
import json
import time
from datetime import datetime
from google.cloud import pubsub_v1, bigquery, storage
from google.cloud import documentai_v1 as documentai
from concurrent import futures
import uuid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
PROJECT_ID = os.getenv('PROJECT_ID', 'tytan-lending-dev')
REGION = os.getenv('REGION', 'us-central1')
DATASET_ID = os.getenv('DATASET_ID', 'tytan_lending_ops')
SUBSCRIPTION_ID = os.getenv('SUBSCRIPTION_ID', 'document-ai-worker-sub')
MOCK_MODE = os.getenv('MOCK_MODE', 'true').lower() == 'true'

# Document AI processor IDs (set these in environment or use mock mode)
DOCAI_IDENTITY_PROCESSOR = os.getenv('DOCAI_IDENTITY_PROCESSOR', '')
DOCAI_FORM_PROCESSOR = os.getenv('DOCAI_FORM_PROCESSOR', '')

# Confidence threshold
CONFIDENCE_THRESHOLD = float(os.getenv('CONFIDENCE_THRESHOLD', '0.85'))

# Initialize GCP clients
try:
    subscriber = pubsub_v1.SubscriberClient()
    bq_client = bigquery.Client(project=PROJECT_ID)
    storage_client = storage.Client(project=PROJECT_ID)

    if not MOCK_MODE and DOCAI_IDENTITY_PROCESSOR:
        docai_client = documentai.DocumentProcessorServiceClient()
    else:
        docai_client = None

    subscription_path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_ID)

    logger.info(f"Initialized worker for subscription: {subscription_path}")
    logger.info(f"Mock mode: {MOCK_MODE}")
except Exception as e:
    logger.error(f"Failed to initialize clients: {e}")
    raise


def get_processor_for_document_type(document_type):
    """Map document type to Document AI processor"""
    processor_map = {
        "drivers_license": DOCAI_IDENTITY_PROCESSOR,
        "passport": DOCAI_IDENTITY_PROCESSOR,
        "paystub": DOCAI_FORM_PROCESSOR,
        "paystub_recent_2": DOCAI_FORM_PROCESSOR,
        "bank_statement_30days": DOCAI_FORM_PROCESSOR,
        "bank_statement_60days": DOCAI_FORM_PROCESSOR,
        "w2": DOCAI_FORM_PROCESSOR,
        "w2_2years": DOCAI_FORM_PROCESSOR,
        "tax_returns_2years": DOCAI_FORM_PROCESSOR
    }

    return processor_map.get(document_type, DOCAI_FORM_PROCESSOR)


def extract_fields_mock(document_type):
    """Return mock extracted fields for testing"""
    mock_extractions = {
        "drivers_license": [
            {"field_name": "full_name", "value": "John Doe", "confidence": 0.98},
            {"field_name": "date_of_birth", "value": "1985-03-15", "confidence": 0.99},
            {"field_name": "license_number", "value": "D1234567", "confidence": 0.95},
            {"field_name": "address", "value": "123 Main St, Anytown, CA 12345", "confidence": 0.92},
            {"field_name": "expiration_date", "value": "2027-03-15", "confidence": 0.97},
            {"field_name": "state", "value": "CA", "confidence": 0.99},
            {"field_name": "gender", "value": "M", "confidence": 0.96},
            {"field_name": "height", "value": "5'10\"", "confidence": 0.88}
        ],
        "paystub": [
            {"field_name": "employee_name", "value": "John Doe", "confidence": 0.96},
            {"field_name": "employer_name", "value": "Acme Corporation", "confidence": 0.94},
            {"field_name": "employer_ein", "value": "12-3456789", "confidence": 0.78},  # Low confidence
            {"field_name": "pay_period_start", "value": "2024-01-01", "confidence": 0.92},
            {"field_name": "pay_period_end", "value": "2024-01-15", "confidence": 0.93},
            {"field_name": "gross_pay", "value": "2500.00", "confidence": 0.97},
            {"field_name": "net_pay", "value": "1950.00", "confidence": 0.95},
            {"field_name": "ytd_gross", "value": "7500.00", "confidence": 0.89}
        ],
        "bank_statement_30days": [
            {"field_name": "account_holder", "value": "John Doe", "confidence": 0.97},
            {"field_name": "account_number", "value": "****1234", "confidence": 0.91},
            {"field_name": "statement_date", "value": "2024-01-31", "confidence": 0.98},
            {"field_name": "beginning_balance", "value": "5000.00", "confidence": 0.96},
            {"field_name": "ending_balance", "value": "4750.00", "confidence": 0.95},
            {"field_name": "bank_name", "value": "First National Bank", "confidence": 0.99}
        ]
    }

    # Default for unknown document types
    default_extraction = [
        {"field_name": "document_date", "value": "2024-01-15", "confidence": 0.90},
        {"field_name": "name", "value": "John Doe", "confidence": 0.85}
    ]

    return mock_extractions.get(document_type, default_extraction)


def extract_fields_real(gcs_uri, processor_name):
    """Extract fields using Document AI"""
    try:
        # Download document from GCS
        if gcs_uri.startswith("gs://"):
            # Parse GCS URI
            parts = gcs_uri.replace("gs://", "").split("/", 1)
            bucket_name = parts[0]
            blob_name = parts[1]

            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            document_content = blob.download_as_bytes()
        else:
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        # Prepare Document AI request
        raw_document = documentai.RawDocument(
            content=document_content,
            mime_type="application/pdf"  # Assume PDF for now
        )

        request = documentai.ProcessRequest(
            name=processor_name,
            raw_document=raw_document
        )

        # Call Document AI
        logger.info(f"Calling Document AI processor: {processor_name}")
        result = docai_client.process_document(request=request)

        # Parse entities
        extracted_fields = []
        for entity in result.document.entities:
            field = {
                "field_name": entity.type_,
                "value": entity.mention_text,
                "confidence": entity.confidence,
                "page_number": entity.page_anchor.page_refs[0].page if entity.page_anchor.page_refs else 0,
                "bounding_box": json.dumps({
                    "vertices": [
                        {"x": v.x, "y": v.y}
                        for v in entity.page_anchor.page_refs[0].bounding_poly.vertices
                    ]
                }) if entity.page_anchor.page_refs and entity.page_anchor.page_refs[0].bounding_poly else None
            }
            extracted_fields.append(field)

        logger.info(f"Extracted {len(extracted_fields)} fields")
        return extracted_fields

    except Exception as e:
        logger.error(f"Document AI extraction failed: {e}", exc_info=True)
        raise


def check_if_already_processed(case_id, document_id):
    """Check if extraction already exists (idempotency)"""
    try:
        query = f"""
            SELECT COUNT(*) as count
            FROM `{PROJECT_ID}.{DATASET_ID}.extracted_fields`
            WHERE case_id = @case_id AND document_id = @document_id
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("case_id", "STRING", case_id),
                bigquery.ScalarQueryParameter("document_id", "STRING", document_id)
            ]
        )

        result = bq_client.query(query, job_config=job_config).result()
        row = list(result)[0]

        return row['count'] > 0

    except Exception as e:
        logger.error(f"Error checking if already processed: {e}")
        return False


def write_extracted_fields(case_id, document_id, fields, processor_id):
    """Write extracted fields to BigQuery"""
    try:
        table_id = f"{PROJECT_ID}.{DATASET_ID}.extracted_fields"

        rows_to_insert = []
        for field in fields:
            row = {
                "extraction_id": str(uuid.uuid4()),
                "case_id": case_id,
                "document_id": document_id,
                "field_name": field['field_name'],
                "value": str(field['value']),
                "confidence": float(field['confidence']),
                "page_number": field.get('page_number', 0),
                "bounding_box": field.get('bounding_box'),
                "extracted_at": datetime.utcnow().isoformat() + "Z",
                "processor_id": processor_id,
                "is_corrected": False
            }
            rows_to_insert.append(row)

        if MOCK_MODE:
            logger.info(f"[MOCK] Would insert {len(rows_to_insert)} rows into {table_id}")
        else:
            errors = bq_client.insert_rows_json(table_id, rows_to_insert)
            if errors:
                logger.error(f"Failed to insert extracted fields: {errors}")
                raise Exception(f"BigQuery insert failed: {errors}")

        logger.info(f"Inserted {len(rows_to_insert)} extracted fields for document {document_id}")

    except Exception as e:
        logger.error(f"Error writing extracted fields: {e}", exc_info=True)
        raise


def update_case_status(case_id, avg_confidence):
    """Update case status based on extraction confidence"""
    try:
        if avg_confidence < CONFIDENCE_THRESHOLD:
            new_status = "NEEDS_REVIEW"
        else:
            new_status = "READY_FOR_REVIEW"

        if MOCK_MODE:
            logger.info(f"[MOCK] Would update case {case_id} to status: {new_status}")
        else:
            query = f"""
                UPDATE `{PROJECT_ID}.{DATASET_ID}.cases`
                SET status = @status, updated_at = CURRENT_TIMESTAMP()
                WHERE case_id = @case_id
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("status", "STRING", new_status),
                    bigquery.ScalarQueryParameter("case_id", "STRING", case_id)
                ]
            )

            bq_client.query(query, job_config=job_config).result()

        logger.info(f"Updated case {case_id} status to {new_status} (avg confidence: {avg_confidence:.2f})")

    except Exception as e:
        logger.error(f"Error updating case status: {e}", exc_info=True)


def update_document_status(document_id, status):
    """Update document processing status"""
    try:
        if MOCK_MODE:
            logger.info(f"[MOCK] Would update document {document_id} to status: {status}")
        else:
            query = f"""
                UPDATE `{PROJECT_ID}.{DATASET_ID}.documents`
                SET status = @status
                WHERE document_id = @document_id
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("status", "STRING", status),
                    bigquery.ScalarQueryParameter("document_id", "STRING", document_id)
                ]
            )

            bq_client.query(query, job_config=job_config).result()

        logger.info(f"Updated document {document_id} status to {status}")

    except Exception as e:
        logger.error(f"Error updating document status: {e}", exc_info=True)


def process_message(message):
    """Process a single Pub/Sub message"""
    try:
        # Parse message
        message_data = json.loads(message.data.decode('utf-8'))
        case_id = message_data['case_id']
        document_id = message_data['document_id']
        gcs_uri = message_data['gcs_uri']
        document_type = message_data.get('document_type', 'unknown')

        logger.info(f"Processing document {document_id} for case {case_id}")

        # Check if already processed (idempotency)
        if not MOCK_MODE and check_if_already_processed(case_id, document_id):
            logger.info(f"Document {document_id} already processed, skipping")
            message.ack()
            return

        # Update document status to EXTRACTING
        update_document_status(document_id, "EXTRACTING")

        # Extract fields
        if MOCK_MODE:
            logger.info(f"[MOCK] Extracting fields from {gcs_uri}")
            extracted_fields = extract_fields_mock(document_type)
            processor_id = "mock-processor"
        else:
            processor_name = get_processor_for_document_type(document_type)
            if not processor_name:
                logger.warning(f"No processor configured for document type: {document_type}")
                # Use generic form parser as fallback
                processor_name = DOCAI_FORM_PROCESSOR

            extracted_fields = extract_fields_real(gcs_uri, processor_name)
            processor_id = processor_name

        # Write to BigQuery
        write_extracted_fields(case_id, document_id, extracted_fields, processor_id)

        # Calculate average confidence
        confidences = [f['confidence'] for f in extracted_fields]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        # Update document status
        if avg_confidence < CONFIDENCE_THRESHOLD:
            update_document_status(document_id, "NEEDS_REVIEW")
        else:
            update_document_status(document_id, "EXTRACTED")

        # Update case status
        update_case_status(case_id, avg_confidence)

        # Acknowledge message
        logger.info(f"Successfully processed document {document_id} (avg confidence: {avg_confidence:.2f})")
        message.ack()

    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        # NACK message to retry (with exponential backoff configured in subscription)
        message.nack()


def callback(message):
    """Pub/Sub message callback"""
    try:
        process_message(message)
    except Exception as e:
        logger.error(f"Unhandled exception in callback: {e}", exc_info=True)
        message.nack()


def main():
    """Main worker loop"""
    logger.info("Starting Document AI Worker...")
    logger.info(f"Subscription: {subscription_path}")
    logger.info(f"Mock mode: {MOCK_MODE}")

    # Subscribe to Pub/Sub
    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)

    logger.info("Listening for messages...")

    # Keep worker running
    with subscriber:
        try:
            streaming_pull_future.result()
        except TimeoutError:
            streaming_pull_future.cancel()
            streaming_pull_future.result()
        except KeyboardInterrupt:
            logger.info("Shutting down worker...")
            streaming_pull_future.cancel()


if __name__ == '__main__':
    main()
