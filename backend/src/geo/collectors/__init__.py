"""采集器包的显式注册点。

新增一个采集器时，只在这里加一行；不做目录扫描，原因见 registry 的说明。
"""

from geo.collectors.csv_bank import CsvBankCollector
from geo.collectors.registry import register

csv_bank = register(CsvBankCollector())

__all__ = ["csv_bank"]
