## VM Spawner

This tool is for enabling a better manual debugging process.
We have two commands:

`cvm` -> Which creates a VM on a cloud company, right now only Hetzner is supported
`kvm` -> Which uses libvirt and ssh to spawn self hosted VMs


## cvm Examples

Create a VM on Hetzner:

```bash
cvm c -m "milo|x86_64"
```

Destroy all `cvm` created machines:
```bash
cvm d
```

### Assumptions
- Assumes you have a Hetzner account and asks you for the API token.
- API token is set in TF_VAR_hcloud_token env var


## kvm Examples

Create a VM on clan.lol:
```bash
kvm c
```

Destroy a VM on clan.lol:
```bash
kvm d --name ubuntu-b0cd069b-7e55-415e-89f3-6c9a08b252c1
```

### Assumptions

- Your ssh pubkey is [whitelisted](https://git.clan.lol/clan/clan-infra/src/branch/main/modules/web01/hypervisor.nix) on the `kvm` user in clan.lol