"""测试包入口。

在任何测试模块被 import 之前设置 VPS_SERVER_TESTING=1，让 config.py 切换到
独立的测试 DB 文件（db/vps_server_test.db），避免污染 dev 数据。

必须在 import config / db / services 等模块**之前**生效——这就是为什么放在
__init__.py 里：unittest discover 加载 test 包时这个文件先跑。
"""

import os

os.environ.setdefault("VPS_SERVER_TESTING", "1")
