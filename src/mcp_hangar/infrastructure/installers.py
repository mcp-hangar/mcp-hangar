"""Package installers for hot-loading.

The `IPackageInstaller` port had no implementation at all until #958, which is
why `hangar_load` could never succeed: bootstrap passed `installers=[]`.

`uvx` and `npx` need no install step -- they fetch and run in one go -- so an
installer here resolves a `PackageInfo` to the command that runs it and answers
whether its runtime is on PATH. That is why `InstalledPackage.install_path` is
documented as `None for npx/uvx`: there is no path, and nothing to clean up.

Deliberately absent: `oci` and `mcpb`. An OCI package means pulling an image and
running container mode, which needs a container runtime the shipped Hangar image
does not carry (see `config.yaml.example`); `mcpb` has no defined install path.
Both are missing rather than half-present, so `PackageResolver` reports them
unavailable and the caller is told which runtimes it does have.
"""

import shutil

from ..application.services.package_resolver import RuntimeAvailability
from ..domain.contracts.installer import InstalledPackage, IPackageInstaller
from ..domain.contracts.registry import PackageInfo
from ..domain.exceptions import InstallationError
from ..domain.value_objects import McpServerMode
from ..logging_config import get_logger

logger = get_logger(__name__)


class CommandRuntimeInstaller:
    """A registry whose runtime downloads and runs a package in one command.

    `uvx` and `npx` both take a package specifier and execute it, caching as a
    side effect. There is nothing to install ahead of time and nothing to remove
    afterwards, so `install()` resolves the command and `uninstall()` is a no-op
    -- honestly, rather than by deleting a directory this class never created.

    Constructed with its arguments rather than subclassed per registry: the two
    it is used for differ only in which binary they call and whether that binary
    needs `-y`.
    """

    def __init__(self, registry_type: str, executable: str, prefix_args: tuple[str, ...] = ()) -> None:
        self._registry_type = registry_type
        self._executable = executable
        self._prefix_args = prefix_args

    @property
    def registry_type(self) -> str:
        return self._registry_type

    def supports(self, registry_type: str) -> bool:
        return registry_type == self._registry_type

    async def install(self, package: PackageInfo) -> InstalledPackage:
        # Re-checked here rather than trusting the bootstrap probe: availability
        # is read once when the resolver is built, and a gateway that has been up
        # for a week may be running on a host where the binary was removed since.
        # Failing here names the missing runtime; failing in `ensure_ready()`
        # names a subprocess that would not start.
        if not self.is_runtime_available():
            raise InstallationError(f"{self._executable} is not on PATH, so {package.identifier} cannot be run")

        # `name@version` is the form both runtimes accept.
        specifier = f"{package.identifier}@{package.version}" if package.version else package.identifier
        command = [self._executable, *self._prefix_args, specifier]
        if package.transport.args:
            command.extend(package.transport.args)

        logger.info(
            "hot_load_package_resolved",
            registry_type=self._registry_type,
            identifier=package.identifier,
            version=package.version,
        )
        return InstalledPackage(
            package_info=package,
            install_path=None,
            command=command,
            mode=McpServerMode.SUBPROCESS,
        )

    async def uninstall(self, installed: InstalledPackage) -> None:
        """Nothing to undo: the runtime owns its own cache."""

    def is_runtime_available(self) -> bool:
        return shutil.which(self._executable) is not None


def uvx_installer() -> CommandRuntimeInstaller:
    """PyPI packages, run through `uvx`."""
    return CommandRuntimeInstaller(registry_type="pypi", executable="uvx")


def npx_installer() -> CommandRuntimeInstaller:
    """npm packages, run through `npx`.

    `-y` because the alternative is `npx` pausing on a TTY prompt to confirm a
    download -- against a subprocess with no terminal, that is a hang rather
    than a question.
    """
    return CommandRuntimeInstaller(registry_type="npm", executable="npx", prefix_args=("-y",))


def runtime_availability(installers: list[IPackageInstaller]) -> RuntimeAvailability:
    """What the resolver may pick from, asked of the installers that would run it.

    Bootstrap used to hardcode every field to `False`, which made
    `PackageResolver` reject every package and `hangar_load` fail on every call
    (#958). It is derived here instead, so "available" means an installer is
    registered AND its runtime is on PATH -- the two conditions the load path
    actually needs, which used to be tracked in two places that could disagree.

    `binary` (mcpb) comes out False rather than the dataclass's True default:
    there is no mcpb installer, so advertising it available would resolve a
    package that the very next line has no installer for.
    """
    ready = {i.registry_type for i in installers if i.is_runtime_available()}
    availability = RuntimeAvailability(
        pypi="pypi" in ready,
        npm="npm" in ready,
        oci="oci" in ready,
        binary="mcpb" in ready,
    )
    logger.info(
        "hot_load_runtime_availability",
        pypi=availability.pypi,
        npm=availability.npm,
        oci=availability.oci,
        binary=availability.binary,
    )
    return availability
