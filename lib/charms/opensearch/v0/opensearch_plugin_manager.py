# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implements the plugin manager class.

This module manages each plugin's lifecycle. It is responsible to install, configure and
upgrade of each of the plugins.

This class is instantiated at the operator level and is called at every relevant event:
config-changed, upgrade, s3-credentials-changed, etc.
"""

import logging
from typing import Any, Dict, List, Optional

from charms.opensearch.v0.opensearch_backups import OpenSearchBackupPlugin
from charms.opensearch.v0.opensearch_exceptions import OpenSearchCmdError
from charms.opensearch.v0.opensearch_health import HealthColors
from charms.opensearch.v0.opensearch_keystore import OpenSearchKeystore
from charms.opensearch.v0.opensearch_plugins import (
    OpenSearchKnn,
    OpenSearchPlugin,
    OpenSearchPluginConfig,
    OpenSearchPluginError,
    OpenSearchPluginInstallError,
    OpenSearchPluginMissingConfigError,
    OpenSearchPluginMissingDepsError,
    OpenSearchPluginRelationClusterNotReadyError,
    OpenSearchPluginRemoveError,
    PluginState,
)

# The unique Charmhub library identifier, never change it
LIBID = "da838485175f47dbbbb83d76c07cab4c"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


logger = logging.getLogger(__name__)


ConfigExposedPlugins = {
    "opensearch-knn": {
        "class": OpenSearchKnn,
        "config": "plugin_opensearch_knn",
        "relation": None,
    },
    "repository-s3": {
        "class": OpenSearchBackupPlugin,
        "config": None,
        "relation": "s3-credentials",
    },
}


class OpenSearchPluginManager:
    """Manages plugins."""

    def __init__(self, charm):
        """Creates the plugin manager object based on the charm and home_path.

        Stores the home path and, optionally, plugins path can also be passed if it is
        not available in {home_path}/plugins.
        """
        self._charm = charm
        self._opensearch = charm.opensearch
        self._opensearch_config = charm.opensearch_config
        self._charm_config = self._charm.model.config
        self._plugins_path = self._opensearch.paths.plugins
        self._keystore = OpenSearchKeystore(self._charm)

    @property
    def plugins(self) -> List[OpenSearchPlugin]:
        """Returns List of installed plugins."""
        plugins_list = []
        for plugin_data in ConfigExposedPlugins.values():
            new_plugin = plugin_data["class"](
                self._plugins_path, extra_config=self._extra_conf(plugin_data)
            )
            plugins_list.append(new_plugin)
        return plugins_list

    def get_plugin(self, plugin_class: OpenSearchPlugin) -> OpenSearchPlugin:
        """Returns a given plugin based on its class."""
        for plugin in self.plugins:
            if isinstance(plugin, plugin_class):
                return plugin
        raise KeyError(f"Plugin manager did not find plugin: {plugin_class}")

    def get_plugin_status(self, plugin_class: OpenSearchPlugin) -> OpenSearchPlugin:
        """Returns a given plugin based on its class."""
        for plugin in self.plugins:
            if isinstance(plugin, plugin_class):
                return self.status(plugin)
        raise KeyError(f"Plugin manager did not find plugin: {plugin_class}")

    def _extra_conf(self, plugin_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Returns the config from the relation data of the target plugin if applies."""
        relation_name = plugin_data.get("relation")
        relation = self._charm.model.get_relation(relation_name) if relation_name else None
        if not relation:
            return None
        app = self._charm.model.get_relation(relation_name).app
        return relation.data[app]

    def run(self) -> bool:
        """Runs a check on each plugin: install, execute config changes or remove.

        This method should be called at config-changed event. Returns if needed restart.
        """
        if not self._charm.opensearch.is_started() and (
            self._charm.health.apply() != HealthColors.GREEN
            or self._charm.health.apply() != HealthColors.YELLOW
        ):
            # If the health is not green, then raise a cluster-not-ready error
            # The classes above should then defer their own events in waiting.
            # Defer is important as next steps to configure plugins will involve
            # calls to the APIs of the cluster.
            raise OpenSearchPluginRelationClusterNotReadyError()

        restart_needed = False
        for plugin in self.plugins:
            restart_needed = any(
                [
                    self._install_if_needed(plugin),
                    self._configure_if_needed(plugin),
                    self._disable_if_needed(plugin),
                    restart_needed,
                ]
            )
        return restart_needed

    def _install_if_needed(self, plugin: OpenSearchPlugin) -> bool:
        """Installs all the plugins enabled via the config/relation.

        Check if plugin in status: PluginState.MISSING and config/relation is set.
        Returns True if the plugin was installed.
        """
        installed_plugins = self._installed_plugins()

        # Add the plugin
        try:
            if self.status(plugin) != PluginState.MISSING or not self._user_requested_to_enable(
                plugin
            ):
                # Nothing to do here
                return False

            # Check for dependencies
            missing_deps = [dep for dep in plugin.dependencies if dep not in installed_plugins]
            if missing_deps:
                raise OpenSearchPluginMissingDepsError(
                    f"Failed to install {plugin.name}, missing dependencies: {missing_deps}"
                )

            self._opensearch.run_bin("opensearch-plugin", f"install --batch {plugin.name}")
        except KeyError as e:
            raise OpenSearchPluginMissingConfigError(e)
        except OpenSearchCmdError as e:
            if "already exists" in str(e):
                logger.info(f"Plugin {plugin.name} already installed, continuing...")
                # Nothing installed, as plugin already exists
                return False
            raise OpenSearchPluginInstallError(f"Failed to install plugin {plugin.name}: {e}")
        # Install successful
        return True

    def _configure_if_needed(self, plugin: OpenSearchPlugin) -> bool:
        """Gathers all the configuration changes needed and applies them."""
        try:
            if (
                not self._user_requested_to_enable(plugin)
                or self.status(plugin) != PluginState.INSTALLED
            ):
                # Leave this method if either user did not request to enable this plugin
                # or plugin has been already enabled.
                return False
            return self._apply_config(plugin.config())
        except KeyError as e:
            raise OpenSearchPluginMissingConfigError(e)

    def _disable_if_needed(self, plugin: OpenSearchPlugin) -> bool:
        """If disabled, removes plugin configuration or sets it to other values."""
        try:
            if self._user_requested_to_enable(plugin) or self.status(plugin) not in [
                PluginState.ENABLED,
                PluginState.WAITING_FOR_UPGRADE,
            ]:
                # Only considering "INSTALLED" or "WAITING FOR UPGRADE" status as it
                # represents a plugin that has been installed but either not yet configured
                # or user explicitly disabled.
                return False
            return self._apply_config(plugin.disable())
        except KeyError as e:
            raise OpenSearchPluginMissingConfigError(e)

    def _apply_config(self, config: OpenSearchPluginConfig) -> bool:
        """Runs the configuration changes as passed via OpenSearchPluginConfig.

        For each: configuration and secret
        1) Remove the entries to be deleted
        2) Add entries, if available

        For example:
            KNN needs to:
            1) Remove from configuration: {"knn.plugin.enabled": "True"}
            2) Add to configuration: {"knn.plugin.enabled": "False"}

        Returns True if a configuration change was performed.
        """
        self._keystore.delete(config.secret_entries_to_del)
        self._keystore.add(config.secret_entries_to_add)
        # Add and remove configuration if applies
        if config.config_entries_to_del:
            self._opensearch_config.delete_plugin(config.config_entries_to_del)

        if config.config_entries_to_add:
            self._opensearch_config.add_plugin(config.config_entries_to_add)

        # Return True if some configuration entries changed
        return True if config.config_entries_to_add or config.config_entries_to_del else False

    def status(self, plugin: OpenSearchPlugin) -> PluginState:
        """Returns the status for a given plugin."""
        if not self._is_installed(plugin):
            return PluginState.MISSING
        if not self._is_enabled(plugin):
            return PluginState.INSTALLED
        if self._needs_upgrade(plugin):
            return PluginState.WAITING_FOR_UPGRADE
        return PluginState.ENABLED

    def _is_installed(self, plugin: OpenSearchPlugin) -> bool:
        """Returns true if plugin is installed."""
        return plugin.name in self._installed_plugins()

    def _user_requested_to_enable(self, plugin: OpenSearchPlugin) -> bool:
        """Returns True if user requested plugin to be enabled."""
        plugin_data = ConfigExposedPlugins[plugin.name]
        if not self._charm.config.get(
            plugin_data["config"], False
        ) and not self._is_plugin_relation_set(plugin_data["relation"]):
            # User asked to disable this plugin
            return False
        return True

    def _is_enabled(self, plugin: OpenSearchPlugin) -> bool:
        """Returns true if plugin is enabled."""
        # If not requested to be disabled, check if options are configured or not
        try:
            plugin_conf = plugin.config().config_entries_to_add
            stored_plugin_conf = self._opensearch_config.get_plugin(plugin_conf)
            return plugin_conf == stored_plugin_conf
        except (KeyError, OpenSearchPluginError) as e:
            logger.warning(f"_is_enabled: error with {e}")
            return False

    def _needs_upgrade(self, plugin: OpenSearchPlugin) -> bool:
        """Returns true if plugin needs upgrade."""
        version = self._opensearch.version.split(".")
        # Some plugins are formatted as "x.y.z.a", which is different than opensearch's
        plugin_version = plugin.version.split(".")
        num_points = min(len(plugin_version), len(version))
        return ".".join(version[:num_points]) != ".".join(plugin_version[:num_points])

    def _is_plugin_relation_set(self, relation_name: str) -> bool:
        """Returns True if a relation is expected and it is set."""
        if not relation_name:
            return False
        relation = self._charm.model.get_relation(relation_name)
        if not relation:
            return False
        return len(relation.units) > 0

    def _remove_plugin(self, name: str) -> None:
        """Remove a plugin without restarting the node."""
        try:
            self._opensearch.run_bin("opensearch-plugin", f"remove {name}")
        except OpenSearchCmdError as e:
            if "not found" in str(e):
                logger.info(f"Plugin {name} to be deleted, not found. Continuing...")
                return
            raise OpenSearchPluginRemoveError(f"Failed to remove plugin {name}: {e}")

    def _installed_plugins(self) -> List[str]:
        """List plugins."""
        try:
            return self._opensearch.run_bin("opensearch-plugin", "list").split("\n")
        except OpenSearchCmdError as e:
            raise OpenSearchPluginError("Failed to list plugins: " + str(e))
