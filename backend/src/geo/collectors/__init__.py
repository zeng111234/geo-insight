"""采集器包的显式注册点。

新增一个采集器时，只在这里加一行；不做目录扫描，原因见 registry 的说明。

注册顺序不代表优先级：``registry.for_model`` 按能力筛，并且 api 优先于 offline。
这里从上到下的顺序只是「读起来顺」。
"""

from geo.collectors.csv_bank import CsvBankCollector
from geo.collectors.deepseek import DeepSeekCollector
from geo.collectors.doubao import DoubaoCollector
from geo.collectors.registry import register

# 真实采集器只是注册，不会因为没配 key 就报错。
# available() 在没 key 时返回 False，CLI 的 --list 会把它们显示成「未配置」。
deepseek = register(DeepSeekCollector())
doubao = register(DoubaoCollector())
csv_bank = register(CsvBankCollector())

__all__ = ["csv_bank", "deepseek", "doubao"]
