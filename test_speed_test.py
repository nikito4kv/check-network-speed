"""Автотесты для speed_test.py. Запуск: python -m unittest -v"""

import http.server
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from speed_test import (
    Measurement,
    download,
    summarize,
    to_megabits_per_second,
    to_megabytes,
)

SCRIPT = Path(__file__).with_name("speed_test.py")
FILE_SIZE = 300_000  # больше куска в 64 КБ, чтобы проверить чтение по частям
SMALL_FILE_SIZE = 1000
DELAY_SECONDS = 0.3
MIN_DELAY_SECONDS = DELAY_SECONDS * 0.9  # запас на неточность системного таймера


class FakeFileHandler(http.server.BaseHTTPRequestHandler):
    """Локальный сервер для тестов: отдаёт файлы с известным размером и задержками."""

    def do_GET(self):
        if self.path == "/file":
            self.send_file(FILE_SIZE)
        elif self.path == "/slow-start":
            time.sleep(DELAY_SECONDS)  # сервер долго не начинает отвечать
            self.send_file(SMALL_FILE_SIZE)
        elif self.path == "/slow-body":
            self.send_file(SMALL_FILE_SIZE, pause_in_middle=True)
        else:
            self.send_error(404)

    def send_file(self, size, pause_in_middle=False):
        body = b"x" * size
        half = size // 2

        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        self.wfile.write(body[:half])
        if pause_in_middle:
            time.sleep(DELAY_SECONDS)  # файл передаётся медленно
        self.wfile.write(body[half:])

    def log_message(self, *args):
        pass  # не засоряем вывод тестов логами сервера


class LocalServerTestCase(unittest.TestCase):
    """Поднимает локальный сервер на время тестов, чтобы не зависеть от интернета."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeFileHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()


class ConversionTests(unittest.TestCase):
    def test_megabytes(self):
        self.assertEqual(to_megabytes(2_500_000), 2.5)

    def test_megabits_per_second(self):
        # 125 000 байт = 1 000 000 бит, за 1 секунду это 1 Мбит/с
        self.assertEqual(to_megabits_per_second(125_000, 1), 1.0)
        self.assertEqual(to_megabits_per_second(1_000_000, 2), 4.0)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        # Время запросов: 1.5, 1.5 и 3.0 с. Последний запрос долго ждал ответа сервера.
        self.measurements = [
            Measurement(wait_seconds=0.5, download_seconds=1.0, size_bytes=1_000_000),
            Measurement(wait_seconds=0.5, download_seconds=1.0, size_bytes=1_000_000),
            Measurement(wait_seconds=2.0, download_seconds=1.0, size_bytes=1_000_000),
        ]
        self.summary = summarize(self.measurements)

    def test_request_time_is_wait_plus_download(self):
        self.assertEqual(self.measurements[2].total_seconds, 3.0)

    def test_total_bytes(self):
        self.assertEqual(self.summary.total_bytes, 3_000_000)

    def test_mean_and_median(self):
        # Один медленный запрос тянет среднее вверх, а медиану нет
        self.assertAlmostEqual(self.summary.mean_request_seconds, 2.0)
        self.assertAlmostEqual(self.summary.median_request_seconds, 1.5)

    def test_mean_wait(self):
        self.assertAlmostEqual(self.summary.mean_wait_seconds, 1.0)

    def test_speed_ignores_wait_time(self):
        # 3 МБ = 24 Мбит за 3 с скачивания = 8 Мбит/с.
        # Если бы ожидание попало в скорость, вышло бы 24 / 6 = 4 Мбит/с.
        self.assertAlmostEqual(self.summary.speed_mbps, 8.0)


class DownloadTests(LocalServerTestCase):
    def test_counts_all_bytes(self):
        measurement = download(f"{self.base_url}/file")
        self.assertEqual(measurement.size_bytes, FILE_SIZE)

    def test_slow_server_response_counts_as_wait(self):
        measurement = download(f"{self.base_url}/slow-start")
        self.assertGreater(measurement.wait_seconds, MIN_DELAY_SECONDS)
        self.assertLess(measurement.download_seconds, MIN_DELAY_SECONDS)

    def test_slow_transfer_counts_as_download(self):
        measurement = download(f"{self.base_url}/slow-body")
        self.assertGreater(measurement.download_seconds, MIN_DELAY_SECONDS)
        self.assertLess(measurement.wait_seconds, MIN_DELAY_SECONDS)

    def test_server_error_raises(self):
        with self.assertRaises(OSError):
            download(f"{self.base_url}/missing")


class ScriptTests(LocalServerTestCase):
    """Запускает скрипт целиком, как это делает человек из консоли."""

    def run_script(self, url):
        return subprocess.run(
            [sys.executable, str(SCRIPT), url],
            capture_output=True,
            encoding="utf-8",
            timeout=60,
        )

    def test_makes_ten_requests_and_prints_summary(self):
        result = self.run_script(f"{self.base_url}/file")

        self.assertEqual(result.returncode, 0)
        request_lines = [line for line in result.stdout.splitlines() if line.startswith("Запрос ")]
        self.assertEqual(len(request_lines), 10)
        self.assertIn("Скачано всего:            3.00 МБ", result.stdout)  # 10 × 300 000 байт
        self.assertIn("Скорость скачивания:", result.stdout)

    def test_server_error_stops_with_message(self):
        result = self.run_script(f"{self.base_url}/missing")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Ошибка на запросе 1: HTTP Error 404", result.stderr)

    def test_invalid_url_stops_with_message(self):
        result = self.run_script("not-a-url")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Ошибка на запросе 1: unknown url type", result.stderr)


if __name__ == "__main__":
    unittest.main()
