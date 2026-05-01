# SensOS

SensOS is now organized as multiple focused repositories.
This repository is the umbrella landing page and historical archive.

## Start Here

- Server/control plane: `../sensos-server`
- Client/runtime software: `../sensos-client`
- Image build pipeline (Pi-gen): `../sensos-pigen`

## Recommended Paths

### Deploy or operate a server

1. Go to `sensos-server`
2. Follow server docs and command guides under `sensos-server/docs/`

### Configure and run a client device

1. Go to `sensos-client`
2. Follow client setup and command docs under `sensos-client/docs/`

### Build test or deployment images

1. Go to `sensos-pigen`
2. Follow image build workflow in that repo

## Migration And History

- Historical pre-split SensOS code is preserved at tag:
  - `umbrella-cutover-2026-05-01`
- This umbrella repo no longer contains active runtime code.

## Contributing

Contribute in the repo that matches your change scope:

- server behavior/API/UI -> `sensos-server`
- client setup/runtime/device commands -> `sensos-client`
- image build and provisioning stages -> `sensos-pigen`
