"""Package installers for hot-loading.

The `IPackageInstaller` port had no implementation at all until #958, which is
why `hangar_load` could never succeed: bootstrap passed `installers=[]`.

`uvx` and `npx` need no install step -- they fetch and run in one go -- so an
installer here resolves a `PackageInfo` to the command that runs it and answers
whether its runtime is on PATH. That is why `InstalledPackage.install_path` is
documented as `None for npx/uvx`: there is no path, and nothing to clean up.

Deliberately not here: `oci` and `mcpb`. An OCI package means pulling an image
and running container mode, which needs a container runtime the shipped Hangar
image does not carry (see `config.yaml.example`); `mcpb` has no defined install
path. Both are absent rather than half-present, so `PackageResolver` reports
them unavailable and the caller is told which runtimes it does have.
"""

from .command_runtime import CommandRuntimeInstaller, NpxInstaller, UvxInstaller, runtime_availability

__all__ = [
    "CommandRuntimeInstaller",
    "NpxInstaller",
    "UvxInstaller",
    "runtime_availability",
]
