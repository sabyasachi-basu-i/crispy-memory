# Tytan LendingOps & MemberAssist - Terraform Outputs

output "api_url" {
  description = "Cloud Run API service URL"
  value       = google_cloud_run_v2_service.api.uri
}

output "webhook_url" {
  description = "Dialogflow webhook URL"
  value       = google_cloud_run_v2_service.webhook.uri
}

output "worker_service_name" {
  description = "Document AI worker service name"
  value       = google_cloud_run_v2_service.worker.name
}

output "bucket_name" {
  description = "Cloud Storage bucket for documents"
  value       = google_storage_bucket.documents.name
}

output "dataset_id" {
  description = "BigQuery dataset ID"
  value       = google_bigquery_dataset.lending_ops.dataset_id
}

output "pubsub_topic" {
  description = "Pub/Sub topic for document uploads"
  value       = google_pubsub_topic.document_uploaded.name
}

output "pubsub_subscription" {
  description = "Pub/Sub subscription for worker"
  value       = google_pubsub_subscription.document_ai_worker.name
}

output "api_service_account" {
  description = "API service account email"
  value       = google_service_account.api_sa.email
}

output "worker_service_account" {
  description = "Worker service account email"
  value       = google_service_account.worker_sa.email
}

output "webhook_service_account" {
  description = "Webhook service account email"
  value       = google_service_account.webhook_sa.email
}

output "mock_mode" {
  description = "Whether the system is running in mock mode"
  value       = local.mock_mode
}

output "deployment_summary" {
  description = "Summary of deployed resources"
  value = {
    environment     = var.environment
    project_id      = var.project_id
    region          = var.region
    api_url         = google_cloud_run_v2_service.api.uri
    webhook_url     = google_cloud_run_v2_service.webhook.uri
    bucket          = google_storage_bucket.documents.name
    dataset         = google_bigquery_dataset.lending_ops.dataset_id
    mock_mode       = local.mock_mode
  }
}
