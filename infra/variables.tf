# Tytan LendingOps & MemberAssist - Terraform Variables

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "dataset_name" {
  description = "BigQuery dataset name"
  type        = string
  default     = "tytan_lending_ops"
}

variable "bucket_name_prefix" {
  description = "Cloud Storage bucket name prefix"
  type        = string
  default     = "tytan-lending-docs"
}

variable "docai_identity_processor_id" {
  description = "Document AI Identity Processor ID (leave empty for mock mode)"
  type        = string
  default     = ""
}

variable "docai_form_processor_id" {
  description = "Document AI Form Parser Processor ID (leave empty for mock mode)"
  type        = string
  default     = ""
}

variable "alert_email" {
  description = "Email for monitoring alerts"
  type        = string
  default     = ""
}

variable "api_min_instances" {
  description = "Minimum instances for API service"
  type        = number
  default     = 1
}

variable "api_max_instances" {
  description = "Maximum instances for API service"
  type        = number
  default     = 100
}

variable "worker_min_instances" {
  description = "Minimum instances for worker service"
  type        = number
  default     = 0
}

variable "worker_max_instances" {
  description = "Maximum instances for worker service"
  type        = number
  default     = 50
}

variable "webhook_min_instances" {
  description = "Minimum instances for webhook service"
  type        = number
  default     = 0
}

variable "webhook_max_instances" {
  description = "Maximum instances for webhook service"
  type        = number
  default     = 20
}

variable "enable_audit_logging" {
  description = "Enable comprehensive audit logging"
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Log retention in days (7 years = 2555 days)"
  type        = number
  default     = 2555
}
