# SensOS

**SensOS** is an operating system overlay designed to orchestrate fleets of Raspberry Pi computers.  
It is developed by [Rosalia Labs, LLC](https://rosalialabs.com) to support applications in environmental sensing and biodiversity monitoring, where robust, scalable deployments of low-power computing are essential.

SensOS works for building custom Raspberry Pi OS images today, but it’s still early — we’re expanding features and documentation.

If SensOS informs your work, please cite the project and acknowledge contributors.

---

## Purpose

SensOS extends the [Raspberry Pi OS build framework](https://github.com/RPi-Distro/pi-gen) with additional stages and tooling for:

- Coordinated deployment of multiple Raspberry Pi devices
- Streamlined configuration for field and laboratory environments
- Support for applications in ecological research, conservation, and sensing networks

Although created for environmental monitoring, SensOS is a general-purpose system overlay and may be useful in other distributed or embedded computing contexts.

---

## Goals

- **Reliable orchestration** of Raspberry Pi devices at scale
- **Reproducible builds** with minimal manual configuration
- **Extensibility** for custom sensing and data workflows
- **Accessibility** for researchers, practitioners, and developers

---

## Getting Started

Clone the repository and initialize its submodules (SensOS depends on [`pi-gen`](https://github.com/RPi-Distro/pi-gen), included here as a submodule and pinned to a tagged release):

```bash
# Clone including submodules
git clone --recurse-submodules https://github.com/Rosalia-Labs/SensOS.git
cd SensOS

# If you cloned without --recurse-submodules, run:
git submodule update --init --recursive
```

Once cloned, you can use the included scripts (e.g. `./bin/generate-config.sh`) to generate a configuration and build a bootable Raspberry Pi image.

---

## Contributing

SensOS is open to contributions. We welcome:

- Improvements to documentation and tooling
- Extensions for new sensing hardware
- Feedback from real-world deployments

If you are interested in contributing, please open an issue or submit a pull request. For major changes, we encourage discussing ideas in advance to ensure alignment.

---

## License

This code is copyright © Rosalia Labs, LLC.  
Distributed under the terms described in the [LICENSE](./LICENSE) file.
