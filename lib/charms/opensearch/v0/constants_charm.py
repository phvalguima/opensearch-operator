# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""In this file we declare the constants and enums used by the charm."""

# The unique Charmhub library identifier, never change it
LIBID = "a8e3e482b22f4552ad6211ea77b46f7b"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


# Blocked statuses
WaitingToStart = "Waiting for OpenSearch to start..."
InstallError = "Could not install OpenSearch."
CertsExpirationError = "The certificates: {} need to be refreshed."
TLSNotFullyConfigured = "Waiting for TLS to be fully configured..."
TLSRelationBrokenError = (
    "Relation broken with the TLS Operator while TLS not fully configured. Stopping OpenSearch."
)

# Maintenance statuses
InstallProgress = "Installing OpenSearch..."
SecurityIndexInitProgress = "Initializing the security index..."
AdminUserInitProgress = "Configuring admin user..."