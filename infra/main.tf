# Tytan LendingOps & MemberAssist - Main Terraform Configuration

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Local variables
locals {
  bucket_name = "${var.bucket_name_prefix}-${var.environment}"
  mock_mode   = var.docai_identity_processor_id == "" ? "true" : "false"

  common_labels = {
    environment = var.environment
    solution    = "tytan-lending-ops"
    managed_by  = "terraform"
  }
}

# ====================================================================
# SERVICE ACCOUNTS
# ====================================================================

resource "google_service_account" "api_sa" {
  account_id   = "tytan-api-sa"
  display_name = "Tytan API Service Account"
  description  = "Service account for Cloud Run API service"
}

resource "google_service_account" "worker_sa" {
  account_id   = "tytan-worker-sa"
  display_name = "Tytan Document AI Worker Service Account"
  description  = "Service account for Document AI worker"
}

resource "google_service_account" "webhook_sa" {
  account_id   = "tytan-webhook-sa"
  display_name = "Tytan Dialogflow Webhook Service Account"
  description  = "Service account for Dialogflow webhook"
}

# ====================================================================
# CLOUD STORAGE
# ====================================================================

resource "google_storage_bucket" "documents" {
  name          = local.bucket_name
  location      = var.region
  force_destroy = var.environment != "prod" # Prevent accidental deletion in prod

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = var.log_retention_days
    }
    action {
      type = "Delete"
    }
  }

  labels = local.common_labels
}

# Grant API service account permissions to upload documents
resource "google_storage_bucket_iam_member" "api_object_creator" {
  bucket = google_storage_bucket.documents.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_storage_bucket_iam_member" "api_object_viewer" {
  bucket = google_storage_bucket.documents.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.api_sa.email}"
}

# Grant worker service account read-only access
resource "google_storage_bucket_iam_member" "worker_object_viewer" {
  bucket = google_storage_bucket.documents.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker_sa.email}"
}

# ====================================================================
# BIGQUERY
# ====================================================================

resource "google_bigquery_dataset" "lending_ops" {
  dataset_id                 = var.dataset_name
  location                   = var.region
  delete_contents_on_destroy = var.environment != "prod"

  labels = local.common_labels
}

