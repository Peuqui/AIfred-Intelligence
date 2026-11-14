from aifred.lib import config


def test_data_dir_is_not_checkout_data() -> None:
    assert config.DATA_DIR != config.PROJECT_ROOT / "data"
