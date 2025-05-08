variable "hcloud_token" {
  description = "Hetzner Cloud API Token"
  type        = string
  sensitive   = true
}

variable "servers" {
  description = "List of servers with name, location, and server_type"
  type = list(object({
    name        = string
    location    = string
    server_type = string
    arch        = string
    os_image    = string
  }))
}

variable "ssh_pubkeys" {
  description = "SSH public keys to add to the servers"
  type        = list(string)
}


