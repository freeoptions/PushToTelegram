import unittest

from app import (
    APP_NAME,
    APP_USER_MODEL_ID,
    APP_WINDOW_TITLE,
    TRAY_TOOLTIP,
    BiliPulseWindow,
    build_export_file_name,
)


class DesktopIdentityTests(unittest.TestCase):
    def test_desktop_identity_uses_current_product_name(self) -> None:
        self.assertEqual(APP_NAME, "PushToTelegram")
        self.assertEqual(APP_WINDOW_TITLE, APP_NAME)
        self.assertEqual(APP_USER_MODEL_ID, "PushToTelegram.Desktop")
        self.assertTrue(TRAY_TOOLTIP.startswith(f"{APP_NAME} - "))
        self.assertTrue(build_export_file_name().startswith("PushToTelegram_exportConfig_"))

    def test_window_minimize_keeps_native_windows_behavior(self) -> None:
        self.assertNotIn("changeEvent", BiliPulseWindow.__dict__)


if __name__ == "__main__":
    unittest.main()
