terraform {
  required_version = ">= 1.6"
  required_providers {
    google      = { source = "hashicorp/google", version = "~> 6.0" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 6.0" }
  }
  backend "gcs" {}
}
variable "project_id" { type = string }
variable "region" { type = string }
variable "image" { type = string }
variable "spark_image" { type = string }
variable "frontend_origin" { type = string }
variable "supabase_url" { type = string }
variable "supabase_anon_key" { type = string }
variable "database_secret_id" { type = string }
variable "schedules_paused" {
  type    = bool
  default = true
}
provider "google" {
  project = var.project_id
  region  = var.region
}
provider "google-beta" {
  project = var.project_id
  region  = var.region
}
resource "google_project_service" "enabled" {
  for_each           = toset(["run.googleapis.com", "pubsub.googleapis.com", "storage.googleapis.com", "cloudscheduler.googleapis.com", "secretmanager.googleapis.com", "iamcredentials.googleapis.com", "artifactregistry.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}
resource "google_artifact_registry_repository" "images" {
  repository_id = "pianokt"
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.enabled]
}
resource "google_service_account" "runtime" {
  for_each   = toset(["api", "worker", "relay", "pipeline", "scheduler", "push"])
  account_id = "pianokt-${each.value}"
}
resource "google_storage_bucket" "data" {
  for_each                    = toset(["raw", "lake", "checkpoint"])
  name                        = "${var.project_id}-pianokt-${each.value}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  dynamic "cors" {
    for_each = each.key == "raw" ? [1] : []
    content {
      origin          = [var.frontend_origin]
      method          = ["PUT", "GET", "HEAD"]
      response_header = ["Content-Type", "ETag", "x-goog-generation", "x-goog-if-generation-match"]
      max_age_seconds = 3600
    }
  }
}
resource "google_storage_bucket_iam_member" "raw_read" {
  for_each = toset(["api", "worker", "pipeline"])
  bucket   = google_storage_bucket.data["raw"].name
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${google_service_account.runtime[each.value].email}"
}
resource "google_storage_bucket_iam_member" "raw_create" {
  for_each = toset(["api", "worker"])
  bucket   = google_storage_bucket.data["raw"].name
  role     = "roles/storage.objectCreator"
  member   = "serviceAccount:${google_service_account.runtime[each.value].email}"
}
resource "google_storage_bucket_iam_member" "tables" {
  for_each = toset(["lake", "checkpoint"])
  bucket   = google_storage_bucket.data[each.value].name
  role     = "roles/storage.objectAdmin"
  member   = "serviceAccount:${google_service_account.runtime["pipeline"].email}"
}
resource "google_service_account_iam_member" "sign" {
  service_account_id = google_service_account.runtime["api"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.runtime["api"].email}"
}
resource "google_secret_manager_secret_iam_member" "database" {
  for_each  = toset(["api", "worker", "relay", "pipeline"])
  secret_id = var.database_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime[each.value].email}"
}
resource "google_pubsub_topic" "events" {
  name       = "pianokt-events"
  depends_on = [google_project_service.enabled]
}
resource "google_pubsub_topic" "dead" {
  name       = "pianokt-dead-letter"
  depends_on = [google_project_service.enabled]
}
resource "google_pubsub_subscription" "dead" {
  name                       = "pianokt-dead-letter-inspection"
  topic                      = google_pubsub_topic.dead.id
  message_retention_duration = "604800s"
}
resource "google_pubsub_topic_iam_member" "relay" {
  topic  = google_pubsub_topic.events.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.runtime["relay"].email}"
}
resource "google_project_service_identity" "pubsub" {
  provider   = google-beta
  service    = "pubsub.googleapis.com"
  depends_on = [google_project_service.enabled]
}
resource "google_storage_bucket_iam_member" "archive_create" {
  bucket = google_storage_bucket.data["raw"].name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
resource "google_storage_bucket_iam_member" "archive_bucket" {
  bucket = google_storage_bucket.data["raw"].name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
resource "google_pubsub_subscription" "archive" {
  name  = "pianokt-events-archive"
  topic = google_pubsub_topic.events.id
  expiration_policy { ttl = "" }
  cloud_storage_config {
    bucket          = google_storage_bucket.data["raw"].name
    filename_prefix = "events/"
    filename_suffix = ".jsonl"
    max_duration    = "60s"
    text_config {}
  }
  depends_on = [google_storage_bucket_iam_member.archive_create, google_storage_bucket_iam_member.archive_bucket]
}
resource "google_cloud_run_v2_service" "app" {
  for_each            = toset(["api", "worker"])
  name                = "pianokt-${each.value}"
  location            = var.region
  deletion_protection = true
  template {
    service_account                  = google_service_account.runtime[each.value].email
    timeout                          = "540s"
    max_instance_request_concurrency = each.value == "worker" ? 1 : 20
    scaling { max_instance_count = 3 }
    containers {
      image   = var.image
      command = ["uvicorn"]
      args    = [each.value == "api" ? "pianokt_backend.api:app" : "pianokt_backend.worker_api:app", "--host", "0.0.0.0", "--port", "8080"]
      resources { limits = { cpu = "2", memory = "2Gi" } }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = var.database_secret_id
            version = "latest"
          }
        }
      }
      dynamic "env" {
        for_each = { RAW_ROOT = "gs://${google_storage_bucket.data["raw"].name}", SUPABASE_URL = var.supabase_url, SUPABASE_ANON_KEY = var.supabase_anon_key, FRONTEND_ORIGINS = var.frontend_origin, SIGNING_SERVICE_ACCOUNT = google_service_account.runtime["api"].email }
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [google_project_service.enabled, google_secret_manager_secret_iam_member.database]
}
resource "google_cloud_run_v2_service_iam_member" "api" {
  name     = google_cloud_run_v2_service.app["api"].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
resource "google_cloud_run_v2_service_iam_member" "worker" {
  name     = google_cloud_run_v2_service.app["worker"].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime["push"].email}"
}
resource "google_service_account_iam_member" "push_token" {
  service_account_id = google_service_account.runtime["push"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
resource "google_pubsub_topic_iam_member" "dead_publish" {
  topic  = google_pubsub_topic.dead.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
resource "google_pubsub_subscription" "worker" {
  name                 = "pianokt-alignment-worker"
  topic                = google_pubsub_topic.events.id
  filter               = "attributes.event_type = \"performance.uploaded\""
  ack_deadline_seconds = 600
  expiration_policy { ttl = "" }
  push_config {
    push_endpoint = "${google_cloud_run_v2_service.app["worker"].uri}/pubsub"
    oidc_token { service_account_email = google_service_account.runtime["push"].email }
  }
  retry_policy {
    minimum_backoff = "30s"
    maximum_backoff = "600s"
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead.id
    max_delivery_attempts = 5
  }
}
resource "google_pubsub_subscription_iam_member" "dead_subscribe" {
  subscription = google_pubsub_subscription.worker.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
resource "google_cloud_run_v2_job" "job" {
  for_each            = toset(["relay", "pipeline"])
  name                = "pianokt-${each.value}"
  location            = var.region
  deletion_protection = true
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.runtime[each.value].email
      timeout         = "1800s"
      max_retries     = 2
      containers {
        image   = each.value == "pipeline" ? var.spark_image : var.image
        command = ["pianokt-data"]
        args    = [each.value]
        resources { limits = { cpu = "2", memory = "4Gi" } }
        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = var.database_secret_id
              version = "latest"
            }
          }
        }
        dynamic "env" {
          for_each = { RAW_ROOT = "gs://${google_storage_bucket.data["raw"].name}", LAKE_ROOT = "gs://${google_storage_bucket.data["lake"].name}", CHECKPOINT_ROOT = "gs://${google_storage_bucket.data["checkpoint"].name}", EVENT_TOPIC = google_pubsub_topic.events.id, ENABLE_SPARK_EVENTS = "1" }
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }
  depends_on = [google_project_service.enabled, google_secret_manager_secret_iam_member.database]
}
resource "google_cloud_run_v2_job_iam_member" "scheduler" {
  for_each = google_cloud_run_v2_job.job
  name     = each.value.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime["scheduler"].email}"
}
resource "google_cloud_scheduler_job" "job" {
  for_each  = google_cloud_run_v2_job.job
  name      = "pianokt-${each.key}"
  schedule  = each.key == "relay" ? "0 2 * * *" : "0 3 * * *"
  time_zone = "Asia/Tokyo"
  paused    = var.schedules_paused
  http_target {
    uri         = "https://run.googleapis.com/v2/${each.value.id}:run"
    http_method = "POST"
    oauth_token { service_account_email = google_service_account.runtime["scheduler"].email }
  }
  depends_on = [google_project_service.enabled]
}
output "api_url" { value = google_cloud_run_v2_service.app["api"].uri }
