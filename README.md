# SensOS

**SensOS** is an operating system overlay designed to orchestrate fleets of Raspberry Pi computers.  
It was originally developed by [Rosalia Labs, LLC](https://rosalialabs.com) to support applications in environmental sensing and biodiversity monitoring, where robust, scalable deployments of low-power computing are essential.

If SensOS informs your work, please cite the project and acknowledge contributors.

## Purpose

SensOS extends the [Raspberry Pi OS build framework](https://github.com/RPi-Distro/pi-gen) with additional stages and tooling for:

- Coordinated deployment of multiple Raspberry Pi devices
- Streamlined configuration for field and laboratory environments
- Support for applications in ecological research, conservation, and sensing networks

Although created for environmental monitoring, SensOS is a general-purpose system overlay and may be useful in other distributed or embedded computing contexts.

## Goals

- **Reliable orchestration** of Raspberry Pi devices at scale
- **Reproducible builds** with minimal manual configuration
- **Extensibility** for custom sensing and data workflows
- **Accessibility** for researchers, practitioners, and developers

## Getting Started

The simplest way to begin is by reviewing the documentation and following the workflow for generating a bootable image.  
Step-by-step guides and usage examples are provided in the [project wiki](./wiki) (or documentation site, if separate).

## Contributing

SensOS is open to contributions. We welcome:

- Improvements to documentation and tooling
- Extensions for new sensing hardware
- Feedback from real-world deployments

If you are interested in contributing, please open an issue or submit a pull request. For major changes, we encourage discussing ideas in advance to ensure alignment.

## License

This code is copyright © Rosalia Labs, LLC.  
Distributed under the terms described in the [LICENSE](./LICENSE) file.

---

## Further Documentation

The following section contains the existing quick-start material. This will be migrated into the wiki for easier navigation.

---

SensOS is an OS overlay for orchestrating raspberry pi computers. It was developed for applications in environmental sensing. The code is copyright Rosalia Labs, LLC.

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
  --enable-geekworm-eeprom                   Enable EEPROM update (default: disabled)
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