# Table: cases
resource "google_bigquery_table" "cases" {
  dataset_id          = google_bigquery_dataset.lending_ops.dataset_id
  table_id            = "cases"
  deletion_protection = var.environment == "prod"

  time_partitioning {
    type          = "DAY"
    field         = "created_at"
    expiration_ms = var.log_retention_days * 24 * 60 * 60 * 1000
  }

  clustering = ["status", "loan_type"]

  schema = <<EOF
[
  {"name": "case_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "member_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "loan_type", "type": "STRING", "mode": "REQUIRED"},
  {"name": "loan_amount", "type": "NUMERIC", "mode": "REQUIRED"},
  {"name": "status", "type": "STRING", "mode": "REQUIRED"},
  {"name": "created_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
  {"name": "updated_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
  {"name": "member_contact_email", "type": "STRING", "mode": "NULLABLE"},
  {"name": "member_contact_phone", "type": "STRING", "mode": "NULLABLE"},
  {"name": "source_channel", "type": "STRING", "mode": "NULLABLE"},
  {"name": "metadata", "type": "JSON", "mode": "NULLABLE"}
]
EOF

  labels = local.common_labels
}

# Table: documents
resource "google_bigquery_table" "documents" {
  dataset_id          = google_bigquery_dataset.lending_ops.dataset_id
  table_id            = "documents"
  deletion_protection = var.environment == "prod"

  time_partitioning {
    type          = "DAY"
    field         = "uploaded_at"
    expiration_ms = var.log_retention_days * 24 * 60 * 60 * 1000
  }

  clustering = ["case_id", "document_type"]

  schema = <<EOF
[
  {"name": "document_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "case_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "document_type", "type": "STRING", "mode": "REQUIRED"},
  {"name": "gcs_uri", "type": "STRING", "mode": "REQUIRED"},
  {"name": "file_size_bytes", "type": "INT64", "mode": "NULLABLE"},
  {"name": "mime_type", "type": "STRING", "mode": "NULLABLE"},
  {"name": "uploaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
  {"name": "status", "type": "STRING", "mode": "REQUIRED"},
  {"name": "file_hash_sha256", "type": "STRING", "mode": "NULLABLE"}
]
EOF

  labels = local.common_labels
}

# Table: extracted_fields
resource "google_bigquery_table" "extracted_fields" {
  dataset_id          = google_bigquery_dataset.lending_ops.dataset_id
  table_id            = "extracted_fields"
  deletion_protection = var.environment == "prod"

  time_partitioning {
    type          = "DAY"
    field         = "extracted_at"
    expiration_ms = var.log_retention_days * 24 * 60 * 60 * 1000
  }

  clustering = ["case_id", "field_name"]

  schema = <<EOF
[
  {"name": "extraction_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "case_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "document_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "field_name", "type": "STRING", "mode": "REQUIRED"},
  {"name": "value", "type": "STRING", "mode": "NULLABLE"},
  {"name": "confidence", "type": "FLOAT64", "mode": "NULLABLE"},
  {"name": "page_number", "type": "INT64", "mode": "NULLABLE"},
  {"name": "bounding_box", "type": "JSON", "mode": "NULLABLE"},
  {"name": "extracted_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
  {"name": "processor_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "is_corrected", "type": "BOOLEAN", "mode": "NULLABLE"}
]
EOF

  labels = local.common_labels
}

# Table: field_corrections
resource "google_bigquery_table" "field_corrections" {
  dataset_id          = google_bigquery_dataset.lending_ops.dataset_id
  table_id            = "field_corrections"
  deletion_protection = var.environment == "prod"

  time_partitioning {
    type  = "DAY"
    field = "review_timestamp"
  }

  schema = <<EOF
[
  {"name": "correction_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "extraction_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "case_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "document_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "field_name", "type": "STRING", "mode": "REQUIRED"},
  {"name": "original_value", "type": "STRING", "mode": "NULLABLE"},
  {"name": "corrected_value", "type": "STRING", "mode": "NULLABLE"},
  {"name": "reviewer_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "review_timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"},
  {"name": "correction_reason", "type": "STRING", "mode": "NULLABLE"}
]
EOF

  labels = local.common_labels
}

# Table: audit_log
resource "google_bigquery_table" "audit_log" {
  dataset_id          = google_bigquery_dataset.lending_ops.dataset_id
  table_id            = "audit_log"
  deletion_protection = true # Always protect audit logs

  time_partitioning {
    type          = "DAY"
    field         = "timestamp"
    expiration_ms = (var.log_retention_days + 1095) * 24 * 60 * 60 * 1000 # 7 years + 3 years buffer
  }

  clustering = ["case_id", "event_type"]

  schema = <<EOF
[
  {"name": "event_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "case_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "event_type", "type": "STRING", "mode": "REQUIRED"},
  {"name": "actor", "type": "STRING", "mode": "REQUIRED"},
  {"name": "timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"},
  {"name": "payload", "type": "JSON", "mode": "NULLABLE"},
  {"name": "ip_address", "type": "STRING", "mode": "NULLABLE"},
  {"name": "user_agent", "type": "STRING", "mode": "NULLABLE"}
]
EOF

  labels = local.common_labels
}

# Grant API service account BigQuery permissions
resource "google_bigquery_dataset_iam_member" "api_data_editor" {
  dataset_id = google_bigquery_dataset.lending_ops.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_project_iam_member" "api_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.api_sa.email}"
}

# Grant worker service account BigQuery permissions
resource "google_bigquery_dataset_iam_member" "worker_data_editor" {
  dataset_id = google_bigquery_dataset.lending_ops.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.worker_sa.email}"
}

resource "google_project_iam_member" "worker_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.worker_sa.email}"
}

# Grant webhook service account read-only BigQuery permissions
resource "google_bigquery_dataset_iam_member" "webhook_data_viewer" {
  dataset_id = google_bigquery_dataset.lending_ops.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.webhook_sa.email}"
}

resource "google_project_iam_member" "webhook_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.webhook_sa.email}"
}

# ====================================================================
# PUB/SUB
# ====================================================================

# Topic: document.uploaded
resource "google_pubsub_topic" "document_uploaded" {
  name = "document-uploaded"

  labels = local.common_labels
}

# Subscription for Document AI worker
resource "google_pubsub_subscription" "document_ai_worker" {
  name  = "document-ai-worker-sub"
  topic = google_pubsub_topic.document_uploaded.name

  ack_deadline_seconds = 600 # 10 minutes for Document AI processing

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.document_uploaded_dlq.id
    max_delivery_attempts = 5
  }

  labels = local.common_labels
}

# Dead Letter Queue topic
resource "google_pubsub_topic" "document_uploaded_dlq" {
  name = "document-uploaded-dlq"

  labels = local.common_labels
}

# Topic: extraction.completed
resource "google_pubsub_topic" "extraction_completed" {
  name = "extraction-completed"

  labels = local.common_labels
}

# Grant API service account Pub/Sub publisher permissions
resource "google_pubsub_topic_iam_member" "api_publisher" {
  topic  = google_pubsub_topic.document_uploaded.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.api_sa.email}"
}

# Grant worker service account Pub/Sub subscriber permissions
resource "google_pubsub_subscription_iam_member" "worker_subscriber" {
  subscription = google_pubsub_subscription.document_ai_worker.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.worker_sa.email}"
}

# Grant worker service account Pub/Sub publisher permissions for extraction.completed
resource "google_pubsub_topic_iam_member" "worker_publisher" {
  topic  = google_pubsub_topic.extraction_completed.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.worker_sa.email}"
}

