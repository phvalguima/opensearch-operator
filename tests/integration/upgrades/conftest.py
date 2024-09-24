#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from ..ha.continuous_writes import ContinuousWrites, ReplicationMode
from ..helpers import APP_NAME
from .helpers import app_name

logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
async def c_writes(ops_test: OpsTest):
    """Creates instance of the ContinuousWrites."""
    app = (await app_name(ops_test)) or APP_NAME
    return ContinuousWrites(ops_test, app)


@pytest.fixture(scope="function")
async def c_writes_runner(ops_test: OpsTest, c_writes: ContinuousWrites):
    """Starts continuous write operations and clears writes at the end of the test."""
    await c_writes.start()
    yield
    await c_writes.clear()
    logger.info("\n\n\n\nThe writes have been cleared.\n\n\n\n")


@pytest.fixture(scope="function")
async def c_balanced_writes_runner(ops_test: OpsTest, c_writes: ContinuousWrites):
    """Same as previous runner, but starts continuous writes on cluster wide replicated index."""
    await c_writes.start(repl_on_all_nodes=ReplicationMode.WITH_AT_LEAST_1_REPL)
    yield
    await c_writes.clear()
    logger.info("\n\n\n\nThe writes have been cleared.\n\n\n\n")
