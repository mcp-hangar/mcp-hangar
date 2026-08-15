"""Installers for the two registries whose runtime fetches and runs in one step."""

import shutil

from ...application.services.package_resolver import RuntimeAvailability
from ...domain.contracts.installer import InstalledPackage, IPackageInstaller
from ...domain.contracts.registry import PackageInfo
from ...domain.exceptions import InstallationError
from ...domain.value_objects import McpServerMode
from ...logging_config import get_logger

logger = get_logger(__name__)


class CommandRuntimeInstaller:
    """A registry whose runtime downloads and runs a package in one command.

    `uvx` and `npx` both take a package specifier and execute it, caching as a
    side effect. There is nothing to install ahead of time and nothing to
    remove afterwards, so `install()` resolves the command and `uninstall()` is
    a no-op -- honestly, rather than by deleting a directory this class never
    created.
    """

    def __init__(self, registry_type: str, executable: str, prefix_args: tuple[str, ...] = ()) -> None:
        self._registry_type = registry_type
        self._executable = executable
        self._prefix_args = prefix_args

    @property
    def registry_type(self) -> str:
        return self._registry_type

    @property
    def executable(self) -> str:
        """The binary this installer needs on PATH."""
        return self._executable

    def supports(self, registry_type: str) -> bool:
        return registry_type == self._registry_type

    def specifier(self, package: PackageInfo) -> str:
        """`name` or `name@version` -- the form both runtimes accept."""
        if package.version:
            return f"{package.identifier}@{package.version}"
        return package.identifier

    async def install(self, package: PackageInfo) -> InstalledPackage:
        if not self.supports(package.registry_type):
            raise InstallationError(f"{self._executable} installer cannot handle a {package.registry_type!r} package")
        # Re-checked here, not only at bootstrap: availability is read once when
        # the resolver is built, and a gateway that has been up for a week may
        # be running on a host where the binary was removed since. Failing here
        # names the missing runtime; failing in `ensure_ready()` names a
        # subprocess that would not start.
        if not self.is_runtime_available():
            raise InstallationError(f"{self._executable} is not on PATH, so {package.identifier} cannot be run")

        command = [self._executable, *self._prefix_args, self.specifier(package)]
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


class UvxInstaller(CommandRuntimeInstaller):
    """PyPI packages, run through `uvx`."""

    def __init__(self) -> None:
        super().__init__(registry_type="pypi", executable="uvx")


class NpxInstaller(CommandRuntimeInstaller):
    """npm packages, run through `npx`.

    `-y` because the alternative is `npx` pausing on a TTY prompt to confirm a
    download -- against a subprocess with no terminal, that is a hang rather
    than a question.
    """

    def __init__(self) -> None:
        super().__init__(registry_type="npm", executable="npx", prefix_args=("-y",))


def runtime_availability(installers: list[IPackageInstaller]) -> RuntimeAvailability:
    """What the resolver may pick from, asked of the installers that would run it.

    Bootstrap used to hardcode every field to `False`, which made
    `PackageResolver` reject every package and `hangar_load` fail on every call
    (#958). It is derived here instead, so "available" means an installer is
    registered AND its runtime is on PATH -- the two conditions the load path
    actually needs, which used to be tracked in two places that could disagree.

    `binary` (mcpb) defaults to False rather than the dataclass's True: there is
    no mcpb installer, so advertising it available would resolve a package that
    the very next line has no installer for.
    """
    by_type = {i.registry_type: i for i in installers}

    def ready(registry_type: str) -> bool:
        installer = by_type.get(registry_type)
        return installer is not None and installer.is_runtime_available()

    availability = RuntimeAvailability(
        pypi=ready("pypi"),
        npm=ready("npm"),
        oci=ready("oci"),
        binary=ready("mcpb"),
    )
    logger.info(
        "hot_load_runtime_availability",
        pypi=availability.pypi,
        npm=availability.npm,
        oci=availability.oci,
        binary=availability.binary,
    )
    return availability
