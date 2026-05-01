# SensOS

SensOS is organized as multiple focused repositories.
This repository is the umbrella landing page and historical archive.

## Project Context And Rationale

SensOS supports a client-server model for environmental sensing.
Field devices run the client runtime to collect and forward observations, while a centralized server provides fleet management, configuration control, data aggregation, and reporting.

This architecture is designed for building sensing networks, not just single devices.
Centralized coordination improves consistency across deployments and makes it practical to compare conditions across sites over time.

For broader scientific context on networked sensing and distributed observation systems, see Keitt and Abelson in *Science*.

## Start Here

- Server/control plane: [Rosalia-Labs/sensos-server](https://github.com/Rosalia-Labs/sensos-server)
- Client/runtime software: [Rosalia-Labs/sensos-client](https://github.com/Rosalia-Labs/sensos-client)
- Image build pipeline (Pi-gen): [Rosalia-Labs/sensos-pigen](https://github.com/Rosalia-Labs/sensos-pigen)

## Recommended Paths

### Deploy or operate a server

1. Go to `sensos-server`
2. Follow the docs and command guides in that repository

### Configure and run a client device

1. Go to `sensos-client`
2. Follow setup and command docs in that repository

### Build test or deployment images

1. Go to `sensos-pigen`
2. Follow image build workflow in that repository

## Migration And History

- Historical pre-split SensOS code is preserved at tag:
  - `umbrella-cutover-2026-05-01`
- This umbrella repo no longer contains active runtime code.

## Contributing

Contribute in the repo that matches your change scope:

- server behavior/API/UI -> `sensos-server`
- client setup/runtime/device commands -> `sensos-client`
- image build and provisioning stages -> `sensos-pigen`
