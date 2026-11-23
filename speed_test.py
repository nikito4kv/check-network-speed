"""Замер скорости скачивания: 10 последовательных запросов к одному адресу."""

import argparse
import statistics
import sys
import time
import urllib.request
from typing import NamedTuple

REQUESTS_COUNT = 10
TIMEOUT_SECONDS = 30
CHUNK_SIZE = 64 * 1024  # читаем ответ кусками по 64 КБ
USER_AGENT = "check-network-speed/1.0"  # некоторые сайты отклоняют запросы без него

BITS_IN_BYTE = 8
BYTES_IN_MEGABYTE = 1_000_000
BITS_IN_MEGABIT = 1_000_000


class Measurement(NamedTuple):
    """Результат одного запроса."""

    wait_seconds: float  # от отправки запроса до получения заголовков ответа
    download_seconds: float  # скачивание тела ответа
    size_bytes: int

    @property
    def total_seconds(self):
        return self.wait_seconds + self.download_seconds


class Summary(NamedTuple):
    """Итоги по всем запросам."""

    total_bytes: int
    mean_request_seconds: float
    median_request_seconds: float
    mean_wait_seconds: float
    speed_mbps: float  # мегабиты в секунду


def download(url):
    """Скачивает ответ целиком и возвращает Measurement."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        # urlopen возвращается, когда соединение установлено и пришли заголовки ответа
        response_started = time.perf_counter()
        received_bytes = 0
        while chunk := response.read(CHUNK_SIZE):
            received_bytes += len(chunk)
    finished = time.perf_counter()

    return Measurement(
        wait_seconds=response_started - start,
        download_seconds=finished - response_started,
        size_bytes=received_bytes,
    )


def to_megabytes(size_bytes):
    return size_bytes / BYTES_IN_MEGABYTE


def to_megabits_per_second(size_bytes, seconds):
    return size_bytes * BITS_IN_BYTE / BITS_IN_MEGABIT / seconds


def summarize(measurements):
    """Считает итоги по списку замеров."""
    request_times = [m.total_seconds for m in measurements]
    wait_times = [m.wait_seconds for m in measurements]
    total_bytes = sum(m.size_bytes for m in measurements)
    total_download_seconds = sum(m.download_seconds for m in measurements)

    return Summary(
        total_bytes=total_bytes,
        mean_request_seconds=statistics.mean(request_times),
        median_request_seconds=statistics.median(request_times),
        mean_wait_seconds=statistics.mean(wait_times),
        # Скорость считаем только по времени скачивания, без подключения к серверу
        speed_mbps=to_megabits_per_second(total_bytes, total_download_seconds),
    )


def print_summary(summary):
    print()
    print(f"Скачано всего:            {to_megabytes(summary.total_bytes):.2f} МБ")
    print(f"Среднее время запроса:    {summary.mean_request_seconds:.2f} с")
    print(f"Медиана времени запроса:  {summary.median_request_seconds:.2f} с")
    print(f"Среднее ожидание ответа:  {summary.mean_wait_seconds:.2f} с")
    print(f"Скорость скачивания:      {summary.speed_mbps:.2f} Мбит/с")


def enable_utf8_output():
    """Настраивает вывод так, чтобы он одинаково работал в любой консоли.

    UTF-8 — иначе в Windows русский текст ломается в Git Bash и при выводе в файл.
    Построчный вывод — иначе в Git Bash строки появляются все разом в конце замера.
    """
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(
        description=f"Делает {REQUESTS_COUNT} запросов к адресу и считает скорость скачивания."
    )
    parser.add_argument("url", help="адрес файла, например https://example.com/big-image.jpg")
    return parser.parse_args()


def main():
    enable_utf8_output()
    url = parse_args().url
    print(f"Адрес: {url}")
    print(f"Запросов: {REQUESTS_COUNT}, по очереди\n")

    measurements = []
    for number in range(1, REQUESTS_COUNT + 1):
        try:
            measurement = download(url)
        except (OSError, ValueError) as error:
            # OSError покрывает сетевые ошибки, таймауты и ответы вроде 404,
            # ValueError — адрес, который вообще не похож на ссылку.
            print(f"Ошибка на запросе {number}: {error}", file=sys.stderr)
            return 1

        measurements.append(measurement)
        print(
            f"Запрос {number:>2}: {measurement.total_seconds:.2f} с "
            f"(ожидание ответа {measurement.wait_seconds:.2f} с), "
            f"{to_megabytes(measurement.size_bytes):.2f} МБ"
        )

    print_summary(summarize(measurements))
    return 0


if __name__ == "__main__":
    sys.exit(main())
