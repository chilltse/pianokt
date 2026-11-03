# Opt-in only; never schedules exports or alters relay/lake schedules.
variable "enable_export_job" {
  type    = bool
  default = false
}
resource "google_service_account" "export" {
  count      = var.enable_export_job ? 1 : 0
  account_id = "pianokt-export"
}
resource "google_secret_manager_secret_iam_member" "export" {
  count     = var.enable_export_job ? 1 : 0
  secret_id = var.database_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.export[0].email}"
}
resource "google_storage_bucket_iam_member" "export_read" {
  count  = var.enable_export_job ? 1 : 0
  bucket = google_storage_bucket.data["raw"].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.export[0].email}"
}
resource "google_storage_bucket_iam_member" "export_create" {
  count  = var.enable_export_job ? 1 : 0
  bucket = google_storage_bucket.data["raw"].name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.export[0].email}"
  condition {
    title      = "datasets-only"
    expression = "resource.name.startsWith('projects/_/buckets/${google_storage_bucket.data["raw"].name}/objects/datasets/')"
  }
}
resource "google_cloud_run_v2_job" "export" {
  count               = var.enable_export_job ? 1 : 0
  name                = "pianokt-export"
  location            = var.region
  deletion_protection = true
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.export[0].email
      timeout         = "1800s"
      max_retries     = 0
      containers {
        image   = var.image
        command = ["pianokt-data"]
        # Deliberately inert until an operator supplies cutoff and a new prefix.
        args = ["export-attempts", "--help"]
        resources { limits = { cpu = "2", memory = "4Gi" } }
        env {
          name  = "RAW_ROOT"
          value = "gs://${google_storage_bucket.data["raw"].name}"
        }
        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = var.database_secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }
  depends_on = [google_project_service.enabled, google_secret_manager_secret_iam_member.export]
}
