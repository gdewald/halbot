# main.tf (Adjusted for Minimum Viable Cost)

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 4.0"
    }
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

variable "gcp_project_id" {
  description = "The GCP project ID to use."
  type        = string
}
variable "gcp_region" {
  description = "The desired region for the VM instance."
  type        = string
}
variable "vm_user" {
  description = "The non-root user on the VM that will run LM Studio."
  type        = string
  default     = "lmstudio"
}
variable "lms_key_id" {
  description = "LM Studio pre-authenticated key ID."
  type        = string
  sensitive   = true
}
variable "lms_public_key" {
  description = "LM Studio pre-authenticated public key."
  type        = string
  sensitive   = true
}
variable "lms_private_key" {
  description = "LM Studio pre-authenticated private key."
  type        = string
  sensitive   = true
}

resource "google_compute_network" "vpc_net" {
  name = "llmster-vpc"
  auto_create_subnetworks = true
}

# --- THE MINIMAL INSTANCE DEFINITION ---
resource "google_compute_instance" "llmster_vm" {
  name         = "llmster-cpu-min-server"
  # *** CHANGE MADE HERE: Using e2-small for minimum cost ***
  machine_type = "e2-small"

  zone         = "${var.gcp_region}-a"

  boot_disk {
    initialize_params {
      image = "debian-11"
      size  = 50
    }
  }

  network_interface {
    network = google_compute_network.vpc_net.self_link
    access_config {
      # Public IP allocation (optional)
    }
  }

  metadata = {
    startup-script = templatefile("cloud-init-script.sh", {
      vm_user         = var.vm_user
      lms_key_id      = var.lms_key_id
      lms_public_key  = var.lms_public_key
      lms_private_key = var.lms_private_key
    })
  }

  tags = ["http-server"]
}

resource "google_compute_firewall" "llmster-fw" {
  name    = "allow-llmster-access"
  network = google_compute_network.vpc_net.self_link

  allow {
    protocol = "tcp"
    ports    = ["80", "5000"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["http-server"]
}
