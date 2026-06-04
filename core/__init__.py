"""core 基础设施包：通用工具（加密 / SSH / 防火墙 / 连通性 ...）。

任何领域（xray / ip / proxy / VPS 业务）都通过这里拿基础能力。
"""

# 加密：业务层一般不直接 import，调用集中在 db.models.VPSRecord 内部
# 想用的话：`from core.security import encrypt_password, decrypt_password`
