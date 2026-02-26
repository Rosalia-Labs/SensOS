# Client

This directory contains the client portion of SensOS. It includes [pi-gen](https://github.com/RPi-Distro/pi-gen) as a submodule.

# Basic instructions

To create a bootable image for the pi, there are several commands.

`generate-config.sh` creates a `config` file and places it in the `pi-gen` directory. `pi-gen` is the raspberry pi OS framework for creating boot images.
SensOS is an overlay that adds and addtional stage of construction. The `config` file is part of `pi-gen`, so you can find its documentation there.

```
 ./bin/generate-config.sh --help
Usage: ./bin/generate-config.sh [options]

Options:
  --pi-gen-release <value>                   (default: SensOS reference)
  --pigen-docker-opts <value>                (default: -v /Users/keittth/Desktop/SensOS/client/sensos:/sensos)
  --stage-list <value>                       (default: stage0 stage1 stage2)
  --img-name <value>                         (default: sensos)
  --timezone-default <value>                 (default: UTC)
  --keyboard-keymap <value>                  (default: us)
  --keyboard-layout <value>                  (default: English (US))
  --locale-default <value>                   (default: C.UTF-8)
  --first-user-name <value>                  (default: sensos)
  --first-user-pass <value>                  (default: sensos)
  --disable-first-boot-user-rename <0|1>     (default: 1)
  --wpa-country <value>                      (default: US)
  --deploy-compression <value>               (default: none)
  --enable-wifi-ap                           Enable AP (default: disabled)
  --image-size <value>                       (default: 8192 MB)
  --help                                     Display this help message
```

Once the `config` file is generated, call `create-boot-image.sh` to run `pi-gen`.

```
./bin/create-boot-image.sh --help
Usage: ./bin/create-boot-image.sh [OPTIONS]

Options:
  --remove-existing-images       Delete the 'deploy' directory before building
  --build-docker-images          Build and store docker images for offline use
  --continue                     Continue from a previously interrupted build
  -h, --help                     Show this help message a
```

You can use `burn-boot-image.sh` or any other software to copy the resulting image onto an SD card or other bootable media.

# On-device configuration order

After first boot, SensOS is configured by running scripts on the device. It is not intended to be "boot-and-run" without configuration.

Recommended sequence:

1. Configure network/WireGuard first (writes `/sensos/etc/network.conf` and firewall include rules).
2. Configure storage next (mounts/initializes `/sensos/data`).
3. Configure Docker settings (writes `/sensos/docker/.env`).
4. Build/load container images if needed.
5. Start container services.

Example flow:

```bash
config-network --config-server <server-ip> --port 8765 --network sensos --subnet 1 --wg-endpoint <endpoint-ip>
config-storage --device /dev/<your-disk>
config-docker --start-service true
```

Notes:

- `config-docker` is the step that writes runtime container configuration, including dashboard settings such as `DASHBOARD_PORT`, `DASHBOARD_USER`, and `DASHBOARD_PASSWORD`.
- If container images are not already loaded from tarballs, run `build-containers` before starting `sensos-container.service`.
