import os

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    del early_config, parser, args
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.test"
