"""
Tytan LendingOps & MemberAssist - Dialogflow CX Webhook
Handles chatbot queries for case status
"""

import os
import logging
import json
from datetime import datetime
from flask import Flask, request, jsonify
from google.cloud import bigquery

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Configuration
PROJECT_ID = os.getenv('PROJECT_ID', 'tytan-lending-dev')
DATASET_ID = os.getenv('DATASET_ID', 'tytan_lending_ops')
MOCK_MODE = os.getenv('MOCK_MODE', 'false').lower() == 'true'

# Initialize BigQuery client
try:
    bq_client = bigquery.Client(project=PROJECT_ID)
    logger.info(f"Initialized BigQuery client for project: {PROJECT_ID}")
except Exception as e:
    logger.error(f"Failed to initialize BigQuery client: {e}")
    bq_client = None


def get_case_status(case_id):
    """Query BigQuery for case status and documents"""
    if MOCK_MODE:
        return {
            "case_id": case_id,
            "status": "NEEDS_REVIEW",
            "created_at": "2024-01-15T10:00:00Z",
            "loan_type": "auto",
            "documents_received": ["drivers_license", "paystub"],
            "missing_documents": ["bank_statement_30days"]
        }

    try:
        query = f"""
            SELECT
                c.case_id,
                c.status,
                c.created_at,
                c.loan_type,
                ARRAY_AGG(DISTINCT d.document_type) as documents_received
            FROM `{PROJECT_ID}.{DATASET_ID}.cases` c
            LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.documents` d
                ON c.case_id = d.case_id
            WHERE c.case_id = @case_id
            GROUP BY c.case_id, c.status, c.created_at, c.loan_type
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("case_id", "STRING", case_id)
            ]
        )

        result = bq_client.query(query, job_config=job_config).result()

        if result.total_rows == 0:
            return None

        row = list(result)[0]

        # Determine required documents
        required_docs = get_required_documents(row.loan_type)
        documents_received = row.documents_received or []
        missing_docs = [doc for doc in required_docs if doc not in documents_received]

        return {
            "case_id": case_id,
            "status": row.status,
            "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            "loan_type": row.loan_type,
            "documents_received": documents_received,
            "missing_documents": missing_docs
        }

    except Exception as e:
        logger.error(f"Error querying case status: {e}", exc_info=True)
        return None


def get_required_documents(loan_type):
    """Return list of required documents based on loan type"""
    base_docs = ["drivers_license"]

    if loan_type == "auto":
        return base_docs + ["paystub_recent_2", "bank_statement_30days"]
    elif loan_type == "personal":
        return base_docs + ["paystub_recent_2", "bank_statement_60days"]
    elif loan_type == "mortgage":
        return base_docs + ["paystub_recent_2", "w2_2years", "bank_statement_60days", "tax_returns_2years"]
    else:
        return base_docs + ["paystub_recent_2", "bank_statement_30days"]


def format_status_message(case_data):
    """Format case status into conversational response"""
    if not case_data:
        return "I couldn't find that application. Please check the application number and try again."

    case_id = case_data['case_id']
    status = case_data['status']
    docs_received = case_data.get('documents_received', [])
    missing_docs = case_data.get('missing_documents', [])

    # Map internal status to user-friendly message
    status_messages = {
        "SUBMITTED": "received and is being processed",
        "EXTRACTING": "under review",
        "NEEDS_REVIEW": "under review",
        "READY_FOR_DECISION": "being reviewed by our team",
        "APPROVED": "approved! Congratulations",
        "REJECTED": "been reviewed"
    }

    status_text = status_messages.get(status, "in progress")

    message = f"Your application {case_id} has been {status_text}.\n\n"

    # Document checklist
    if docs_received:
        message += "Documents received:\n"
        for doc in docs_received:
            doc_name = format_document_name(doc)
            message += f"✅ {doc_name}\n"

    if missing_docs:
        message += "\nStill waiting for:\n"
        for doc in missing_docs:
            doc_name = format_document_name(doc)
            message += f"⏳ {doc_name}\n"

    # Timeline estimate
    if status in ["SUBMITTED", "EXTRACTING", "NEEDS_REVIEW"]:
        message += "\nWe'll contact you within 24 hours if we need anything else."
    elif status == "READY_FOR_DECISION":
        message += "\nYou should hear back within 24 hours."

    return message


def format_document_name(doc_type):
    """Convert document type to user-friendly name"""
    doc_names = {
        "drivers_license": "Driver's License",
        "paystub_recent_2": "Recent Paystubs (2 most recent)",
        "bank_statement_30days": "Bank Statement (last 30 days)",
        "bank_statement_60days": "Bank Statement (last 60 days)",
        "w2_2years": "W-2 Forms (last 2 years)",
        "tax_returns_2years": "Tax Returns (last 2 years)",
        "proof_of_insurance": "Proof of Insurance"
    }
    return doc_names.get(doc_type, doc_type.replace('_', ' ').title())


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }), 200


@app.route('/dialogflow-webhook', methods=['POST'])
def dialogflow_webhook():
    """
    Dialogflow CX webhook endpoint

    Request from Dialogflow CX:
    {
      "detectIntentResponseId": "...",
      "intentInfo": {
        "displayName": "check_application_status"
      },
      "sessionInfo": {
        "parameters": {
          "case_id": "CU-2024-00123"
        }
      },
      "fulfillmentInfo": {
        "tag": "get_case_status"
      }
    }
    """
    try:
        req_data = request.get_json()
        logger.info(f"Dialogflow webhook request: {json.dumps(req_data)}")

        # Extract parameters
        session_info = req_data.get('sessionInfo', {})
        parameters = session_info.get('parameters', {})
        fulfillment_info = req_data.get('fulfillmentInfo', {})
        tag = fulfillment_info.get('tag', '')

        # Handle different webhook tags
        if tag == 'get_case_status':
            case_id = parameters.get('case_id')

            if not case_id:
                response_text = "I need your application number to look up your status. It should look like CU-2024-00123."
            else:
                # Query case status
                case_data = get_case_status(case_id)
                response_text = format_status_message(case_data)

            # Build Dialogflow response
            response = {
                "fulfillmentResponse": {
                    "messages": [
                        {
                            "text": {
                                "text": [response_text]
                            }
                        }
                    ]
                },
                "sessionInfo": {
                    "parameters": {
                        "case_status": case_data['status'] if case_data else None,
                        "has_missing_docs": len(case_data.get('missing_documents', [])) > 0 if case_data else False
                    }
                }
            }

            return jsonify(response), 200

        elif tag == 'escalate_to_human':
            case_id = parameters.get('case_id')
            phone = parameters.get('phone', 'the number on file')

            response_text = (
                f"I'm creating a callback request for your application {case_id}. "
                f"A loan officer will call you at {phone} within 2 hours during business hours. "
                f"Is there anything else I can help with?"
            )

            # TODO: Create ticket in CRM system

            response = {
                "fulfillmentResponse": {
                    "messages": [
                        {
                            "text": {
                                "text": [response_text]
                            }
                        }
                    ]
                }
            }

            return jsonify(response), 200

        elif tag == 'get_timeline':
            case_id = parameters.get('case_id')

            # Simple timeline logic (could be more sophisticated)
            response_text = (
                "Based on current volume, we expect to complete initial review by "
                "tomorrow at 3 PM. You'll receive an email when there's an update."
            )

            response = {
                "fulfillmentResponse": {
                    "messages": [
                        {
                            "text": {
                                "text": [response_text]
                            }
                        }
                    ]
                }
            }

            return jsonify(response), 200

        else:
            # Default response
            response = {
                "fulfillmentResponse": {
                    "messages": [
                        {
                            "text": {
                                "text": ["I'm not sure how to help with that. Can you rephrase your question?"]
                            }
                        }
                    ]
                }
            }
            return jsonify(response), 200

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)

        # Return error response to Dialogflow
        error_response = {
            "fulfillmentResponse": {
                "messages": [
                    {
                        "text": {
                            "text": ["I'm having trouble connecting to our system. Please try again in a moment."]
                        }
                    }
                ]
            }
        }
        return jsonify(error_response), 200  # Return 200 to Dialogflow even on error


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