# Grant worker access to DLQ
resource "google_pubsub_topic_iam_member" "worker_dlq_publisher" {
  topic  = google_pubsub_topic.document_uploaded_dlq.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.worker_sa.email}"
}

# ====================================================================
# CLOUD RUN - API SERVICE
# ====================================================================

resource "google_cloud_run_v2_service" "api" {
  name     = "tytan-lending-api"
  location = var.region

  template {
    service_account = google_service_account.api_sa.email

    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    containers {
      image = "gcr.io/cloudrun/placeholder" # Replace during deployment

      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "REGION"
        value = var.region
      }

      env {
        name  = "DATASET_ID"
        value = var.dataset_name
      }

      env {
        name  = "BUCKET_NAME"
        value = local.bucket_name
      }

      env {
        name  = "PUBSUB_TOPIC"
        value = "document-uploaded"
      }

      env {
        name  = "MOCK_MODE"
        value = local.mock_mode
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      ports {
        container_port = 8080
      }
    }
  }

  labels = local.common_labels
}

# Allow unauthenticated access (for demo/testing - remove for production)
resource "google_cloud_run_v2_service_iam_member" "api_invoker" {
  name   = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role   = "roles/run.invoker"
  member = "allUsers"
}

# ====================================================================
# CLOUD RUN - DOCUMENT AI WORKER
# ====================================================================

resource "google_cloud_run_v2_service" "worker" {
  name     = "tytan-lending-docai-worker"
  location = var.region

  template {
    service_account = google_service_account.worker_sa.email

    scaling {
      min_instance_count = var.worker_min_instances
      max_instance_count = var.worker_max_instances
    }

    containers {
      image = "gcr.io/cloudrun/placeholder" # Replace during deployment

      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "REGION"
        value = var.region
      }

      env {
        name  = "DATASET_ID"
        value = var.dataset_name
      }

      env {
        name  = "SUBSCRIPTION_ID"
        value = "document-ai-worker-sub"
      }

      env {
        name  = "MOCK_MODE"
        value = local.mock_mode
      }

      env {
        name  = "DOCAI_IDENTITY_PROCESSOR"
        value = var.docai_identity_processor_id
      }

      env {
        name  = "DOCAI_FORM_PROCESSOR"
        value = var.docai_form_processor_id
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
      }
    }
  }

  labels = local.common_labels
}

# Grant worker service account Document AI permissions
resource "google_project_iam_member" "worker_documentai_user" {
  count   = var.docai_identity_processor_id != "" ? 1 : 0
  project = var.project_id
  role    = "roles/documentai.apiUser"
  member  = "serviceAccount:${google_service_account.worker_sa.email}"
}

# ====================================================================
# CLOUD RUN - DIALOGFLOW WEBHOOK
# ====================================================================

resource "google_cloud_run_v2_service" "webhook" {
  name     = "tytan-lending-webhook"
  location = var.region

  template {
    service_account = google_service_account.webhook_sa.email

    scaling {
      min_instance_count = var.webhook_min_instances
      max_instance_count = var.webhook_max_instances
    }

    containers {
      image = "gcr.io/cloudrun/placeholder" # Replace during deployment

      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "DATASET_ID"
        value = var.dataset_name
      }

      env {
        name  = "MOCK_MODE"
        value = local.mock_mode
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      ports {
        container_port = 8080
      }
    }
  }

  labels = local.common_labels
}

# Allow Dialogflow to invoke webhook
resource "google_cloud_run_v2_service_iam_member" "webhook_invoker" {
  name     = google_cloud_run_v2_service.webhook.name
  location = google_cloud_run_v2_service.webhook.location
  role     = "roles/run.invoker"
  member   = "allUsers" # In production, restrict to Dialogflow service account
}

# ====================================================================
# LOGGING
# ====================================================================

resource "google_logging_project_sink" "audit_export" {
  count = var.enable_audit_logging ? 1 : 0

  name        = "tytan-audit-log-export"
  destination = "storage.googleapis.com/${google_storage_bucket.audit_logs[0].name}"

  filter = <<-EOT
    logName:"cloudaudit.googleapis.com" OR
    protoPayload.serviceName="bigquery.googleapis.com" OR
    protoPayload.serviceName="storage.googleapis.com"
  EOT

  unique_writer_identity = true
}

resource "google_storage_bucket" "audit_logs" {
  count = var.enable_audit_logging ? 1 : 0

  name          = "${local.bucket_name}-audit-logs"
  location      = var.region
  storage_class = "ARCHIVE"
  force_destroy = var.environment != "prod"

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = var.log_retention_days + 1095 # 7 years + 3 years buffer
    }
  }

  labels = local.common_labels
}

# Grant logging sink permission to write to audit bucket
resource "google_storage_bucket_iam_member" "audit_log_writer" {
  count = var.enable_audit_logging ? 1 : 0

  bucket = google_storage_bucket.audit_logs[0].name
  role   = "roles/storage.objectCreator"
  member = google_logging_project_sink.audit_export[0].writer_identity
}
